"""MERRY setup: the one command a new owner runs (see SETUP.md).

    python3 merry_agent/setup.py              # uses settings.py
    python3 merry_agent/setup.py --db NAME    # or another database (its role becomes NAME_APP unless MERRY_ROLE is set)

1. deploy.py: warehouse, database, schemas, stages, role, tables (empty), reference data, views, procedures, the
   contract search service, semantic view, agent, worker task, grants.
2. tools/deploy_root_app.py: the Streamlit app <DB>.APP.MERRY on the container runtime.
3. tools/smoke_test.py: checks the objects, the role, the app and the empty state.
Safe to run again: it never drops or empties a table (to start over, use tools/reset.py)."""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--db")
db = ap.parse_args().db
env = dict(os.environ, **({"MERRY_DB": db.upper()} if db else {}))
steps = [("database", [sys.executable, os.path.join(HERE, "deploy.py")]),
         ("app", [sys.executable, os.path.join(HERE, "tools", "deploy_root_app.py")]),
         ("smoke test", [sys.executable, os.path.join(HERE, "tools", "run.py")] + (["--db", db.upper()] if db else [])
          + [os.path.join(HERE, "tools", "smoke_test.py")])]
for name, cmd in steps:
    print(f"\n######## {name}", flush=True)
    if subprocess.run(cmd, env=env).returncode:
        sys.exit(f"setup stopped: the {name} step failed (see above and SETUP.md, Troubleshooting)")
print("\nMERRY is set up. Open Snowsight > Projects > Streamlit > MERRY.")
