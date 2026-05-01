"""
extractor.py — Parses raw cell outputs from an executed notebook into
structured JSON consumed by the frontend dashboard.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Strip ANSI escape codes and normalise whitespace."""
    ansi = re.compile(r'\x1B\[[0-9;]*[mGKHF]')
    return ansi.sub('', text)


def _collect_outputs(executed_nb) -> str:
    """Concatenate all printable text from every executed cell."""
    parts = []
    for cell in executed_nb.cells:
        for output in getattr(cell, 'outputs', []):
            otype = output.get('output_type', '')
            if otype == 'stream':
                parts.append(output.get('text', ''))
            elif otype in ('execute_result', 'display_data'):
                data = output.get('data', {})
                parts.append(data.get('text/plain', ''))
    return _clean('\n'.join(parts))


# ---------------------------------------------------------------------------
# Individual extractors
# ---------------------------------------------------------------------------

def extract_kpis(all_text: str) -> Optional[dict]:
    """
    Looks for:
        ═══ NOVAMART — EXECUTIVE KPI DASHBOARD ═══
        KPI Name   :  Value
    """
    block_match = re.search(
        r'NOVAMART[^\n]*EXECUTIVE\s+KPI\s+DASHBOARD[^\n]*\n(.*?)(?:\n\s*\n|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    if not block_match:
        return None

    block = block_match.group(1)
    kpi_map = {
        'total_leads':          r'total\s+leads?\s*[:\|]\s*([\d,]+)',
        'conversion_rate':      r'conv(?:ersion)?\s+rate?\s*[:\|]\s*([\d.]+)',
        'avg_cpa':              r'avg\.?\s+cpa\s*[:\|]\s*\$?([\d,.]+)',
        'total_revenue':        r'total\s+revenue\s*[:\|]\s*\$?([\d,.]+)',
        'roas':                 r'\broas\b\s*[:\|]\s*([\d.]+)',
        'avg_order_value':      r'avg\.?\s+order\s+value\s*[:\|]\s*\$?([\d,.]+)',
        'repeat_buyer_rate':    r'repeat\s+buyer\s+rate\s*[:\|]\s*([\d.]+)',
        'return_rate':          r'return\s+rate\s*[:\|]\s*([\d.]+)',
        'avg_days_to_convert':  r'avg\.?\s+days?\s+to\s+convert\s*[:\|]\s*([\d.]+)',
        'lead_revenue_ratio':   r'lead\s+revenue\s+ratio\s*[:\|]\s*([\d.]+)',
    }

    result = {}
    for key, pattern in kpi_map.items():
        m = re.search(pattern, block, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(',', '')
            try:
                result[key] = float(raw) if '.' in raw else int(raw)
            except ValueError:
                result[key] = raw
    return result if result else None


def extract_channel_table(all_text: str) -> Optional[list]:
    """
    Looks for KPI BREAKDOWN BY: CHANNEL block and parses tabular rows.
    """
    block_match = re.search(
        r'KPI\s+BREAKDOWN\s+BY[:\s]+CHANNEL[^\n]*\n(.*?)(?:\n{2,}|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    if not block_match:
        return None

    block = block_match.group(1)
    rows = []
    col_headers = None

    for line in block.splitlines():
        line = line.strip()
        if not line or re.match(r'^[-=|+]+$', line):
            continue

        # Detect header row
        if re.search(r'channel', line, re.IGNORECASE) and col_headers is None:
            col_headers = [h.strip().lower().replace(' ', '_') for h in re.split(r'\s{2,}|\|', line) if h.strip()]
            continue

        # Parse data rows (split on 2+ spaces or |)
        parts = [p.strip() for p in re.split(r'\s{2,}|\|', line) if p.strip()]
        if len(parts) >= 4:
            row = {}
            field_names = col_headers or [
                'channel', 'total_leads', 'conversion_rate',
                'avg_cpa', 'avg_ltv', 'repeat_rate', 'avg_days', 'avg_discount'
            ]
            for i, val in enumerate(parts):
                key = field_names[i] if i < len(field_names) else f'col_{i}'
                cleaned = val.replace('%', '').replace('$', '').replace(',', '')
                try:
                    row[key] = float(cleaned) if '.' in cleaned else (
                        int(cleaned) if cleaned.lstrip('-').isdigit() else val
                    )
                except ValueError:
                    row[key] = val
            rows.append(row)

    if not rows:
        return None

    # Sort by conversion_rate descending
    conv_key = next((k for k in rows[0] if 'conv' in k.lower()), None)
    if conv_key:
        rows.sort(key=lambda r: float(r.get(conv_key, 0) or 0), reverse=True)

    return rows


def extract_segments(all_text: str) -> Optional[list]:
    """
    Looks for CUSTOMER SEGMENT SUMMARY block.
    """
    block_match = re.search(
        r'CUSTOMER\s+SEGMENT\s+SUMMARY[^\n]*\n(.*?)(?:\n{2,}|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    if not block_match:
        return None

    block = block_match.group(1)
    rows = []
    col_headers = None

    for line in block.splitlines():
        line = line.strip()
        if not line or re.match(r'^[-=|+]+$', line):
            continue

        if re.search(r'cluster', line, re.IGNORECASE) and col_headers is None:
            col_headers = [h.strip().lower().replace(' ', '_') for h in re.split(r'\s{2,}|\|', line) if h.strip()]
            continue

        parts = [p.strip() for p in re.split(r'\s{2,}|\|', line) if p.strip()]
        if len(parts) >= 3:
            row = {}
            field_names = col_headers or [
                'cluster', 'customers', 'avg_ltv', 'avg_orders', 'avg_aov', 'label'
            ]
            for i, val in enumerate(parts):
                key = field_names[i] if i < len(field_names) else f'col_{i}'
                cleaned = val.replace('%', '').replace('$', '').replace(',', '')
                try:
                    row[key] = float(cleaned) if '.' in cleaned else (
                        int(cleaned) if cleaned.lstrip('-').isdigit() else val
                    )
                except ValueError:
                    row[key] = val
            rows.append(row)

    return rows if rows else None


def extract_campaign_clusters(all_text: str) -> Optional[list]:
    """
    Looks for CAMPAIGN CLUSTER PROFILES and RECOMMENDATIONS blocks.
    """
    block_match = re.search(
        r'CAMPAIGN\s+CLUSTER\s+(?:PROFILES|RECOMMENDATIONS)[^\n]*\n(.*?)(?:\n{2,}|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    if not block_match:
        return None

    block = block_match.group(1)
    rows = []

    for line in block.splitlines():
        line = line.strip()
        if not line or re.match(r'^[-=|+]+$', line):
            continue

        # Look for lines starting with a cluster number or label
        m = re.search(
            r'(?:cluster\s*)?(\d+|[A-Za-z ]+)\s*[|\s]+'
            r'([\d.]+)\s*[|\s]+([\d.]+)\s*[|\s]+([\d.]+)\s*[|\s]+(\d+)\s*[|\s]+(.*)',
            line, re.IGNORECASE
        )
        if m:
            rows.append({
                'cluster':         m.group(1).strip(),
                'roas':            float(m.group(2)),
                'conversion_rate': float(m.group(3)),
                'cpl':             float(m.group(4)),
                'campaign_count':  int(m.group(5)),
                'recommendation':  m.group(6).strip(),
            })

    return rows if rows else None


def extract_discount_analysis(all_text: str) -> Optional[list]:
    """
    Looks for DISCOUNT EFFECTIVENESS BY SUBGROUP block.
    """
    block_match = re.search(
        r'DISCOUNT\s+EFFECTIVENESS[^\n]*\n(.*?)(?:\n{2,}|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    if not block_match:
        return None

    block = block_match.group(1)
    rows = []
    col_headers = None

    for line in block.splitlines():
        line = line.strip()
        if not line or re.match(r'^[-=|+]+$', line):
            continue

        if re.search(r'channel', line, re.IGNORECASE) and col_headers is None:
            col_headers = [h.strip().lower().replace(' ', '_') for h in re.split(r'\s{2,}|\|', line) if h.strip()]
            continue

        parts = [p.strip() for p in re.split(r'\s{2,}|\|', line) if p.strip()]
        if len(parts) >= 3:
            row = {}
            field_names = col_headers or ['channel', 'no_discount_rate', 'discount_rate', 'uplift_pp']
            for i, val in enumerate(parts):
                key = field_names[i] if i < len(field_names) else f'col_{i}'
                cleaned = val.replace('%', '').replace('$', '').replace(',', '')
                try:
                    row[key] = float(cleaned) if '.' in cleaned else (
                        int(cleaned) if cleaned.lstrip('-').isdigit() else val
                    )
                except ValueError:
                    row[key] = val

            # Determine uplift verdict — key name varies (uplift_pp, uplift_(pp), uplift…)
            uplift_key = next(
                (k for k in row if 'uplift' in k.lower()),
                None
            )
            try:
                uplift_val = float(row[uplift_key]) if uplift_key else 0.0
            except (TypeError, ValueError):
                uplift_val = 0.0
            row['discount_verdict'] = (
                'effective' if uplift_val > 5 else
                'ineffective' if uplift_val < 2 else
                'marginal'
            )
            rows.append(row)

    return rows if rows else None


def extract_models(all_text: str) -> Optional[dict]:
    """
    Looks for MODEL COMPARISON — LEAD CONVERSION PREDICTION block.
    """
    block_match = re.search(
        r'MODEL\s+COMPARISON[^\n]*LEAD\s+CONVERSION[^\n]*\n(.*?)(?:\n{2,}|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    if not block_match:
        return None

    block = block_match.group(1)
    models = []
    col_headers = None

    for line in block.splitlines():
        line = line.strip()
        if not line or re.match(r'^[-=|+]+$', line):
            continue

        if re.search(r'model', line, re.IGNORECASE) and col_headers is None:
            col_headers = [h.strip().lower() for h in re.split(r'\s{2,}|\|', line) if h.strip()]
            continue

        parts = [p.strip() for p in re.split(r'\s{2,}|\|', line) if p.strip()]
        if len(parts) >= 4:
            field_names = col_headers or ['model', 'accuracy', 'precision', 'recall', 'f1']
            row = {}
            for i, val in enumerate(parts):
                key = field_names[i] if i < len(field_names) else f'col_{i}'
                cleaned = val.replace('%', '').replace(',', '')
                try:
                    row[key] = float(cleaned)
                except ValueError:
                    row[key] = val
            models.append(row)

    if not models:
        return None

    # Identify best model by F1
    f1_key = next((k for k in (models[0] if models else {}) if 'f1' in k.lower()), 'f1')
    best = max(models, key=lambda m: float(m.get(f1_key, 0) or 0))

    # Parse Lead Priority Tiers
    tiers_match = re.search(
        r'(?:Lead\s+Priority\s+Tier[s]?|Priority\s+Tier)[^\n]*\n(.*?)(?:\n{2,}|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    tiers = []
    if tiers_match:
        tier_block = tiers_match.group(1)
        for line in tier_block.splitlines():
            line = line.strip()
            if not line or re.match(r'^[-=|+]+$', line):
                continue
            m = re.search(
                r'(low|medium|high)\s*[|\s]+([\d,]+)\s*[|\s]+([\d,]+)\s*[|\s]+([\d.]+)',
                line, re.IGNORECASE
            )
            if m:
                tiers.append({
                    'tier':         m.group(1).capitalize(),
                    'leads':        int(m.group(2).replace(',', '')),
                    'actual_conv':  int(m.group(3).replace(',', '')),
                    'conv_rate':    float(m.group(4)),
                })

    return {
        'models':      models,
        'best_model':  best.get('model', best.get(list(best.keys())[0], 'Unknown')),
        'best_metrics': best,
        'priority_tiers': tiers,
    }


def extract_pareto(all_text: str) -> Optional[dict]:
    """
    Looks for Pareto summary line and related metrics.
    """
    pct_match = re.search(
        r'(?:Pareto|top)\s*[:\s]*([\d.]+)\s*%\s*of\s*customers?\s+account\s+for\s+80\s*%',
        all_text, re.IGNORECASE
    )
    gini_match = re.search(r'gini\s+(?:coefficient|index)\s*[:\|]\s*([\d.]+)', all_text, re.IGNORECASE)
    bottom_match = re.search(r'bottom\s+20\s*%[^\$\d]*([\$\d,]+)', all_text, re.IGNORECASE)
    top_match = re.search(r'top\s+(?:80|20)\s*%[^\$\d]*([\$\d,]+)', all_text, re.IGNORECASE)

    if not pct_match and not gini_match:
        return None

    def _parse_money(s):
        if s is None:
            return None
        return float(s.replace('$', '').replace(',', ''))

    return {
        'pct_customers':    float(pct_match.group(1)) if pct_match else None,
        'gini_coefficient': float(gini_match.group(1)) if gini_match else None,
        'bottom_20_net':    _parse_money(bottom_match.group(1)) if bottom_match else None,
        'top_80_net':       _parse_money(top_match.group(1)) if top_match else None,
    }


def extract_repeat_buyer(all_text: str) -> Optional[dict]:
    """
    Looks for REPEAT BUYER MODEL COMPARISON block.
    """
    block_match = re.search(
        r'REPEAT\s+BUYER\s+MODEL\s+COMPARISON[^\n]*\n(.*?)(?:\n{2,}|\Z)',
        all_text, re.IGNORECASE | re.DOTALL
    )
    if not block_match:
        return None

    block = block_match.group(1)
    models = []
    col_headers = None

    for line in block.splitlines():
        line = line.strip()
        if not line or re.match(r'^[-=|+]+$', line):
            continue

        if re.search(r'model', line, re.IGNORECASE) and col_headers is None:
            col_headers = [h.strip().lower() for h in re.split(r'\s{2,}|\|', line) if h.strip()]
            continue

        parts = [p.strip() for p in re.split(r'\s{2,}|\|', line) if p.strip()]
        if len(parts) >= 4:
            field_names = col_headers or ['model', 'accuracy', 'precision', 'recall', 'f1']
            row = {}
            for i, val in enumerate(parts):
                key = field_names[i] if i < len(field_names) else f'col_{i}'
                cleaned = val.replace('%', '').replace(',', '')
                try:
                    row[key] = float(cleaned)
                except ValueError:
                    row[key] = val
            models.append(row)

    if not models:
        return None

    f1_key = next((k for k in (models[0] if models else {}) if 'f1' in k.lower()), 'f1')
    best = max(models, key=lambda m: float(m.get(f1_key, 0) or 0))

    return {
        'models':     models,
        'best_model': best.get('model', 'Unknown'),
        'best_metrics': best,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_all(executed_nb) -> dict:
    """
    Extract structured analytics data from an executed notebook.
    Returns a dict with all sections; missing sections are None.
    """
    all_text = _collect_outputs(executed_nb)
    warnings = []

    sections = {
        'kpis':              extract_kpis,
        'channels':          extract_channel_table,
        'segments':          extract_segments,
        'campaign_clusters': extract_campaign_clusters,
        'discount':          extract_discount_analysis,
        'lead_models':       extract_models,
        'pareto':            extract_pareto,
        'repeat_buyer':      extract_repeat_buyer,
    }

    result = {}
    for key, fn in sections.items():
        try:
            val = fn(all_text)
            result[key] = val
            if val is None:
                warnings.append(f"Block '{key}' not found in notebook output.")
        except Exception as exc:
            result[key] = None
            warnings.append(f"Error extracting '{key}': {exc}")

    result['extraction_warnings'] = warnings
    return result
