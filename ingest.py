"""MERRY ingest: every file dropped in the sidebar is classified here first, then handed to MERRY.

CSV -> classified by its column headers (the same columns APP.SP_LOAD_SITE_CSV loads): timesheets, gate log, daily
       reports or equipment log. Rate files and unknown CSVs are refused here, with the reason.
PDF -> classified by its CONTENT with AI_CLASSIFY (INVOICE / TICKET / CONTRACT / OTHER), so file names do not matter;
       the file-name rule is only a fallback if AI_CLASSIFY fails. OTHER is refused before any extraction cost.

Accepted files go to MERRY unchanged (merry_tab.feed_uploads): site-record CSVs to SP_LOAD_SITE_CSV, PDFs to MERRY's
intake, which reads and files them. This module never writes to a table: data changes only through MERRY's procedures.
"""

import csv
import io
import json
import re
import uuid

import merry_tab                 # merry_chat/merry_tab.py (on sys.path, see streamlit_app.py)
from common import STAGE         # merry_chat/common.py, rendered for the database in settings.py

# ----------------------------------------------------------------------------
# CSV: header -> site record type (columns as in APP.SP_LOAD_SITE_CSV)
# ----------------------------------------------------------------------------
CSV_TYPES = {
    "TIMESHEETS": {"work_date", "worker_id", "worker_name", "contractor_code", "role", "hours", "approved_by"},
    "GATE_LOG": {"gate_entry_id", "entry_date", "subject_type", "subject_id", "subject_name", "contractor_code", "gate",
                 "time_in", "time_out", "remarks"},
    "DAILY_REPORTS": {"report_date", "weather", "ops_status", "camp_headcount", "activities", "supervisor_notes",
                      "reported_by"},
    "EQUIPMENT_LOG": {"log_date", "equipment_id", "contractor_code", "status", "hours_run", "remarks"},
}
RATE_WORDS = {"rate", "rate_php", "unit_rate", "rate_code", "day_rate", "hourly_rate", "standby_rate", "standby_rate_php"}

# ----------------------------------------------------------------------------
# PDF: AI_CLASSIFY labels. Descriptions (max 25 words each) tell the model what makes each type different.
# ----------------------------------------------------------------------------
DOC_TYPE_LABELS = [
    {"label": "INVOICE", "description": "Contractor bill requesting payment, with invoice number, line items, amounts, "
                                        "VAT and total due"},
    {"label": "TICKET", "description": "Daily field ticket recording personnel, hours, equipment and quantities at a "
                                       "site, approved by a company representative"},
    {"label": "CONTRACT", "description": "Master service agreement or contract with terms, clauses, rate schedule and "
                                         "contractor schedules"},
    {"label": "OTHER", "description": "Any other document"},
]
CLASSIFY_CONFIG = {"task_description": "Classify oilfield contractor documents by document type"}
PDF_TYPES = {"INVOICE", "TICKET", "CONTRACT"}
LABEL_WORDS = {"INVOICE": "Invoice", "TICKET": "Field ticket", "CONTRACT": "Contract", "TIMESHEETS": "Timesheets",
               "GATE_LOG": "Gate log", "DAILY_REPORTS": "Daily reports", "EQUIPMENT_LOG": "Equipment log"}


def safe_name(name: str) -> str:
    """Keep only safe characters so the file name can be used in a stage path and SQL."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _sql_literal(value) -> str:
    """Render a Python constant as a Snowflake array/object constant: {'k': 'v'}, ['a', 'b'].
    AI_CLASSIFY needs its labels and config as constants (PARSE_JSON(?) is rejected). Only use this for the module
    constants above - never for user input."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_sql_literal(k)}: {_sql_literal(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_sql_literal(v) for v in value) + "]"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


# ----------------------------------------------------------------------------
# Classifiers
# ----------------------------------------------------------------------------
def classify_csv(uploaded_file) -> str | None:
    """'RATES', one of CSV_TYPES, or None when the header matches no site record."""
    text = uploaded_file.getvalue().decode("utf-8-sig", "replace")
    header = next(csv.reader(io.StringIO(text)), [])
    head = {h.strip().lower() for h in header}
    if head & RATE_WORDS or "rate" in uploaded_file.name.lower():
        return "RATES"
    return next((t for t, cols in CSV_TYPES.items() if cols <= head), None)


def pdf_type_for(filename: str) -> str | None:
    """Fallback: the document type from the file name (used only if AI_CLASSIFY fails)."""
    name = filename.upper()
    if "-INV-" in name or "INVOICE" in name:
        return "INVOICE"
    if "-T-" in name or "TICKET" in name:
        return "TICKET"
    if "CONTRACT" in name or "MSA" in name or "AGREEMENT" in name:
        return "CONTRACT"
    return None


def classify_pdf(session, uploaded_file, batch: str) -> tuple[str, str]:
    """(doc_type, how): doc_type is INVOICE, TICKET, CONTRACT or OTHER; how is 'AI_CLASSIFY' or 'file name'.
    The PDF is saved under _classify/<batch>/ on the document stage so AI_CLASSIFY can read its content."""
    name = safe_name(uploaded_file.name)
    path = f"_classify/{batch}/{name}"
    try:
        session.file.put_stream(io.BytesIO(uploaded_file.getvalue()), f"{STAGE}/{path}",
                                auto_compress=False, overwrite=True)
        row = session.sql(
            f"""SELECT AI_CLASSIFY(TO_FILE('{STAGE}', ?), {_sql_literal(DOC_TYPE_LABELS)},
                                   {_sql_literal(CLASSIFY_CONFIG)})""",
            params=[path]).collect()[0][0]
        result = json.loads(row) if isinstance(row, str) else (row or {})
        labels = result.get("labels") or (result.get("value") or {}).get("labels") or []
    except Exception:
        labels = []
    if labels:
        return (labels[0] if labels[0] in PDF_TYPES else "OTHER"), "AI_CLASSIFY"
    return (pdf_type_for(name) or "OTHER"), "file name"


# ----------------------------------------------------------------------------
# One-click ingestion used by the Submit button
# ----------------------------------------------------------------------------
def submit(session, uploaded_files) -> tuple[int, list[str]]:
    """Classify every upload, refuse what MERRY can't use, and hand the rest to MERRY.
    Returns (number of PDFs passed to MERRY's intake, one message per file)."""
    batch = uuid.uuid4().hex[:16]
    messages, accepted = [], []
    for f in uploaded_files or []:
        lower = f.name.lower()
        if lower.endswith(".csv"):
            kind = classify_csv(f)
            if kind == "RATES":
                messages.append(f"📊 {f.name} → rate file, not loaded: rates come from the contract.")
            elif kind is None:
                messages.append(f"⚠️ {f.name} → not loaded: the columns match no site record (timesheets, gate log, "
                                "daily reports or equipment log).")
            else:
                messages.append(f"📊 {f.name} → classified as {LABEL_WORDS[kind]} (column headers).")
                accepted.append(f)
        elif lower.endswith(".pdf"):
            kind, how = classify_pdf(session, f, batch)
            if kind == "OTHER":
                messages.append(f"⚠️ {f.name} → not an invoice, field ticket or contract ({how}), so not sent to MERRY.")
            else:
                messages.append(f"📄 {f.name} → classified as {LABEL_WORDS[kind]} ({how}).")
                accepted.append(f)
        else:
            messages.append(f"⚠️ {f.name} → skipped: only .csv and .pdf are supported.")
    n_pdfs, loaded = merry_tab.feed_uploads(accepted)
    return n_pdfs, messages + loaded
