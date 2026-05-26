"""
cleanup_config.py
==================
Central configuration for the Data Cleanup / Data Preparation GPT.

Everything that controls cleanup BEHAVIOR lives here, not buried in the
cleanup functions. This is deliberate: when asked "how do you decide what
counts as a null / a Yes / an ambiguous date," the answer is this readable
file, not source code. Thresholds and mappings are visible and auditable.

This module has NO external dependencies. It is plain Python data.
The cleanup and validation modules import these values.
"""

# ---------------------------------------------------------------------------
# HEADERS
# ---------------------------------------------------------------------------
# We do NOT snake_case or otherwise rename columns. Preserving the original
# label means a user can VLOOKUP / INDEX-MATCH the cleaned file back to the
# source to confirm nothing was broken. We only trim and collapse whitespace.
HEADER_TRIM_WHITESPACE = True          # strip + collapse internal runs of spaces
HEADER_SNAKE_CASE = False              # leave labels intact for source-matching
HEADER_FIX_CASE = False                # do not change header capitalization

# ---------------------------------------------------------------------------
# VALUE CASING
# ---------------------------------------------------------------------------
# Off by default. Changing "smith" -> "Smith" edits business content, not
# representation, and source casing may be meaningful. Only apply casing to
# columns the user has explicitly named as categorical (none by default).
NORMALIZE_VALUE_CASE = False
CATEGORICAL_COLUMNS = []               # opt-in only; empty in v1

# ---------------------------------------------------------------------------
# CELL FORMATTING
# ---------------------------------------------------------------------------
# Dropped from v1. Alignment / text-wrapping are cosmetic Excel styling, not
# data, and "analytics-ready" does not depend on them. Revisit in v2.
HANDLE_CELL_FORMATTING = False

# ---------------------------------------------------------------------------
# NULL NORMALIZATION
# ---------------------------------------------------------------------------
# These string values are mapped to a true empty (NaN) so filtering and
# counting behave consistently. Matching is case-insensitive and trimmed.
NULL_VARIANTS = [
    "",
    "n/a",
    "na",
    "null",
    "none",
    "-",
    "--",
    "#n/a",
]

# ---------------------------------------------------------------------------
# YES / NO STANDARDIZATION
# ---------------------------------------------------------------------------
# Obvious boolean-style variants map to a standard form. Anything not in
# these lists is left alone and flagged (never guessed).
YES_VARIANTS = ["y", "yes", "true", "t", "1"]
NO_VARIANTS = ["n", "no", "false", "f", "0"]
YES_STANDARD = "Yes"
NO_STANDARD = "No"

# ---------------------------------------------------------------------------
# DATES
# ---------------------------------------------------------------------------
# Output format is YYYY-MM-DD (ISO 8601). CONFIGURABLE — confirm with the
# team before finalizing in case they prefer another standard.
DATE_OUTPUT_FORMAT = "%Y-%m-%d"

# Date disambiguation is COLUMN-LEVEL, not row-level:
#   1. Scan the whole column.
#   2. If any value has a day/month component > 12, that fixes the
#      day-vs-month order for the entire column -> convert all rows.
#   3. If the column is entirely ambiguous (every row could go either way)
#      -> FLAG, do not convert.
#   4. If the scan finds evidence of BOTH orders (some clearly DMY, some
#      clearly MDY) -> the source is internally inconsistent -> FLAG.
# This stays fully deterministic: same column always resolves the same way.
DATE_DISAMBIGUATION = "column_scan"

# ---------------------------------------------------------------------------
# NUMBERS STORED AS TEXT
# ---------------------------------------------------------------------------
# Two separate operations:
#   1. strip_numeric_formatting  - SAFE. On predominantly-numeric columns,
#      strip currency symbols, thousands commas, stray spaces, and convert
#      (123) accounting-negatives to -123. Genuine text columns are untouched.
#   2. coerce_numeric            - GUARDED. Convert a column to real numbers
#      ONLY if every non-null value is numeric AND it doesn't look like an
#      identifier (see safeguards). Mixed columns are left alone and flagged.
COERCE_TEXT_NUMBERS = True

# Currency / symbol characters stripped from numeric-looking cells.
NUMERIC_STRIP_SYMBOLS = ["$", "£", "€", "¥"]

# Identifier safeguards — DO NOT coerce a column to numeric if:
#   * any value has a leading-zero integer signature (e.g. 00123, 0501) — this
#     is ZIP codes, account/employee IDs. Strong signal; keep enabled.
TREAT_LEADING_ZERO_AS_ID = True
#   * the column is entirely integers of one identical length >= the width
#     below (the ZIP/ID fingerprint). Heuristic — EYEBALL against real files;
#     uniform 5-digit *amounts* would be false-flagged. Tune or disable here.
TREAT_UNIFORM_LENGTH_INT_AS_ID = True
UNIFORM_ID_MIN_LENGTH = 5

# ---------------------------------------------------------------------------
# VALIDATION / FLAGGING
# ---------------------------------------------------------------------------
# These drive the flag-only validation layer. Nothing here modifies data —
# conditions are described and flagged for human review.

# Rows containing any of these (case-insensitive) are flagged as likely
# subtotal / total rows that would distort record counts and sums.
SUBTOTAL_KEYWORDS = ["total", "subtotal", "grand total", "sum", "average", "avg"]

# A column is flagged "mixed type" only if it has at least this fraction of
# numeric values AND at least this fraction of non-numeric values (so a column
# that's 99% numeric with one stray label is still flagged, but near-pure
# columns aren't). Tunable.
MIXED_TYPE_MIN_MINORITY = 0.05

# ---------------------------------------------------------------------------
# DUPLICATES
# ---------------------------------------------------------------------------
# Full-row identical only. We cannot pass key-column criteria into a
# customGPT knowledge-file script, so subset/key-based dedup is out of scope.
# Duplicates are FLAGGED and relocated, never silently deleted.
DUPLICATE_DEFINITION = "full_row_identical"

# ---------------------------------------------------------------------------
# BLANK-RATE FLAGGING
# ---------------------------------------------------------------------------
# Columns blanker than this fraction are flagged for review (not removed).
BLANK_RATE_THRESHOLD = 0.50            # 50%

# ---------------------------------------------------------------------------
# REMOVED-DATA HANDLING
# ---------------------------------------------------------------------------
# Nothing is ever deleted. Removed/relocated rows go to ONE separate tab,
# with their ORIGINAL column structure preserved (no consolidation across
# different removal types — that would force a common schema = chaos), plus
# an appended reason column. The tab is hidden and unmistakably named so it
# never gets mistaken for analysis data, regardless of how many tabs exist.
REMOVED_TAB_NAME = "_REMOVED_(do not analyze)"
REMOVED_TAB_HIDDEN = True
REMOVED_REASON_COLUMN = "_removal_reason"
PRESERVE_ORIGINAL = True               # source file is never overwritten
