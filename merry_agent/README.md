# merry_agent: MERRY's engine

This folder holds everything MERRY needs in one Snowflake database: the database objects, the chat, the setup and
the tools. Installing it in an account is described in [../SETUP.md](../SETUP.md); this page is for people changing it.

| Path | What it is |
|---|---|
| `setup.py` | The one setup command: `deploy.py`, then `tools/deploy_root_app.py`, then the smoke test |
| `deploy.py` | Creates or updates every object in the database named in `../settings.py` (or `--db`) |
| `sql/` | Tables (empty), reference rows, functions, views, procedures, the contract search service, the semantic view, the agent, the worker task, the role grants. Placeholders: `{{DB}}`, `{{ROLE}}`, `{{WAREHOUSE}}`, `{{COMPUTE_POOL}}`, `{{MODEL}}`, `{{CONTRACT_MODEL}}` |
| `app/` | The chat (`merry_tab.py`, `review.py`, `chat.py`, `common.py`, `onboard.py`) and `lib/merry_sql.py`, shared with the procedures |
| `tools/` | `deploy_root_app.py` (the app), `build_tab.py` (renders `app/` into `../merry_chat/`), `run.py` (runs a tool against a database), `smoke_test.py`, `try_sample.py` (the sample pack in first-use order, then the audit), `reset.py` (admin: back to the first-ever state) |
| `demo/` | `app_harness.py` and `st_stub.py`: load the app's own modules against a real session with a stand-in UI, for the tools |
| `data/` | Synthetic sample files (read-only) |
| `docs/` | PRODUCT.md, ARCHITECTURE.md, DECISIONS.md |

## Changing it
Edit `sql/` or `app/` (keep the placeholders; never write a database, role, warehouse or model name), deploy to a
test database first, run the smoke test, then deploy to the real one:

```
python3 merry_agent/setup.py --db MERRY_TEST
python3 merry_agent/tools/run.py --db MERRY_TEST merry_agent/tools/try_sample.py      # optional: the sample audit
python3 merry_agent/tools/run.py --db MERRY_TEST merry_agent/tools/smoke_test.py --findings 9 --upheld 287000
python3 merry_agent/setup.py
```

## Running it again keeps your data
- Tables are only created when missing; `deploy.py` refuses any statement that would drop, replace or empty a table,
  schema, database or stage.
- Reference rows (rule labels, status words, plain phrases) only get the rows that are missing.
- Views, procedures, functions, the semantic view, the agent, the task and the app are replaced; they hold no data.
- The contract search service is created once and then kept.

To prove it on a database: `smoke_test.py --snapshot /tmp/before.json`, run setup, then `smoke_test.py --compare
/tmp/before.json`.
