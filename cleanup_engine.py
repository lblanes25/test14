"""
cleanup_engine.py
=================
Deterministic cleanup transformations for the Data Cleanup / Data Prep GPT.

Design rules (non-negotiable):
  * Dependencies: pandas + numpy ONLY (both preinstalled in Code Interpreter).
  * Deterministic: same input -> same output, every run.
  * Transparent: every function returns a CHANGE RECORD describing what it did.
  * Conservative: ambiguous conditions are NOT fixed here. They are left for
    the validation module to flag. This module only does the safe, obvious work.
  * Non-destructive: rows that are removed are RETURNED to the caller (for the
    separate _REMOVED_ tab), never silently dropped.

Each public function has the signature:
    func(df) -> (df_out, change_record [, removed_rows])

change_record is a dict:
    {"operation": str, "columns": list, "count": int, "details": str}

The orchestrator (run_cleanup) calls them in order, collects the change
records into the transformation summary, and accumulates removed rows.
"""

import re
import pandas as pd
import numpy as np

import cleanup_config as cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_blank_series(s):
    """True where a cell is NaN or an empty/whitespace-only string."""
    return s.isna() | s.astype(str).str.strip().eq("")


def _norm_token(value):
    """Lowercase + trimmed form of a value, for matching against config lists."""
    return str(value).strip().lower()


def _is_text_col(series):
    """
    True for text columns. Must handle BOTH legacy object dtype AND modern
    pandas StringDtype — recent pandas infers string columns as StringDtype,
    so a bare `dtype == object` check silently skips them.
    """
    return series.dtype == object or pd.api.types.is_string_dtype(series)


# ---------------------------------------------------------------------------
# Structural cleanup
# ---------------------------------------------------------------------------
def clean_headers(df):
    """Trim and collapse whitespace in column labels. No renaming/snake_case."""
    changed = []
    new_cols = []
    for col in df.columns:
        if cfg.HEADER_TRIM_WHITESPACE and isinstance(col, str):
            cleaned = re.sub(r"\s+", " ", col).strip()
        else:
            cleaned = col
        if cleaned != col:
            changed.append((col, cleaned))
        new_cols.append(cleaned)
    df = df.copy()
    df.columns = new_cols
    record = {
        "operation": "clean_headers",
        "columns": [c[1] for c in changed],
        "count": len(changed),
        "details": "Trimmed/collapsed whitespace in headers. Labels preserved "
                   "(no rename) so cleaned columns still match the source.",
    }
    return df, record


def remove_blank_rows(df):
    """Relocate fully-blank rows. Returns removed rows for the _REMOVED_ tab."""
    blank_mask = df.apply(lambda row: _is_blank_series(row).all(), axis=1)
    removed = df[blank_mask].copy()
    df_out = df[~blank_mask].copy()
    if not removed.empty:
        removed[cfg.REMOVED_REASON_COLUMN] = "fully blank row"
    record = {
        "operation": "remove_blank_rows",
        "columns": [],
        "count": int(blank_mask.sum()),
        "details": f"{int(blank_mask.sum())} fully-blank rows relocated to "
                   f"'{cfg.REMOVED_TAB_NAME}'.",
    }
    return df_out, record, removed


def remove_blank_columns(df):
    """Drop fully-blank columns. Names logged in the change record."""
    blank_cols = [c for c in df.columns if _is_blank_series(df[c]).all()]
    df_out = df.drop(columns=blank_cols).copy()
    record = {
        "operation": "remove_blank_columns",
        "columns": blank_cols,
        "count": len(blank_cols),
        "details": f"Dropped {len(blank_cols)} fully-blank columns: "
                   f"{blank_cols if blank_cols else 'none'}.",
    }
    return df_out, record


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------
def trim_whitespace(df):
    """Strip + collapse internal whitespace in all string cells."""
    df = df.copy()
    touched = 0
    cols_touched = []
    for col in df.columns:
        if _is_text_col(df[col]):
            before = df[col].copy()
            df[col] = df[col].map(
                lambda v: re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v
            )
            n = int((before != df[col]).sum())
            if n:
                touched += n
                cols_touched.append(col)
    record = {
        "operation": "trim_whitespace",
        "columns": cols_touched,
        "count": touched,
        "details": f"Trimmed/collapsed whitespace in {touched} cells across "
                   f"{len(cols_touched)} columns.",
    }
    return df, record


