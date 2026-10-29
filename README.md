# MERRY – Contractor Invoice Audit

MERRY checks contractor invoices against the service contract and the site records (timesheets, gate log, equipment
log, daily reports) in your Snowflake account, and talks the analyst through the findings in a chat.

- SQL checks flag invoice lines; a Cortex Agent reviews each flag against the contract clauses and site records.
- Every peso amount comes from SQL; the agent only explains it. Nothing is emailed: approving saves a letter PDF.
- One Streamlit app (container runtime) with four tabs: **Dashboard**, **Issues**, **Ask MERRY** (the chat) and **Data**.
  Every screen reads the same database views, so the chat and the screens always agree.

**To install it in your account, follow [SETUP.md](SETUP.md).** In short: fill in `settings.py`, run
`python3 merry_agent/setup.py`, open Snowsight > Projects > Streamlit > MERRY.

## What is in the repository

| Path | What it is |
|---|---|
| `settings.py` | The only place for account-specific names: database, role, warehouse, compute pool, models |
| `SETUP.md` | Installing MERRY in a new account: requirements, settings, setup, first use, costs, troubleshooting |
| `streamlit_app.py`, `dashboard.py`, `pyproject.toml` | The app: sidebar upload, Dashboard, Issues and Data tabs (the Ask MERRY tab comes from `merry_agent/app/`) |
| `merry_agent/setup.py` | The one setup command (database, app, smoke test) |
| `merry_agent/deploy.py` | Creates or updates every database object; never drops or empties a table |
| `merry_agent/sql/` | The database objects, in creation order, with `{{DB}}`-style placeholders filled from `settings.py` |
| `merry_agent/app/` | The chat and its helpers (built into `merry_chat/` at setup; `merry_chat/` is generated, not source) |
| `merry_agent/tools/` | `deploy_root_app.py`, `build_tab.py`, `run.py`, `smoke_test.py`, `try_sample.py`, `reset.py` |
| `merry_agent/demo/` | The test harness the smoke test uses (`app_harness.py`, `st_stub.py`) |
| `merry_agent/data/` | Synthetic sample files only (a contract, invoices, field tickets, site-record CSVs) |
| `merry_agent/docs/` | PRODUCT.md, ARCHITECTURE.md, DECISIONS.md (DECISIONS.md overrides the other two) |

Generated at setup and not kept in the repository: `merry_chat/` and `snowflake.yml`.
