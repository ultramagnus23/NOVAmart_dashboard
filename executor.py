"""
executor.py — Runs a NovaMart .ipynb notebook via papermill,
patching out Google Colab-specific cells, and returns structured
analytics data extracted from the executed notebook.
"""

import os
import tempfile
import logging

import nbformat
import papermill as pm

from extractor import extract_all

logger = logging.getLogger(__name__)

# Default execution timeout in seconds.  Override with the environment
# variable NOVAMART_EXEC_TIMEOUT if you need a different value.
NOTEBOOK_EXECUTION_TIMEOUT = int(os.environ.get("NOVAMART_EXEC_TIMEOUT", "600"))


def _colab_stub(xlsx_path: str, xlsx_filename: str) -> str:
    """
    Return Python source that stubs out a Google Colab file-upload cell.

    The stub defines every variable name that NovaMart notebooks commonly
    reference after calling files.upload():

        excel_file          – plain string path, used by pd.read_excel(excel_file)
        file_path           – alias for excel_file (used by some v3 notebooks)
        uploaded            – dict {filename: bytes}, mimics files.upload() return value
        list(uploaded.keys())[0]    → returns xlsx_filename
        list(uploaded.values())[0]  → returns the raw bytes
        io.BytesIO(uploaded[...])   → works because io is imported directly
        BytesIO(...)                → shortcut alias also provided
        _xlsx_name          – just the filename string
    """
    abs_path = os.path.abspath(xlsx_path)
    # Use repr() so backslashes and spaces in the path are safely escaped.
    path_repr = repr(abs_path)
    name_repr = repr(xlsx_filename)
    return (
        "# ── NovaMart dashboard: Google Colab file-upload stub ──\n"
        "import io\n"
        "import io as _io\n"
        "from io import BytesIO\n"
        f"excel_file   = {path_repr}\n"
        f"file_path    = {path_repr}\n"
        f"_xlsx_name   = {name_repr}\n"
        f"_xlsx_bytes  = open({path_repr}, 'rb').read()\n"
        f"uploaded     = {{{name_repr}: _xlsx_bytes}}\n"
    )


def _is_colab_cell(source: str) -> bool:
    """Return True if the cell source contains any google.colab import."""
    return "google.colab" in source


def _patch_colab_cells(nb: nbformat.NotebookNode, xlsx_path: str) -> nbformat.NotebookNode:
    """
    Replace every cell that imports from google.colab.

    •  The *first* such cell is replaced with a comprehensive file-path stub
       that defines excel_file, uploaded, and _xlsx_bytes so all downstream
       variable references keep working.
    •  Subsequent colab cells are replaced with a comment so they are inert.
    •  All colab cells have their outputs cleared so papermill re-executes them.
    """
    xlsx_filename = os.path.basename(xlsx_path)
    first_colab = True

    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        source = cell.get("source", "")
        if not _is_colab_cell(source):
            continue

        if first_colab:
            cell["source"] = _colab_stub(xlsx_path, xlsx_filename)
            first_colab = False
        else:
            cell["source"] = "# google.colab cell removed by NovaMart dashboard"

        cell["outputs"] = []
        cell["execution_count"] = None

    return nb


def run_notebook(nb_path: str, xlsx_path: str) -> dict:
    """
    Execute the notebook at *nb_path* using papermill, injecting the
    xlsx path in place of any Google Colab upload cell.

    Returns the structured extraction dict from extractor.extract_all().
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # ------------------------------------------------------------------
        # 1. Read and patch the notebook
        # ------------------------------------------------------------------
        with open(nb_path, "r", encoding="utf-8") as fh:
            nb = nbformat.read(fh, as_version=4)

        nb = _patch_colab_cells(nb, os.path.abspath(xlsx_path))

        patched_path = os.path.join(tmpdir, "patched_notebook.ipynb")
        with open(patched_path, "w", encoding="utf-8") as fh:
            nbformat.write(nb, fh)

        output_path = os.path.join(tmpdir, "executed_notebook.ipynb")

        # ------------------------------------------------------------------
        # 2. Execute with papermill
        # ------------------------------------------------------------------
        try:
            pm.execute_notebook(
                patched_path,
                output_path,
                kernel_name="python3",
                execution_timeout=NOTEBOOK_EXECUTION_TIMEOUT,
                progress_bar=False,
                log_output=True,
            )
        except pm.exceptions.PapermillExecutionError as exc:
            logger.error("Papermill execution error: %s", exc)
            raise

        # ------------------------------------------------------------------
        # 3. Read executed notebook and extract analytics
        # ------------------------------------------------------------------
        with open(output_path, "r", encoding="utf-8") as fh:
            executed_nb = nbformat.read(fh, as_version=4)

        return extract_all(executed_nb)