def normalize_nulls(df):
    """Map configured null-variant strings to true NaN (case-insensitive)."""
    df = df.copy()
    variants = {v.lower() for v in cfg.NULL_VARIANTS}
    touched = 0
    cols_touched = []
    for col in df.columns:
        if _is_text_col(df[col]):
            mask = df[col].map(
                lambda v: _norm_token(v) in variants if isinstance(v, str) else False
            )
            n = int(mask.sum())
            if n:
                df.loc[mask, col] = np.nan
                touched += n
                cols_touched.append(col)
    record = {
        "operation": "normalize_nulls",
        "columns": cols_touched,
        "count": touched,
        "details": f"Standardized {touched} null-like values "
                   f"({', '.join(cfg.NULL_VARIANTS)}) to empty for consistent "
                   f"filtering and counting.",
    }
    return df, record


def standardize_yes_no(df):
    """Map obvious Yes/No variants to standard form. Unknowns left untouched."""
    df = df.copy()
    yes = {v.lower() for v in cfg.YES_VARIANTS}
    no = {v.lower() for v in cfg.NO_VARIANTS}
    touched = 0
    cols_touched = []
    for col in df.columns:
        if not _is_text_col(df[col]):
            continue
        col_vals = df[col].dropna().map(_norm_token)
        # Only treat as a Yes/No column if every non-null value is recognized.
        recognized = col_vals.map(lambda t: t in yes or t in no)
        if len(col_vals) and recognized.all():
            def _map(v):
                if not isinstance(v, str):
                    return v
                t = _norm_token(v)
                if t in yes:
                    return cfg.YES_STANDARD
                if t in no:
                    return cfg.NO_STANDARD
                return v
            before = df[col].copy()
            df[col] = df[col].map(_map)
            n = int((before != df[col]).sum())
            if n:
                touched += n
                cols_touched.append(col)
    record = {
        "operation": "standardize_yes_no",
        "columns": cols_touched,
        "count": touched,
        "details": f"Standardized Yes/No values in {len(cols_touched)} columns. "
                   f"Columns with unrecognized values were left untouched (flagged "
                   f"by validation instead).",
    }
    return df, record


# ---------------------------------------------------------------------------
# Numbers stored as text
# ---------------------------------------------------------------------------
# A cell that is a formatted number: optional currency symbol, optional
# accounting-parentheses negative, digits with optional thousands commas and
# optional decimal. Examples: 1,200  $1,200.50  (500)  1200
_NUMERIC_CELL = re.compile(
    r"^\(?\s*[$£€¥]?\s*\d{1,3}(,\d{3})+(\.\d+)?\s*\)?$"   # with thousands commas
    r"|^\(?\s*[$£€¥]?\s*\d+(\.\d+)?\s*\)?$"               # plain / decimal
)


def _strip_one_number(value):
    """Strip formatting from a single numeric-looking string -> clean numeric
    string (negatives as -N). Returns the original value if it doesn't look
    like a formatted number."""
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not _NUMERIC_CELL.match(s):
        return value
    negative = s.startswith("(") and s.endswith(")")
    for sym in cfg.NUMERIC_STRIP_SYMBOLS:
        s = s.replace(sym, "")
    s = s.replace(",", "").replace("(", "").replace(")", "").strip()
    if negative and s:
        s = "-" + s
    return s


def strip_numeric_formatting(df):
    """SAFE pass: on columns that are predominantly formatted numbers, strip
    currency symbols, thousands separators, and accounting parentheses.
    Columns that are mostly genuine text are left untouched."""
    df = df.copy()
    touched, cols_touched = 0, []
    for col in df.columns:
        if not _is_text_col(df[col]):
            continue
        vals = df[col].dropna().astype(str).map(str.strip)
        if len(vals) == 0:
            continue
        looks_num = vals.map(lambda v: bool(_NUMERIC_CELL.match(v))).mean()
        if looks_num < 0.7:           # not a numeric column; leave text alone
            continue
        before = df[col].copy()
        df[col] = df[col].map(_strip_one_number)
        n = int((before != df[col]).sum())
        if n:
            touched += n
            cols_touched.append(col)
    record = {
        "operation": "strip_numeric_formatting",
        "columns": cols_touched,
        "count": touched,
        "details": f"Stripped currency/thousands/parentheses formatting from "
                   f"{touched} numeric cells across {len(cols_touched)} columns.",
    }
    return df, record


