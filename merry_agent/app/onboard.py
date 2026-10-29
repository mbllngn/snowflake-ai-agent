# MERRY contract onboarding jobs (D25): state from APP.JOBS + CONTRACT.ONBOARD_LOG, and the progress block.
# Step 12c: moved out of streamlit_app.py so the Review conversation can show a dropped contract being read.
import streamlit as st

from common import current_user, kick_worker, q

STALL_MINUTES = 5          # longest step gap seen is ~30 s; AI_PARSE of a big PDF stays well under
ONBOARD_STEPS = ["Parse document", "Read header", "Split clauses", "Read contractors and terms (Exhibit B)",
                 "Read rates (Exhibit A)", "Read rule params", "Map roles to rates", "Save contract"]


# ---------------------------------------------------------------- onboarding jobs (D25)
# A Streamlit session's queries die with the browser tab (error 90230), so the app never runs a long CALL itself:
# it queues a row in APP.JOBS and runs EXECUTE TASK APP.TK_RUN_JOBS. Everything shown comes from JOBS and
# ONBOARD_LOG, so a reload changes nothing. A run with no log progress for STALL_MINUTES shows as interrupted.
def onboard_jobs():
    """Latest job per contract file (APP.JOBS) with its steps (ONBOARD_LOG; the SP clears a file's log at start)."""
    jobs = {}
    for r in q("""SELECT job_key, status, error, requested_at,
                         DATEDIFF('second', COALESCE(started_at, requested_at), CURRENT_TIMESTAMP()) age_s,
                         DATEDIFF('second', COALESCE(finished_at, requested_at), CURRENT_TIMESTAMP()) done_s
                  FROM {{DB}}.APP.JOBS WHERE kind = 'ONBOARD_CONTRACT'
                  QUALIFY ROW_NUMBER() OVER (PARTITION BY job_key ORDER BY requested_at DESC) = 1"""):
        jobs[r["JOB_KEY"]] = {"job": r["STATUS"], "error": r["ERROR"], "age_s": r["AGE_S"], "done_s": r["DONE_S"],
                              "steps": {}, "contract_id": None, "idle_s": None}
    # log ages on the SP's own clock: logged_at is CURRENT_TIMESTAMP() as NTZ in the account time zone
    for r in q("""SELECT source_file, contract_id, step_no, step, status, detail,
                         DATEDIFF('second', logged_at, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ) idle_s
                  FROM {{DB}}.CONTRACT.ONBOARD_LOG WHERE STARTSWITH(source_file, 'contracts/') OR STARTSWITH(source_file, '_inbox/')
                  ORDER BY source_file, logged_at, step_no"""):
        j = jobs.setdefault(r["SOURCE_FILE"], {"job": None, "error": None, "age_s": None, "done_s": None,
                                               "steps": {}, "contract_id": None, "idle_s": None})
        j["contract_id"] = r["CONTRACT_ID"] or j["contract_id"]
        j["steps"][r["STEP_NO"]] = {"step": r["STEP"], "status": r["STATUS"], "detail": r["DETAIL"]}
        j["idle_s"] = r["IDLE_S"]
    for j in jobs.values():
        sts = [s["status"] for s in j["steps"].values()]
        saved = any(s["step"] == "Save contract" and s["status"] == "OK" for s in j["steps"].values())
        idle = min(x for x in (j["idle_s"], j["age_s"]) if x is not None)
        if j["job"] == "FAILED" or "FAIL" in sts:
            j["state"] = "FAILED"
        elif j["job"] == "DONE" or (j["job"] is None and saved):
            j["state"] = "DONE"
        elif j["job"] == "INTERRUPTED" or idle > STALL_MINUTES * 60:
            j["state"] = "INTERRUPTED"            # RUNNING/QUEUED with no progress: never spin forever
        else:
            j["state"] = "QUEUED" if j["job"] == "QUEUED" and not j["steps"] else "RUNNING"
        j["recent_s"] = j["done_s"] if j["done_s"] is not None else j["idle_s"]
    return jobs


def queue_onboarding(path):
    q("""INSERT INTO {{DB}}.APP.JOBS (kind, job_key, args, status, requested_by, requested_at)
         SELECT 'ONBOARD_CONTRACT', ?, OBJECT_CONSTRUCT('stage_path', ?), 'QUEUED', ?, CURRENT_TIMESTAMP()""",
      [path, path, current_user()])


def render_job(path, j):
    state = j["state"]
    name = path.split("/")[-1]
    cid = f" → {j['contract_id']}" if j.get("contract_id") else ""
    label = {"QUEUED": f"Reading {name}: starting", "RUNNING": f"Reading {name}{cid}",
             "DONE": f"{name}{cid}: read", "FAILED": f"{name}: failed",
             "INTERRUPTED": f"{name}: interrupted (no progress for {STALL_MINUTES} min)"}[state]
    st_state = {"QUEUED": "running", "RUNNING": "running", "DONE": "complete"}.get(state, "error")
    with st.status(label, state=st_state, expanded=state != "DONE"):
        done = {s["step"]: s for s in j["steps"].values()}
        for name_ in ONBOARD_STEPS:
            s = done.get(name_)
            if s is None:
                st.markdown(f"○ {name_}")
            elif s["status"] == "OK":
                st.markdown(f"✓ **{name_}**: {s['detail'] or ''}")
            elif s["status"] == "RUNNING":
                st.markdown(("✗" if state == "INTERRUPTED" else "◐") + f" **{name_}**...")
            else:
                st.markdown(f"✗ **{name_}**: {s['detail']}")
        if j.get("error") and state in ("FAILED", "INTERRUPTED"):
            st.error(j["error"][:500])
        if state in ("FAILED", "INTERRUPTED"):
            st.caption("The stored contract is unchanged: a run saves everything in one step or nothing.")
            if st.button("Read again", key=f"again:{path}"):
                queue_onboarding(path)
                kick_worker()
                st.rerun(scope="app")
