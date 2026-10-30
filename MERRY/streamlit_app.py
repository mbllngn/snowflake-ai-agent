"""MERRY - invoice audit app.

D32 (team-approved): MERRY is the single engine and source of truth. Every upload goes to MERRY (PDFs to its intake,
site-record CSVs to APP.SP_LOAD_SITE_CSV); the Dashboard, Issues and Data tabs read MERRY's APP.V_UI_* views,
and saving a review goes through MERRY's procedures, so the chat, the activity history and the letters stay in sync.
The database name comes from settings.py (through the generated merry_chat/common.py).

Files in this app:
  dashboard.py      dashboard widgets (unchanged)
  merry_chat/       the MERRY chat ("Ask MERRY" tab), built from merry_agent/ (merry_agent/tools/build_tab.py)
  streamlit_app.py  this file: page layout and buttons
"""

import os
import sys

import pandas as pd
import streamlit as st

import dashboard

# "Ask MERRY" tab: the MERRY chat, built from merry_agent/ into merry_chat/ (merry_agent/tools/build_tab.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "merry_chat"))
import merry_tab  # noqa: E402
from common import DB, current_user  # noqa: E402  (merry_chat/common.py, rendered for the database in settings.py)

MERRY_APP = f"{DB}.APP"             # all screens read MERRY's APP views

st.set_page_config(page_title="MERRY", page_icon="🧾", layout="wide")

conn = st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL"))
session = conn.session()


# -------------------------------------------------------------------
# Loaders (not cached: a review in the chat must show here on the next rerun)
# -------------------------------------------------------------------
def load_kpis():
    return session.sql(f"SELECT * FROM {MERRY_APP}.V_UI_KPIS").to_pandas()


def load_flags():
    return session.sql(
        f"""SELECT * FROM {MERRY_APP}.V_UI_FLAGS
            ORDER BY IFF(severity = 'HIGH', 0, 1), impact_php DESC NULLS LAST, work_date""").to_pandas()


# Tables the Data tab may show (fixed list - never build table names from user input) -> MERRY view
DATA_TABLES = {name: f"{MERRY_APP}.V_UI_DATA_{name}" for name in
               ["TIMESHEETS", "DAILY_REPORTS", "EQUIPMENT_LOG", "GATE_LOG", "TICKETS", "TICKET_ITEMS", "INVOICES",
                "INVOICE_LINES", "CONTRACT_RATES", "CONTRACT_TERMS"]}


@st.cache_data(ttl="1m", show_spinner=False)
def load_table(name: str):
    if name not in DATA_TABLES:
        raise ValueError(name)
    return session.sql(f"SELECT * FROM {DATA_TABLES[name]} LIMIT 1000").to_pandas()


def clear_caches() -> None:
    load_table.clear()


def audit_ready() -> bool:
    try:
        session.sql(f"SELECT 1 FROM {MERRY_APP}.V_UI_KPIS LIMIT 1").collect()
        return True
    except Exception:
        return False


def add_rule_info(flags: pd.DataFrame) -> None:
    """MERRY's checks have their own names: give dashboard.RULE_INFO a line for each (why, contract basis, what to check)."""
    for rule, rows in flags.groupby("RULE"):
        if rule not in dashboard.RULE_INFO:
            clauses = sorted({c.strip() for cs in rows["CLAUSES"].dropna() for c in str(cs).split(",") if c.strip()})
            basis = ", ".join(("Exhibit " if c.isalpha() else "Clause ") + c for c in clauses)
            dashboard.RULE_INFO[rule] = (rule, basis, "Open the invoice in Ask MERRY to see the evidence and the letter")


def call(sql: str, params: list):
    import json
    r = session.sql(sql, params=params).collect()[0][0]
    return json.loads(r) if isinstance(r, str) else r


# -------------------------------------------------------------------
# Sidebar: ingestion (MERRY only)
# -------------------------------------------------------------------
with st.sidebar:
    st.header("📥 Data")
    # The uploader button's icon is a Material Symbols ligature; where that font is not served it renders as the
    # word "upload" in front of the label ("uploadUpload"). Hide the icon so the button shows only its label.
    st.markdown("""<style>
        [data-testid="stFileUploaderDropzone"] button [data-testid="stIconMaterial"],
        [data-testid="stFileUploaderDropzone"] button span[translate="no"] { display: none !important; }
        </style>""", unsafe_allow_html=True)

    # A new key after each submit gives an empty uploader (Streamlit has no "clear uploader" call).
    st.session_state.setdefault("uploader_key", 0)
    uploads = st.file_uploader("➕ Add files (CSV or PDF)", type=["pdf", "csv"], accept_multiple_files=True,
                               key=f"uploader_{st.session_state.uploader_key}",
                               help="PDFs (contracts, invoices, field tickets) go to MERRY's intake. Site-record CSVs "
                                    "(timesheets, gate log, daily reports, equipment log) are loaded into MERRY; rows "
                                    "already loaded are ignored. Rates come from the contract.")

    if st.button("Submit", type="primary", width="stretch"):
        with st.status("Passing files to MERRY...", expanded=True) as status:
            n_pdfs, messages = merry_tab.feed_uploads(uploads)
            if n_pdfs:
                messages = messages + [f"{n_pdfs} PDF(s) passed to MERRY's intake (see the Ask MERRY tab)."]
            status.update(label="Done", state="complete")
        clear_caches()
        st.session_state.last_ingest = messages or ["No files selected."]
        st.session_state.uploader_key += 1           # -> empty uploader
        st.rerun()                                   # -> dashboard reloads with the new data

    if st.session_state.get("last_ingest"):
        with st.expander(f"✅ Last submit ({len(st.session_state.last_ingest)} item(s))", expanded=True):
            for msg in st.session_state.last_ingest:
                st.write(msg)
            if st.button("Dismiss", key="dismiss_ingest"):
                st.session_state.last_ingest = []
                st.rerun()

