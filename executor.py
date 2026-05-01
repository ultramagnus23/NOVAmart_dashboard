"""
executor.py — Runs a NovaMart .ipynb notebook via papermill,
patching out Google Colab-specific cells, and returns structured
analytics data extracted from the executed notebook.
"""

import os
import re
import tempfile
import logging

import nbformat
import papermill as pm

from extractor import extract_all

logger = logging.getLogger(__name__)


NOTEBOOK_EXECUTION_TIMEOUT = 300


def _patch_colab_cells(nb: nbformat.NotebookNode, xlsx_path: str) -> nbformat.NotebookNode:
    """
    Replace any cell that imports from google.colab with a simple
    assignment that points to the uploaded xlsx file.
    The first such cell becomes:
        excel_file = "<absolute path>"
    Subsequent colab cells are replaced with empty pass statements.
    """
    first_colab = True
    for cell in nb.cells:
        if cell.cell_type != 'code':
            continue
        source = cell.get('source', '')
        if 'google.colab' in source:
            if first_colab:
                cell['source'] = f'excel_file = r"{xlsx_path}"'
                first_colab = False
            else:
                cell['source'] = '# colab cell removed'
            # Clear any previous outputs so papermill re-executes cleanly
            cell['outputs'] = []
            cell['execution_count'] = None

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
        with open(nb_path, 'r', encoding='utf-8') as fh:
            nb = nbformat.read(fh, as_version=4)

        nb = _patch_colab_cells(nb, os.path.abspath(xlsx_path))

        patched_path = os.path.join(tmpdir, 'patched_notebook.ipynb')
        with open(patched_path, 'w', encoding='utf-8') as fh:
            nbformat.write(nb, fh)

        output_path = os.path.join(tmpdir, 'executed_notebook.ipynb')

        # ------------------------------------------------------------------
        # 2. Execute with papermill
        # ------------------------------------------------------------------
        try:
            pm.execute_notebook(
                patched_path,
                output_path,
                kernel_name='python3',
                execution_timeout=NOTEBOOK_EXECUTION_TIMEOUT,
                progress_bar=False,
                log_output=True,
            )
        except pm.exceptions.PapermillExecutionError as exc:
            logger.error("Papermill execution error: %s", exc)
            # Re-raise so the caller (main.py) can surface the error with context
            raise

        # ------------------------------------------------------------------
        # 3. Read executed notebook and extract analytics
        # ------------------------------------------------------------------
        with open(output_path, 'r', encoding='utf-8') as fh:
            executed_nb = nbformat.read(fh, as_version=4)

        return extract_all(executed_nb)
