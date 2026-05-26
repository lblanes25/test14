"""
validation_engine.py
=====================
The flag-only validation layer. Philosophy (from the concept): DESCRIBE data
quality conditions, do not judge business completeness, and NEVER modify data.
"Detect aggressively, resolve conservatively."

Two halves:
  * validate_worksheet(ws)  - STRUCTURAL checks on a raw openpyxl worksheet,
    run at ingest BEFORE pandas flattens the workbook (merged cells, hidden
    rows/columns are invisible once pandas has read the data).
  * validate_table(df)      - DATA-QUALITY checks on the detected table
    (repeated header rows, subtotal/total rows, high blank-rate columns,
    mixed numeric/text columns).

Every check returns flags shaped as:
    {"condition": str, "location": str, "severity": "review"|"info", "detail": str}

Dependencies: pandas + numpy + openpyxl (all preinstalled in Code Interpreter).
"""

import re
import pandas as pd
import numpy as np

import cleanup_config as cfg


def _flag(condition, location, detail, severity="review"):
    return {"condition": condition, "location": location,
            "severity": severity, "detail": detail}


def _is_blank(v):
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    s = str(v).strip()
    return s == "" or s.lower() in {x.lower() for x in cfg.NULL_VARIANTS}


# ===========================================================================
# STRUCTURAL CHECKS  (openpyxl worksheet, pre-flatten)
# ===========================================================================
def validate_worksheet(ws):
    """Structural conditions only visible in the raw workbook."""
    flags = []

    # Merged cells — collapse a value into one corner; distort reads & filters.
    merged = list(ws.merged_cells.ranges)
    if merged:
        ranges = ", ".join(str(m) for m in merged[:10])
        more = "" if len(merged) <= 10 else f" (+{len(merged) - 10} more)"
        flags.append(_flag(
            "merged cells", f"sheet '{ws.title}'",
            f"{len(merged)} merged range(s): {ranges}{more}. Values live in one "
            f"corner cell; the rest read as blank.",
        ))

    # Hidden rows.
    hidden_rows = [i for i, d in ws.row_dimensions.items() if d.hidden]
    if hidden_rows:
        flags.append(_flag(
            "hidden rows", f"sheet '{ws.title}'",
            f"{len(hidden_rows)} hidden row(s): "
            f"{hidden_rows[:20]}{'...' if len(hidden_rows) > 20 else ''}. "
            f"Hidden data is easy to overlook in review.",
        ))

    # Hidden columns.
    hidden_cols = [c for c, d in ws.column_dimensions.items() if d.hidden]
    if hidden_cols:
        flags.append(_flag(
            "hidden columns", f"sheet '{ws.title}'",
            f"{len(hidden_cols)} hidden column(s): {hidden_cols}. "
            f"Hidden data is easy to overlook in review.",
        ))

    return flags


# ===========================================================================
# DATA-QUALITY CHECKS  (detected table DataFrame)
# ===========================================================================
def _repeated_header_rows(df):
    """Rows whose values duplicate the column headers (concatenated exports)."""
    flags = []
    header_norm = [str(c).strip().lower() for c in df.columns]
    hits = []
    for idx, row in df.iterrows():
        row_norm = [str(v).strip().lower() for v in row.tolist()]
        if row_norm == header_norm:
            hits.append(idx)
    if hits:
        flags.append(_flag(
            "repeated header rows", "table body",
            f"{len(hits)} row(s) repeat the header: rows {hits[:20]}. Often from "
            f"stacked exports; they will corrupt counts and type inference.",
        ))
    return flags


def _subtotal_rows(df):
    """Rows containing subtotal/total-style keywords."""
    flags = []
    kws = {k.lower() for k in cfg.SUBTOTAL_KEYWORDS}
    hits = []
    for idx, row in df.iterrows():
        for v in row.tolist():
            if isinstance(v, str) and v.strip().lower() in kws:
                hits.append(idx)
                break
            # also catch "Total:" / "Grand Total" as a leading label
            if isinstance(v, str):
                low = v.strip().lower()
                if any(low.startswith(k) for k in kws):
                    hits.append(idx)
                    break
    if hits:
        flags.append(_flag(
            "subtotal / total rows", "table body",
            f"{len(hits)} row(s) look like subtotals/totals: rows {hits[:20]}. "
            f"Including them double-counts when summing or counting records.",
        ))
    return flags


def _high_blank_columns(df):
    """Columns blanker than the configured threshold."""
    flags = []
    n = len(df)
    if n == 0:
        return flags
    for col in df.columns:
        blank_rate = df[col].map(_is_blank).mean()
        if blank_rate >= cfg.BLANK_RATE_THRESHOLD:
            flags.append(_flag(
                "high blank-rate column", f"column '{col}'",
                f"{blank_rate:.0%} blank (threshold {cfg.BLANK_RATE_THRESHOLD:.0%}). "
                f"May be a mostly-empty or misaligned column.",
            ))
    return flags


def _mixed_type_columns(df):
    """Columns mixing numeric and non-numeric non-null values."""
    flags = []
    for col in df.columns:
        vals = df[col][~df[col].map(_is_blank)]
        if len(vals) == 0:
            continue
        as_str = vals.astype(str).str.strip()
        numeric = pd.to_numeric(as_str, errors="coerce").notna()
        num_frac = numeric.mean()
        non_num_frac = 1 - num_frac
        minority = min(num_frac, non_num_frac)
        if 0 < minority and minority >= cfg.MIXED_TYPE_MIN_MINORITY:
            flags.append(_flag(
                "mixed data types", f"column '{col}'",
                f"{num_frac:.0%} numeric / {non_num_frac:.0%} text. Mixed columns "
                f"break sorting, math, and type assumptions.",
            ))
    return flags


def validate_table(df):
    """Run all data-quality checks on a detected table."""
    flags = []
    flags += _repeated_header_rows(df)
    flags += _subtotal_rows(df)
    flags += _high_blank_columns(df)
    flags += _mixed_type_columns(df)
    return flags


# ===========================================================================
# Consolidation helper
# ===========================================================================
def merge_engine_flags(date_flags, numeric_flags):
    """Turn the cleanup engine's (col, reason) flag tuples into the standard
    flag shape so everything can be reported uniformly."""
    out = []
    for col, reason in (date_flags or []):
        out.append(_flag("date not normalized", f"column '{col}'",
                         f"Left as-is: {reason}."))
    for col, reason in (numeric_flags or []):
        out.append(_flag("number not converted", f"column '{col}'",
                         f"Left as text: {reason}."))
    return out