# -------------------------------------------------------------------
# Main page
# -------------------------------------------------------------------
st.title("🧾 MERRY - Invoice Audit")

if not audit_ready():
    st.info("MERRY's audit data is not set up yet. Run the setup (see SETUP.md), then drop a contract in Ask MERRY.")
    st.stop()

NO_CONTRACTS = "No contracts yet. Drop your first contract in Ask MERRY."
has_contracts = bool(session.sql(f"SELECT COUNT(*) n FROM {DB}.CONTRACT.CONTRACTS").collect()[0]["N"])

tab_dash, tab_issues, tab_chat, tab_data = st.tabs(["📊 Dashboard", "⚠️ Issues", "💬 Ask MERRY", "📂 Data"])

with st.spinner("Loading audit results..."):
    kpis, flags = load_kpis(), load_flags()
    add_rule_info(flags)

with tab_dash:
    dashboard.show_dashboard(kpis, flags, has_contracts, NO_CONTRACTS)

with tab_issues:
    st.subheader("Issue breakdown")
    if st.session_state.get("review_msg"):
        kind, text = st.session_state.pop("review_msg")
        getattr(st, kind)(text)
    selected = dashboard.show_flag_table(dashboard.filter_flags(flags))
    if selected is not None:
        st.markdown(f"**{selected['RULE']}** · {selected['SOURCE_DOC']} · {selected['REASON']}")
        c1, c2 = st.columns([1, 2])
        with c1:
            new_status = st.selectbox("Review status", dashboard.STATUSES,
                                      index=dashboard.STATUSES.index(selected["STATUS"]))
            note = st.text_input("Note", value="" if pd.isna(selected["REVIEW_NOTE"]) else selected["REVIEW_NOTE"],
                                 help="Needed (10+ characters) to reopen a finding or to change MERRY's decision.")
            credit_ref = credit_amt = None
            if new_status == "RECOVERED" and selected["STATUS"] != "RECOVERED":
                credit_ref = st.text_input("Credit note number")
                credit_amt = st.text_input("Credit amount (PHP)", value=f"{dashboard._num(selected['IMPACT_PHP']):.2f}")
            if st.button("Save review"):
                if new_status == "RECOVERED":
                    r = call(f"CALL {DB}.AUDIT.SP_RECORD_CREDIT_NOTE(?, ?, ?, ?, ?)",
                             [selected["FLAG_ID"], credit_ref or "", credit_amt or "", note, current_user()])
                    ok = r.get("status") == "RECORDED"
                    text = "Credit note recorded: the finding is RECOVERED." if ok else r.get("error")
                else:
                    r = call(f"CALL {DB}.APP.SP_REVIEW_FINDING(?, ?, ?, ?)",
                             [selected["FLAG_ID"], new_status, note, current_user()])
                    ok = r.get("status") in ("SETTLED", "REOPENED")
                    if not ok:
                        text = r.get("error")
                    elif r["status"] == "REOPENED":
                        text = "Finding reopened" + (" and the invoice letter is a draft again." if r.get("letter_version") else ".")
                    else:
                        ap = (r.get("approval") or {}).get("status")
                        text = (f"Saved as {new_status}." + (" MERRY changed its decision to match." if r.get("decision_changed") else "")
                                + (f" Every finding on {r['invoice_no']} is settled, so its letter was approved." if ap == "APPROVED"
                                   else f" {r['findings_still_open']} finding(s) on {r['invoice_no']} still open." if r.get("findings_still_open") else ""))
                st.session_state.review_msg = ("success" if ok else "error", text)
                clear_caches()
                st.rerun()
        with c2:
            if st.button("🤖 Explain this issue"):
                st.markdown(f"**MERRY's decision:** {selected['DECISION_WORD'] or selected['DECISION']}"
                            + (f" · clauses {selected['CLAUSES']}" if selected["CLAUSES"] else ""))
                st.write(str(selected["RATIONALE"] or "").replace("PHP ", "₱"))
                st.caption("From MERRY's stored review. Ask MERRY about this invoice for the evidence and the letter.")
    else:
        st.caption("Select a row to review or explain it.")

with tab_chat:
    merry_tab.render()

with tab_data:
    table = st.selectbox("Table", list(DATA_TABLES))
    st.dataframe(load_table(table), hide_index=True, width="stretch")
