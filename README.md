# NovaMart Intelligence Dashboard

A full-stack analytics dashboard that executes your NovaMart notebook on the server, scrapes all computed outputs, and renders a live executive dashboard — zero manual number entry.

**Supported notebooks:** `NovaMart_v3 (1).ipynb`, `NovaMart_Final.ipynb`, and any NovaMart-compatible `.ipynb` file.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+ · FastAPI |
| Notebook execution | papermill · nbformat |
| Output parsing | Custom regex extractors (`extractor.py`) |
| Frontend | Single HTML file · pure CSS · vanilla JS |
| Communication | `/run` JSON endpoint · SSE progress stream |

---

## Project Structure

```
novamart_dashboard/
├── main.py              # FastAPI app (routes + SSE)
├── executor.py          # Notebook runner + Colab-cell patcher
├── extractor.py         # Parses cell outputs into structured JSON
├── static/
│   └── dashboard.html   # Single-file frontend
└── requirements.txt
```

---

## Running

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
uvicorn main:app --reload --port 8000

# 3. Open the dashboard
open http://localhost:8000
```

Then:
1. Upload **NovaMart_v3 (1).ipynb** (or any compatible notebook)
2. Upload **Project_2.xlsx** (or your data file)
3. Click **Run Analysis**
4. Wait ~60–90 seconds for notebook execution
5. Dashboard renders automatically
6. Use **↩ New Analysis** in the header to run a new file without refreshing

---

## How It Works

1. Both files are uploaded via a multipart POST to `/run`.
2. The backend reads the notebook, finds any `google.colab` upload cell, and replaces it with `excel_file = "<absolute path to xlsx>"`.
3. The patched notebook is executed via **papermill** (kernel: `python3`, timeout: 300 s).
4. All `stdout` / `execute_result` / `display_data` outputs are concatenated into one text blob.
5. Targeted regex extractors parse each analytics block (KPIs, channels, segments, campaign clusters, discount effectiveness, lead models, Pareto, repeat buyer models).
6. The structured JSON is returned to the browser, which renders every section client-side with count-up animations.

## Error Handling

- If the notebook fails mid-execution, a **Partial Results** banner is shown and any data successfully extracted before the failure is still rendered.
- If a specific section is missing from the notebook output, that card shows *"Data unavailable — check notebook output format"* without crashing the dashboard.