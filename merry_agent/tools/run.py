"""Run a MERRY tool or demo script against one database.

    python3 merry_agent/tools/run.py merry_agent/tools/smoke_test.py               # the database in settings.py
    python3 merry_agent/tools/run.py --db MERRY_TEST merry_agent/tools/try_sample.py

The scripts, like the app, are written with {{DB}} in place of the database name. This renders app/, demo/ and tools/
into /tmp/merry_run_<DB>/ for that database (data/ is linked, not copied), then runs the script from there."""
import os, re, runpy, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(ROOT))
import settings  # noqa: E402
args = sys.argv[1:]
db = settings.DB
if args[:1] == ["--db"]:
    db, args = args[1].upper(), args[2:]
assert re.fullmatch(r"[A-Z][A-Z0-9_]{0,200}", db), f"bad database name {db}"
assert args, __doc__
out = f"/tmp/merry_run_{db}"
shutil.rmtree(out, ignore_errors=True)
for d in ("app", "demo", "tools"):
    for root, _, files in os.walk(os.path.join(ROOT, d)):
        if "__pycache__" in root:
            continue
        dst = os.path.join(out, os.path.relpath(root, ROOT))
        os.makedirs(dst, exist_ok=True)
        for f in files:
            if f.endswith((".py", ".yml", ".sql")):
                open(os.path.join(dst, f), "w").write(settings.render(open(os.path.join(root, f)).read(), db))
os.symlink(os.path.join(ROOT, "data"), os.path.join(out, "data"))
rel = os.path.relpath(os.path.abspath(args[0]), ROOT) if os.path.exists(args[0]) else args[0]   # any cwd
script = os.path.join(out, rel)
sys.argv = [script] + args[1:]
sys.path.insert(0, os.path.dirname(script))
os.environ["MERRY_DB"] = db
runpy.run_path(script, run_name="__main__")