def _looks_like_identifier(vals):
    """vals: cleaned non-null string Series. True if the column has an
    identifier fingerprint and should NOT be coerced to numeric."""
    if cfg.TREAT_LEADING_ZERO_AS_ID:
        if vals.map(lambda v: bool(re.match(r"^0\d+$", v))).any():
            return True               # leading-zero integers: ZIP / account IDs
    if cfg.TREAT_UNIFORM_LENGTH_INT_AS_ID:
        all_int = vals.map(lambda v: bool(re.match(r"^\d+$", v))).all()
        if all_int:
            lengths = vals.map(len).unique()
            if len(lengths) == 1 and lengths[0] >= cfg.UNIFORM_ID_MIN_LENGTH:
                return True            # uniform-width integer codes
    return False


def coerce_numeric(df):
    """GUARDED pass: convert a column to real numbers only if every non-null
    value is numeric AND the column doesn't look like an identifier. Mixed or
    identifier-like columns are left as-is and reported for flagging."""
    df = df.copy()
    converted, flagged = [], []
    for col in df.columns:
        if not _is_text_col(df[col]):
            continue
        vals = df[col].dropna().astype(str).map(str.strip)
        if len(vals) == 0:
            continue
        as_num = pd.to_numeric(vals, errors="coerce")
        all_numeric = as_num.notna().all()
        any_numeric = as_num.notna().any()
        if not all_numeric:
            if any_numeric:
                flagged.append((col, "mixed numeric/text"))
            continue
        if _looks_like_identifier(vals):
            flagged.append((col, "numeric but looks like identifier — left as text"))
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
        converted.append(col)
    record = {
        "operation": "coerce_numeric",
        "columns": converted,
        "count": len(converted),
        "details": f"Converted {len(converted)} text columns to numeric. "
                   f"Left as text / flagged: {flagged if flagged else 'none'}.",
    }
    return df, record, flagged


# ---------------------------------------------------------------------------
# Dates  (column-level disambiguation — the high-risk operation)
# ---------------------------------------------------------------------------
_DATE_SEP = re.compile(r"[/\-.]")


def _parse_parts(value):
    """Return [a, b, c] integer parts of a 3-part date string, else None."""
    if not isinstance(value, str):
        return None
    parts = _DATE_SEP.split(value.strip())
    if len(parts) != 3:
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    return nums


def _column_date_order(series):
    """
    Inspect a whole column and decide day/month order.

    Returns one of:
        "dmy"          - first component is day (evidence found)
        "mdy"          - first component is month (evidence found)
        "ambiguous"    - every value could go either way; cannot determine
        "contradictory"- evidence for BOTH orders; source is inconsistent
        "not_dates"    - column doesn't look like 3-part dates
    """
    saw_dmy = False
    saw_mdy = False
    looked_like_date = 0
    for v in series.dropna():
        parts = _parse_parts(v)
        if parts is None:
            continue
        looked_like_date += 1
        a, b, c = parts
        # Heuristic for leading day-vs-month when year is last (a/b/c = d/m/y or m/d/y)
        if c > 31:  # last part is a year -> a and b are day/month in some order
            if a > 12 and b <= 12:
                saw_dmy = True       # first part can only be a day
            elif b > 12 and a <= 12:
                saw_mdy = True       # second part can only be a day
        elif a > 31:  # first part is a year (ISO-ish y/m/d) -> already unambiguous
            pass
    if looked_like_date == 0:
        return "not_dates"
    if saw_dmy and saw_mdy:
        return "contradictory"
    if saw_dmy:
        return "dmy"
    if saw_mdy:
        return "mdy"
    return "ambiguous"


