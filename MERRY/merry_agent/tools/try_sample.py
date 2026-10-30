"""Try MERRY with the sample data pack, the way a new user would: everything goes through the app's own upload path
(the same code as the sidebar's Submit), in first-use order, then the month's audit runs as when you click
"Run the audit" in the chat.

    python3 merry_agent/tools/run.py merry_agent/tools/try_sample.py        # add --db NAME after run.py for another DB

1. Site records: data/out/*.csv (timesheets, gate log, daily reports, equipment log).
2. The service contract: data/out/contract_msa.pdf (read and, with no rate warnings, confirmed automatically).
3. Invoices and field tickets: data/out/invoices/*.pdf, data/out/tickets/*.pdf (sorted into service weeks by intake).
4. The audit of every service week the documents were filed under (intake already audits a week once its files are
   in; this runs whatever is left, as "Run the audit" in the chat would).
Writes to the database (it is a normal first use). Start over with tools/reset.py. Expected result with the sample
pack: 9 findings, PHP 287,000.00 upheld (check with smoke_test.py --findings 9 --upheld 287000)."""
import glob, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo"))
import app_harness as a

review, st, q = a.review, a.st, a.review.q
import merry_tab as t  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "out")
T0 = time.time()


class Upload:                                   # what st.file_uploader hands the app
    def __init__(self, path):
        self.name, self._d = os.path.basename(path), open(path, "rb").read()

    def getvalue(self):
        return self._d


def wait_jobs(label, limit=2400):
    """Wait until the worker has no queued or running jobs (kicking it as the app's poller does)."""
    while True:
        r = q("""SELECT kind, status, COUNT(*) n FROM {{DB}}.APP.JOBS WHERE status IN ('QUEUED', 'RUNNING')
                 GROUP BY 1, 2 ORDER BY 1""")
        if not r:
            print(f"{time.time() - T0:5.0f}s  {label}: done", flush=True)
            return
        print(f"{time.time() - T0:5.0f}s  {label}: " + ", ".join(f"{x['KIND']} {x['STATUS'].lower()} {x['N']}" for x in r),
              flush=True)
        if time.time() - T0 > limit:
            sys.exit(f"timed out waiting for {label}")
        a.common.kick_worker()
        time.sleep(20)


def submit(paths, label):
    n_pdf, msgs = t.feed_uploads([Upload(p) for p in paths], note=label)
    for m in msgs:
        print("   ", m)
    if n_pdf:
        print(f"    {n_pdf} PDF(s) passed to intake")
    wait_jobs(label)


st.session_state[t.KEY] = t.new_chat()
submit(sorted(glob.glob(os.path.join(DATA, "*.csv"))), "1. site records")
submit([os.path.join(DATA, "contract_msa.pdf")], "2. service contract")
c = q("SELECT contract_id, status FROM {{DB}}.CONTRACT.CONTRACTS ORDER BY confirmed_at DESC NULLS LAST LIMIT 1")
if not c:
    sys.exit("The contract was not read; see the chat and CONTRACT.ONBOARD_LOG.")
cid = c[0]["CONTRACT_ID"]
print(f"    contract {cid}: {c[0]['STATUS']}")
if c[0]["STATUS"] != "CONFIRMED":
    sys.exit("The contract has rate warnings and needs a review in the app before the audit (Review its values).")
submit(sorted(glob.glob(os.path.join(DATA, "invoices", "*.pdf")) + glob.glob(os.path.join(DATA, "tickets", "*.pdf"))),
       "3. invoices and field tickets")
periods = [str(r["P"]) for r in q("""SELECT DISTINCT target_period p FROM {{DB}}.APP.UPLOADS
                                    WHERE contract_id = ? AND intake_status = 'FILED' AND target_period IS NOT NULL
                                    ORDER BY 1""", [cid])]
print(f"    filed under {len(periods)} service week(s): {', '.join(periods)}")
done = {str(r["P"]) for r in q("""SELECT period_start p FROM {{DB}}.AUDIT.PERIOD_RUNS WHERE contract_id = ?
                                   AND status IN ('DRAFTED', 'REVIEWED') AND NOT COALESCE(is_stale, FALSE)""", [cid])}
todo = [p for p in periods if p not in done]          # intake already audits weeks whose files are complete
if todo:
    thread = cid if q("SELECT 1 FROM {{DB}}.APP.THREADS WHERE thread_id = ?", [cid]) else st.session_state[t.KEY]
    review.start_run(thread, cid, todo)
    wait_jobs("4. audit")
else:
    print("    4. audit: every week was already audited when its files were filed")
for r in q("SELECT period_start, status, current_step FROM {{DB}}.AUDIT.PERIOD_RUNS WHERE contract_id = ? ORDER BY 1", [cid]):
    print("   ", r["PERIOD_START"], r["STATUS"], r["CURRENT_STEP"])
k = q("SELECT findings, upheld_php, invoices FROM {{DB}}.APP.V_UI_KPIS")[0]
print(f"== sample tried in {time.time() - T0:.0f}s: {k['INVOICES']} invoices, {k['FINDINGS']} findings, "
      f"PHP {float(k['UPHELD_PHP'] or 0):,.2f} upheld")
