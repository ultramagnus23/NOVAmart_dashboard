"""
export_data.py — Run the NovaMart notebook once (locally) and export all
analytics outputs to static JSON files consumed by the Vercel frontend.

Usage:
    python export_data.py <notebook.ipynb> <data.xlsx> [--out-dir static/data]

The script reuses executor.py (papermill runner) and extractor.py (regex
parser) that are already part of this project.  The single output file,
static/data/analytics.json, is then committed and deployed to Vercel — no
Python runtime is ever needed on the server.

Requirements (local only, not needed on Vercel):
    pip install -r requirements.txt
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path


def _sanitize(obj):
    """Recursively replace NaN/Inf with None for JSON serialization."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def main():
    parser = argparse.ArgumentParser(
        description="Export NovaMart notebook analytics to static/data/analytics.json."
    )
    parser.add_argument("notebook", help="Path to the .ipynb notebook file")
    parser.add_argument("xlsx", help="Path to the .xlsx data file")
    parser.add_argument(
        "--out-dir",
        default="static/data",
        help="Output directory for JSON files (default: static/data)",
    )
    args = parser.parse_args()

    nb_path = os.path.abspath(args.notebook)
    xlsx_path = os.path.abspath(args.xlsx)

    if not os.path.exists(nb_path):
        print(f"Error: notebook not found: {nb_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(xlsx_path):
        print(f"Error: data file not found: {xlsx_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Notebook : {nb_path}")
    print(f"Data file: {xlsx_path}")
    print("Executing notebook via papermill…")

    from executor import run_notebook

    result = run_notebook(nb_path, xlsx_path)

    # Remove internal debug field not needed by the static frontend
    result.pop("_raw_output_preview", None)

    # Replace NaN / Inf with null so the JSON is valid everywhere
    result = _sanitize(result)

    out_path = out_dir / "analytics.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)

    populated = [k for k, v in result.items() if v not in (None, [], {})]
    empty = [k for k, v in result.items() if v in (None, [], {}) and k != "extraction_warnings"]

    print(f"\n✓ Exported → {out_path}")
    print(f"  Populated sections : {populated}")
    if empty:
        print(f"  Empty sections     : {empty}")
    warnings = result.get("extraction_warnings") or []
    if warnings:
        print(f"\n  Extraction warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    • {w}")

    print("\nDone. Commit static/data/analytics.json and deploy to Vercel.")


if __name__ == "__main__":
    main()