def _coerce_with_order(value, order):
    """
    Convert one date string to a pandas Timestamp using the column's decided
    order ('dmy' or 'mdy'). Handles ISO (year-first) values mixed into the
    column without relying on pandas' ambiguous dayfirst handling.
    Returns Timestamp or NaT.
    """
    parts = _parse_parts(value)
    if parts is None:
        return pd.NaT
    a, b, c = parts
    try:
        if a > 31:                       # year-first (ISO): a=year, b=month, c=day
            y, m, d = a, b, c
        elif c > 31:                     # year-last: a/b are day/month per order
            y = c
            if order == "dmy":
                d, m = a, b
            else:                        # mdy
                m, d = a, b
        else:
            return pd.NaT                # no 4-digit year locatable -> give up
        return pd.Timestamp(year=y, month=m, day=d)
    except (ValueError, TypeError):
        return pd.NaT


def normalize_dates(df, date_columns):
    """
    Convert date columns to DATE_OUTPUT_FORMAT using column-level order.
    Columns that are ambiguous or contradictory are NOT converted; they are
    reported so the validation layer can flag them.

    date_columns: list of column names the caller believes are dates.
    """
    df = df.copy()
    converted, flagged = [], []
    for col in date_columns:
        if col not in df.columns:
            continue
        order = _column_date_order(df[col])
        if order in ("ambiguous", "contradictory", "not_dates"):
            flagged.append((col, order))
            continue
        dayfirst = (order == "dmy")
        parsed = df[col].map(lambda v: _coerce_with_order(v, order))
        parsed = pd.to_datetime(parsed, errors="coerce")
        # If parsing wrecked too much (lots of NaT from non-nulls), don't trust it.
        non_null = df[col].notna().sum()
        if non_null and parsed.notna().sum() < non_null:
            flagged.append((col, "partial_parse_failure"))
            continue
        df[col] = parsed.dt.strftime(cfg.DATE_OUTPUT_FORMAT)
        converted.append(col)
    record = {
        "operation": "normalize_dates",
        "columns": converted,
        "count": len(converted),
        "details": f"Converted {len(converted)} date columns to "
                   f"{cfg.DATE_OUTPUT_FORMAT}. Flagged (not converted): "
                   f"{flagged if flagged else 'none'}.",
    }
    return df, record, flagged


# ---------------------------------------------------------------------------
# Duplicates  (full-row identical only — flagged & relocated, not deleted)
# ---------------------------------------------------------------------------
def relocate_duplicates(df):
    """Keep first occurrence; relocate later full-row duplicates."""
    dup_mask = df.duplicated(keep="first")
    removed = df[dup_mask].copy()
    df_out = df[~dup_mask].copy()
    if not removed.empty:
        removed[cfg.REMOVED_REASON_COLUMN] = "full-row duplicate"
    record = {
        "operation": "relocate_duplicates",
        "columns": [],
        "count": int(dup_mask.sum()),
        "details": f"{int(dup_mask.sum())} full-row duplicates relocated to "
                   f"'{cfg.REMOVED_TAB_NAME}' (first occurrence kept).",
    }
    return df_out, record, removed


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_cleanup(df, date_columns=None):
    """
    Run the full deterministic cleanup pass in fixed order.

    Returns:
        df_clean       : cleaned DataFrame
        summary        : list of change records (feeds the transformation summary)
        removed_frames : list of DataFrames to write to the _REMOVED_ tab
        date_flags     : date columns that could not be confidently converted
        numeric_flags  : columns that were mixed numeric/text or identifier-like
    """
    date_columns = date_columns or []
    summary = []
    removed_frames = []

    df, rec = clean_headers(df);                summary.append(rec)
    df, rec = remove_blank_columns(df);         summary.append(rec)
    df, rec, removed = remove_blank_rows(df);   summary.append(rec)
    if not removed.empty:
        removed_frames.append(removed)

    df, rec = trim_whitespace(df);              summary.append(rec)
    df, rec = normalize_nulls(df);              summary.append(rec)
    df, rec = standardize_yes_no(df);           summary.append(rec)

    numeric_flags = []
    if cfg.COERCE_TEXT_NUMBERS:
        df, rec = strip_numeric_formatting(df); summary.append(rec)
        df, rec, numeric_flags = coerce_numeric(df); summary.append(rec)

    df, rec, date_flags = normalize_dates(df, date_columns)
    summary.append(rec)

    df, rec, removed = relocate_duplicates(df); summary.append(rec)
    if not removed.empty:
        removed_frames.append(removed)

    return df, summary, removed_frames, date_flags, numeric_flags
