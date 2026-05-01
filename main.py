"""
main.py — FastAPI application for the NovaMart Analytics Dashboard.

Endpoints:
  GET  /           → serves static/dashboard.html
  POST /run        → accepts .ipynb + .xlsx, executes notebook, returns JSON
  GET  /run/stream → Server-Sent Events progress stream
"""

import asyncio
import json
import logging
import os
import tempfile
import traceback
from pathlib import Path

import nbformat
import papermill as pm
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from extractor import extract_all
from executor import _patch_colab_cells

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="NovaMart Analytics Dashboard")

# Serve static files (CSS, JS assets if any)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    html_path = STATIC_DIR / "dashboard.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return FileResponse(str(html_path), media_type="text/html")


# ---------------------------------------------------------------------------
# SSE progress helper
# ---------------------------------------------------------------------------

_progress_queues: dict[str, asyncio.Queue] = {}


async def _sse_generator(run_id: str):
    """Yields SSE-formatted messages from the progress queue."""
    queue = _progress_queues.get(run_id)
    if queue is None:
        yield "data: {\"event\":\"error\",\"message\":\"Unknown run_id\"}\n\n"
        return

    while True:
        try:
            message = await asyncio.wait_for(queue.get(), timeout=120)
        except asyncio.TimeoutError:
            yield "data: {\"event\":\"timeout\"}\n\n"
            break

        yield f"data: {json.dumps(message)}\n\n"

        if message.get("event") in ("done", "error"):
            break

    _progress_queues.pop(run_id, None)


@app.get("/run/stream/{run_id}")
async def run_stream(run_id: str):
    return StreamingResponse(
        _sse_generator(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Main /run endpoint
# ---------------------------------------------------------------------------

@app.post("/run")
async def run_analysis(
    ipynb_file: UploadFile = File(..., description="NovaMart_Final.ipynb"),
    xlsx_file: UploadFile = File(..., description="Project_2.xlsx"),
):
    """
    Accepts the notebook and data file, executes the notebook via papermill,
    and returns structured analytics JSON.

    Also supports SSE progress: after POSTing, open GET /run/stream/<run_id>
    using the run_id returned in the JSON response header X-Run-Id.
    """
    import uuid
    run_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _progress_queues[run_id] = queue

    async def _push(event: str, message: str, **extra):
        await queue.put({"event": event, "message": message, **extra})

    with tempfile.TemporaryDirectory() as tmpdir:
        # ------------------------------------------------------------------
        # Save uploaded files
        # ------------------------------------------------------------------
        nb_path = os.path.join(tmpdir, ipynb_file.filename or "notebook.ipynb")
        xlsx_path = os.path.join(tmpdir, xlsx_file.filename or "data.xlsx")

        nb_bytes = await ipynb_file.read()
        xlsx_bytes = await xlsx_file.read()

        with open(nb_path, "wb") as f:
            f.write(nb_bytes)
        with open(xlsx_path, "wb") as f:
            f.write(xlsx_bytes)

        await _push("progress", "Files received. Patching notebook…", step=1)

        # ------------------------------------------------------------------
        # Read + patch
        # ------------------------------------------------------------------
        try:
            with open(nb_path, "r", encoding="utf-8") as fh:
                nb = nbformat.read(fh, as_version=4)
        except Exception as exc:
            await _push("error", f"Failed to read notebook: {exc}")
            raise HTTPException(status_code=400, detail=f"Invalid .ipynb file: {exc}")

        nb = _patch_colab_cells(nb, os.path.abspath(xlsx_path))

        patched_path = os.path.join(tmpdir, "patched_notebook.ipynb")
        output_path = os.path.join(tmpdir, "executed_notebook.ipynb")

        with open(patched_path, "w", encoding="utf-8") as fh:
            nbformat.write(nb, fh)

        await _push("progress", "Notebook patched. Starting execution…", step=2)

        # ------------------------------------------------------------------
        # Count cells for progress reporting
        # ------------------------------------------------------------------
        total_code_cells = sum(1 for c in nb.cells if c.cell_type == "code")

        # ------------------------------------------------------------------
        # Execute with papermill
        # ------------------------------------------------------------------
        partial_result = None
        exec_error = None

        try:
            # papermill is synchronous; run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()

            def _run_pm():
                pm.execute_notebook(
                    patched_path,
                    output_path,
                    kernel_name="python3",
                    execution_timeout=300,
                    progress_bar=False,
                    log_output=True,
                )

            await _push("progress", f"Executing {total_code_cells} cells…", step=3, total=total_code_cells)
            await loop.run_in_executor(None, _run_pm)
            await _push("progress", "Execution complete. Extracting results…", step=4)

        except pm.exceptions.PapermillExecutionError as exc:
            exec_error = {
                "type": "PapermillExecutionError",
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            await _push("error", f"Notebook execution failed: {exc}")
            logger.error("Papermill error: %s", exc)

            # Try to extract partial results from failed notebook
            if os.path.exists(output_path):
                try:
                    with open(output_path, "r", encoding="utf-8") as fh:
                        partial_nb = nbformat.read(fh, as_version=4)
                    partial_result = extract_all(partial_nb)
                except Exception:
                    pass

        except Exception as exc:
            await _push("error", f"Unexpected error: {exc}")
            raise HTTPException(status_code=500, detail=f"Execution failed: {exc}")

        # ------------------------------------------------------------------
        # Extract results from executed notebook
        # ------------------------------------------------------------------
        result = {}
        extraction_error = None

        if exec_error is None:
            try:
                with open(output_path, "r", encoding="utf-8") as fh:
                    executed_nb = nbformat.read(fh, as_version=4)
                result = extract_all(executed_nb)
            except Exception as exc:
                extraction_error = str(exc)
                await _push("error", f"Extraction failed: {exc}")
        elif partial_result:
            result = partial_result

        # ------------------------------------------------------------------
        # Build response
        # ------------------------------------------------------------------
        response_data = {
            "run_id": run_id,
            "status": "partial" if exec_error else "ok",
            **result,
        }

        if exec_error:
            response_data["execution_error"] = exec_error
        if extraction_error:
            response_data["extraction_error"] = extraction_error

        await _push("done", "Analysis complete.", run_id=run_id)

        return JSONResponse(
            content=response_data,
            headers={"X-Run-Id": run_id},
        )
