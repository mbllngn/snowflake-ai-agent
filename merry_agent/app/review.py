# MERRY Review tab (step 12c, D27; threads D28): shared chat threads per contract (the first has thread_id = contract_id,
# later ones <contract_id>#<8 hex>; "_INTAKE" before any contract exists). Every file is dropped in the chat box: the app PUTs it to _inbox/<batch>/, records it in APP.UPLOADS with its
# SHA-256, and queues an INTAKE_FILES job (APP.SP_INTAKE_FILES) for the worker (D25). Runs, briefings and contract
# reading also happen in the worker, so a reload never stops them; this page only reads tables and polls.
# Each MERRY message is rendered fresh from the tables on every rerun (D12). The latest one ends with "What I can do
# next": chat.next_steps(), computed from table state only. Clicking a suggestion is the confirmation; money suggestions
# carry the resolution version they were computed for and are re-checked before anything is approved.
# Explain is served from stored data. Typed questions go to MERRY_AGENT in chat mode, as in 12a/12b.
import hashlib
import io
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import time

import pandas as pd
import streamlit as st

from chat import (INVOICE, mentioned, parties, RULE_LABEL, welcome_text, thread_title, STAGE_WORD, STATUS_WORD, briefing, briefing_markdown, one_by_one_text, range_data, batch_summary, fmt_periods, run_estimate, run_suggestions, fmt_range, load_labels, next_steps, pattern_line, php, short_name,
                  standalone)
from common import (POLL_SECS, STAGE, TOUCH_THREAD, current_user, flash, kick_worker, md_safe, peso, php_to_peso, q, session)
from onboard import onboard_jobs, render_job

AGENT_URL = "/api/v2/databases/{{DB}}/schemas/AGENT/agents/MERRY_AGENT:run"
AGENT_TIMEOUT_MS = 180000
INTAKE = "_INTAKE"
STALL_MINUTES = 15
RUN_STEPS = [("EXTRACT", "Read invoices and tickets"), ("CHECKS", "Match lines to rates, tickets and site records; run the billing checks"),
             ("REVIEW", "Review each flag against the contract and site records"),
             ("DRAFT", "Draft letters and payment recommendations")]
DEC_LABEL = {"CONFIRM": "Disputed in full", "CORRECT": "Corrected", "WITHHOLD": "On hold", "DISMISS": "Cleared"}
DEC_COLOR = {"CONFIRM": "red", "CORRECT": "red", "WITHHOLD": "violet", "DISMISS": "green"}
NEW_DEC = {"CONFIRM": "Dispute in full", "DISMISS": "Clear it (pay in full)", "WITHHOLD": "Hold until documents arrive",
           "CORRECT": "Correct to the contract amount"}
SITE_TABLES = [("GATE_LOG", "entry_date"), ("EQUIPMENT_LOG", "log_date"), ("TIMESHEETS", "work_date"),
               ("DAILY_REPORTS", "report_date")]


def j(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def as_plain(v):
    """Snowpark Row (and anything nested in it) -> plain Python: dicts, lists, scalars. VARIANT columns arrive as JSON
    text and are parsed when they hold an object or array."""
    if hasattr(v, "as_dict"):
        return {k: as_plain(x) for k, x in v.as_dict(recursive=True).items()}
    if isinstance(v, dict):
        return {k: as_plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [as_plain(x) for x in v]
    return v


def qd(sql, params=None):
    """q() for everything handed to chat.py: plain dicts, never Rows (chat.plain asserts it)."""
    return [as_plain(r) for r in q(sql, params)]


def low(r):
    """Snowpark Row -> dict with lower-case keys, VARIANT columns parsed."""
    d = {k.lower(): v for k, v in r.as_dict().items()}
    for k in ("intake",):
        if k in d:
            d[k] = j(d[k]) or {}
    return d


# ---------------------------------------------------------------- thread
def post(tid, role, msg_type, payload):
    q("""INSERT INTO {{DB}}.APP.CHAT_MESSAGES (thread_id, seq, role, msg_type, payload, created_at)
         SELECT ?, COALESCE((SELECT MAX(seq) FROM {{DB}}.APP.CHAT_MESSAGES WHERE thread_id = ?), 0) + 1, ?, ?,
                PARSE_JSON(?), CURRENT_TIMESTAMP()::TIMESTAMP_NTZ""",
      [tid, tid, role, msg_type, json.dumps(payload, default=str)])
    q(TOUCH_THREAD, [tid, current_user(), tid])          # D28: thread list order and first-thread creation


def messages(tid):
    return [{"seq": r["SEQ"], "role": r["ROLE"], "type": r["MSG_TYPE"], "p": j(r["P"]) or {}, "at": r["CREATED_AT"]}
            for r in q("""SELECT seq, role, msg_type, payload::STRING p, created_at FROM {{DB}}.APP.CHAT_MESSAGES
                          WHERE thread_id = ? ORDER BY seq""", [tid])]


def say(tid, text, **extra):
    post(tid, "AGENT", "TEXT", {"kind": "NOTE", "text": text, **extra})


def queue(kind, key, args):
    q("""INSERT INTO {{DB}}.APP.JOBS (kind, job_key, args, status, requested_by, requested_at)
         SELECT ?, ?, PARSE_JSON(?), 'QUEUED', ?, CURRENT_TIMESTAMP()""", [kind, key, json.dumps(args), current_user()])


# ---------------------------------------------------------------- dropping files (D27.2)
UPLOAD_THREADS = 8          # D27.15: Snowpark Session is thread-safe (1.24+, on by default); 60 files in about 2 s
STOPPED_AFTER_S = 120       # a drop whose files stopped arriving this long ago, with no sorting job, was cut off


def drop_files(tid, text, files, put=None):
    """D27.15: record the drop and its full file list first (USER message + UPLOADS rows marked UPLOADING), then save
    the files to the stage in parallel with a progress line, then hand the batch to the worker. A drop cut off by a
    closed tab leaves UPLOADING rows that stopped_drops() reports when the thread is next opened. Files that arrived
    in an earlier, stopped drop are merged into this one (matched by content), so re-dropping completes it."""
    csvs = [f for f in files or [] if f.name.lower().endswith(".csv")]
    if csvs:                                             # D32: site-record CSVs go to SP_LOAD_SITE_CSV, not intake
        load_site_csvs(tid, csvs, text)
        files = [f for f in files if f not in csvs]
    batch = uuid.uuid4().hex[:16]
    items, seen = [], set()
    for up in files or []:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", up.name)
        if not name.lower().endswith(".pdf"):
            flash("error", f"{up.name}: only PDF files can be added.")
            continue
        base, n = name, 1
        while name.lower() in seen:                      # same name twice in one drop: keep both
            n += 1
            name = re.sub(r"(\.pdf)$", f"_{n}\\1", base, flags=re.I)
        seen.add(name.lower())
        data = up.getvalue()
        items.append({"path": f"_inbox/{batch}/{name}", "name": name, "hash": hashlib.sha256(data).hexdigest(), "data": data})
    if not items:
        return
    names = [i["name"] for i in items]
    post(tid, "USER", "TEXT", {"text": text or "", "files": names, "batch_id": batch, "expected": len(items)})
    q("""INSERT INTO {{DB}}.APP.UPLOADS (file_path, file_name, content_hash, batch_id, intake_status, check_status,
                                       uploaded_by, uploaded_at, origin_thread)
         SELECT f.value:path::STRING, f.value:name::STRING, f.value:hash::STRING, ?, 'UPLOADING', 'INTAKE', ?,
                CURRENT_TIMESTAMP(), ? FROM TABLE(FLATTEN(PARSE_JSON(?))) f""",
      [batch, current_user(), tid, json.dumps([{k: i[k] for k in ("path", "name", "hash")} for i in items])])
    put = put or (lambda it: session.file.put_stream(io.BytesIO(it["data"]), f"{STAGE}/{it['path']}",
                                                     auto_compress=False, overwrite=True))
    bar = st.progress(0.0, text=f"Saving {len(items)} files… keep this tab open until I say I've got them.")
    saved, failed, t0 = [], [], time.time()
    with ThreadPoolExecutor(UPLOAD_THREADS) as ex:
        futs = {ex.submit(put, it): it for it in items}
        for f in as_completed(futs):
            try:
                f.result()
                saved.append(futs[f])
            except Exception:
                failed.append(futs[f])
            bar.progress(len(saved) / len(items), text=f"Saving {len(saved)} of {len(items)} files… keep this tab "
                                                         f"open until I say I've got them.")
    for it in list(failed):                              # one sequential retry for anything that failed in parallel
        try:
            put(it)
            saved.append(it)
            failed.remove(it)
        except Exception:
            pass
    secs = round(time.time() - t0, 1)
    q("""UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'NEW' WHERE batch_id = ? AND ARRAY_CONTAINS(file_path::VARIANT, PARSE_JSON(?))""",
      [batch, json.dumps([i["path"] for i in saved])])
    merged = merge_stopped(tid, batch, {i["hash"] for i in saved})
    bar.empty()
    post(tid, "AGENT", "TEXT", {"kind": "RECEIVED", "batch_id": batch, "n": len(saved) + merged, "merged": merged,
                                "missing": [i["name"] for i in failed], "upload_secs": secs, "threads": UPLOAD_THREADS})
    post(tid, "AGENT", "TEXT", {"kind": "INTAKE", "batch_id": batch, "n": len(saved) + merged})
    queue("INTAKE_FILES", batch, {"batch_id": batch})
    kick_worker()


def load_site_csvs(tid, files, text=None):
    """D32: each CSV is saved to _sitecsv/<batch>/ and loaded by APP.SP_LOAD_SITE_CSV (site records into RAW, duplicates
    ignored, source recorded; rate files refused). MERRY says what happened in the chat; returns the messages. A load
    that touches an audited period marks it stale, so the chat offers Re-run (never an automatic re-run). Returns
    (messages, contracts with a period marked stale)."""
    batch = uuid.uuid4().hex[:16]
    out, stale = [], set()
    for up in files:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", up.name)
        path = f"_sitecsv/{batch}/{name}"
        try:
            session.file.put_stream(io.BytesIO(up.getvalue()), f"{STAGE}/{path}", auto_compress=False, overwrite=True)
            r = j(q("CALL {{DB}}.APP.SP_LOAD_SITE_CSV(?, ?, ?, ?)", [path, up.name, batch, current_user()])[0][0]) or {}
            out.append(r.get("message") or f"{up.name}: {r.get('status')}")
            stale |= {c for c, _ in r.get("stale_periods") or []}
        except Exception as e:
            out.append(f"{up.name}: could not be loaded ({str(e)[:200]})")
    if tid:
        post(tid, "USER", "TEXT", {"text": text or "", "files": [f.name for f in files]})
        say(tid, "\n\n".join(out), site_csv=True, stale=sorted(stale))
    return out, stale


def stage_names(batch):
    """File names actually on the stage for one drop (the stage, not UPLOADS, says what arrived)."""
    return {r["name"].split("/")[-1] for r in q(f"LIST {STAGE}/_inbox/{batch}/")} if SAFE_BATCH.fullmatch(batch) else set()


SAFE_BATCH = re.compile(r"[0-9a-f]{16}")


def merge_stopped(tid, batch, hashes):
    """Files that arrived in an earlier stopped drop of this thread join the new batch, unless the new drop brought the
    same content again (then the old copy is excluded as REPLACED). Files that never arrived are marked NOT_RECEIVED."""
    old = q("""SELECT DISTINCT batch_id FROM {{DB}}.APP.UPLOADS WHERE origin_thread = ? AND batch_id <> ?
               AND intake_status IN ('STOPPED', 'UPLOADING') AND uploaded_at < DATEADD('second', ?, CURRENT_TIMESTAMP())""",
            [tid, batch, -STOPPED_AFTER_S])
    moved = 0
    for r in old:
        ob = r["BATCH_ID"]
        there = stage_names(ob)
        for u in q("""SELECT file_path, file_name, content_hash FROM {{DB}}.APP.UPLOADS WHERE batch_id = ?
                      AND intake_status IN ('STOPPED', 'UPLOADING')""", [ob]):
            if u["FILE_NAME"] not in there:
                st_ = ("NOT_RECEIVED", None)
            elif u["CONTENT_HASH"] in hashes:
                st_ = ("REPLACED", None)
            else:
                st_ = ("NEW", batch)
                moved += 1
            q("""UPDATE {{DB}}.APP.UPLOADS SET intake_status = ?, batch_id = COALESCE(?, batch_id),
                 excluded = IFF(? = 'NEW', excluded, TRUE) WHERE file_path = ?""", [st_[0], st_[1], st_[0], u["FILE_PATH"]])
    return moved


def stopped_drops(tid):
    """D27.15, run when the thread is opened: a drop with files still UPLOADING more than STOPPED_AFTER_S ago and no
    sorting job was cut off (the tab closed while saving). Say what arrived and what is missing, once per drop."""
    for r in q("""SELECT u.batch_id, COUNT(*) n FROM {{DB}}.APP.UPLOADS u WHERE u.origin_thread = ? AND u.intake_status = 'UPLOADING'
                  GROUP BY 1 HAVING MAX(u.uploaded_at) < DATEADD('second', ?, CURRENT_TIMESTAMP())
                     AND NOT EXISTS (SELECT 1 FROM {{DB}}.APP.JOBS j WHERE j.kind = 'INTAKE_FILES' AND j.job_key = u.batch_id)""",
               [tid, -STOPPED_AFTER_S]):
        b = r["BATCH_ID"]
        there = stage_names(b)
        rows = q("SELECT file_path, file_name FROM {{DB}}.APP.UPLOADS WHERE batch_id = ? ORDER BY file_name", [b])
        got = [u["FILE_NAME"] for u in rows if u["FILE_NAME"] in there]
        missing = [u["FILE_NAME"] for u in rows if u["FILE_NAME"] not in there]
        q("""UPDATE {{DB}}.APP.UPLOADS SET intake_status = IFF(ARRAY_CONTAINS(file_name::VARIANT, PARSE_JSON(?)), 'STOPPED', 'UPLOADING')
             WHERE batch_id = ? AND intake_status = 'UPLOADING'""", [json.dumps(got), b])
        if not q("""SELECT 1 FROM {{DB}}.APP.CHAT_MESSAGES WHERE thread_id = ? AND payload:kind::STRING = 'STOPPED_DROP'
                    AND payload:batch_id::STRING = ?""", [tid, b]):
            post(tid, "AGENT", "TEXT", {"kind": "STOPPED_DROP", "batch_id": b, "got": len(got), "expected": len(rows),
                                        "missing": missing})


def batch_rows(batch):
    return [low(r) for r in q("""SELECT file_path, file_name, intake_status, doc_kind, doc_no, contract_id, contractor_code,
                                        target_period, printed_start, printed_end, intake::STRING intake, excluded
                                 FROM {{DB}}.APP.UPLOADS WHERE batch_id = ? ORDER BY file_name""", [batch])]


def latest_job(kind, key):
    r = q("""SELECT job_id, status, error, result::STRING result, DATEDIFF('second', requested_at, CURRENT_TIMESTAMP()) age_s
             FROM {{DB}}.APP.JOBS WHERE kind = ? AND job_key = ? ORDER BY requested_at DESC LIMIT 1""", [kind, key])
    return r[0] if r else None


def site_records_through():
    r = q(" UNION ALL ".join(f"SELECT MAX({c}) d FROM {{DB}}.RAW.{t}" for t, c in SITE_TABLES))
    ds = [x["D"] for x in r if x["D"]]
    return min(ds) if ds else None


def contract_card(cid):
    c = q("""SELECT c.contract_id, c.title, c.status, c.confirmation_mode,
                    (SELECT COUNT(*) FROM {{DB}}.CONTRACT.CONTRACTORS k WHERE k.contract_id = c.contract_id) n_ctr,
                    (SELECT LISTAGG(contractor_name, ', ') WITHIN GROUP (ORDER BY contractor_code)
                       FROM {{DB}}.CONTRACT.CONTRACTORS k WHERE k.contract_id = c.contract_id) ctr_names,
                    (SELECT COUNT(*) FROM {{DB}}.CONTRACT.RATES r WHERE r.contract_id = c.contract_id) n_rates,
                    (SELECT COUNT(*) FROM {{DB}}.CONTRACT.RULE_PARAMS p WHERE p.contract_id = c.contract_id) n_params,
                    (SELECT COUNT(*) FROM {{DB}}.CONTRACT.CLAUSES l WHERE l.contract_id = c.contract_id
                        AND COALESCE(l.article, '') <> 'EXHIBIT') n_clauses,
                    (SELECT COUNT(*) FROM {{DB}}.CONTRACT.RATE_WARNINGS w WHERE w.contract_id = c.contract_id
                        AND w.severity = 'WARN') n_warn
             FROM {{DB}}.CONTRACT.CONTRACTS c WHERE c.contract_id = ?""", [cid])
    if not c:
        return
    c = c[0]
    st.markdown(f"| Contract | Contractors | Rates | Rule settings | Clauses | Warnings |\n|---|---:|---:|---:|---:|---|\n"
                f"| **{c['CONTRACT_ID']}**<br>{md_safe(c['TITLE'] or '')} | {c['N_CTR']} | {c['N_RATES']} | "
                f"{c['N_PARAMS']} | {c['N_CLAUSES']} | "
                + (f":orange-background[{c['N_WARN']} rate warning{'s' if c['N_WARN'] != 1 else ''}]" if c["N_WARN"]
                   else ":green-background[None]") + " |", unsafe_allow_html=True)
    st.caption(md_safe(c["CTR_NAMES"] or ""))
    if c["STATUS"] == "CONFIRMED" and c["CONFIRMATION_MODE"] == "AS_EXTRACTED_AUTO":
        st.markdown(f"**{cid}** has no rate warnings, so I confirmed it as extracted, automatically. You can still change "
                    f"any value in Documents.")
    elif c["STATUS"] == "CONFIRMED":
        st.markdown(f"**{cid}** is confirmed"
                    + (" after review." if c["CONFIRMATION_MODE"] == "REVIEWED" else " as extracted."))
    else:
        st.markdown(f"**{cid}** has {c['N_WARN']} rate warning{'s' if c['N_WARN'] != 1 else ''}, so I haven't confirmed "
                    f"it. Review its values, or confirm it as extracted if the warnings are expected.")


def render_intake(tid, cid, p, live):
    batch = p.get("batch_id")
    job = latest_job("INTAKE_FILES", batch)
    if job and job["STATUS"] in ("QUEUED", "RUNNING"):
        with st.status(f"Sorting {p.get('n')} file{'s' if p.get('n') != 1 else ''}", state="running", expanded=True):
            st.markdown("◐ Work out what each file is  \n○ Match each contractor to its contract  \n"
                        "○ Read billing periods and ticket dates")
            st.caption("Runs in the background: you can reload or close the page.")
        return
    if job and job["STATUS"] != "DONE":
        st.error(f"I couldn't sort these files: {(job['ERROR'] or job['STATUS'])[:300]}")
        return
    rows = batch_rows(batch)
    contracts_ = [r for r in rows if r["intake_status"] == "CONTRACT"]
    if contracts_:
        jobs = onboard_jobs()
        n = len(contracts_)
        st.markdown(f"{'This is a service contract' if n == 1 else f'{n} files are service contracts'}. "
                    + ("Here's what I read from it:" if n == 1 else "Here's what I read from them:"))
        for r in contracts_:
            oj = jobs.get(r["file_path"])
            if oj and oj["state"] != "DONE":
                render_job(r["file_path"], oj)
            elif r.get("contract_id"):
                contract_card(r["contract_id"])
            else:
                st.caption(f"{r['file_name']}: waiting to be read.")
    target = cid or next((r["contract_id"] for r in rows if r.get("contract_id")), None)
    summ = batch_summary(rows, target)
    for t in summ["text"]:
        st.markdown(md_safe(t))
    if summ["periods"]:
        thru = site_records_through()
        if thru:
            st.caption(f"Site records (gate log, equipment log, timesheets, daily reports) are up to date through "
                       f"{thru:%b} {thru.day}.")


def render_sorted(p):
    rows = batch_rows(p.get("batch_id"))
    t = [{"File": r["file_name"], "What it is": {"INVOICE": "Invoice", "TICKET": "Field ticket",
                                                 "CONTRACT": "Contract"}.get(r["doc_kind"], "Unknown"),
          "Contractor": r.get("contractor_code") or "", "Contract": r.get("contract_id") or "",
          "Dates covered": fmt_range([r["target_period"]]) if r.get("target_period") else "",
          "Filed": {"FILED": "Filed", "CONTRACT": "Read as a contract", "DUPLICATE": "Ignored (same content)",
                    "LEFT_OUT": "Left out", "REPLACED": "Replaced"}.get(r["intake_status"], "Waiting for you")}
         for r in rows]
    st.dataframe(pd.DataFrame(t), hide_index=True, width="stretch")
    st.caption("Printed dates decide the service period; nothing was moved. If one is wrong, tell me in the chat.")


# ---------------------------------------------------------------- range data
def range_resolutions(cid, periods):
    return {r["INVOICE_NO"]: r for r in q("""
        SELECT r.*, h.contractor_code, k.contractor_name
        FROM {{DB}}.AUDIT.V_RESOLUTIONS r
        LEFT JOIN {{DB}}.CURATED.INVOICE_HEADERS h ON h.contract_id = r.contract_id AND h.invoice_no = r.invoice_no
        LEFT JOIN {{DB}}.CONTRACT.CONTRACTORS k ON k.contract_id = r.contract_id AND k.contractor_code = h.contractor_code
        WHERE r.contract_id = ? AND ARRAY_CONTAINS(r.period_start::STRING::VARIANT, PARSE_JSON(?))
        ORDER BY r.period_start, r.invoice_no""", [cid, json.dumps(sorted(periods))])}


def range_decisions(cid, periods=None, invoice_no=None):
    out = {}
    for r in q("""SELECT e.exception_id, e.invoice_no, e.rule_type, e.summary, e.flagged_php, e.period_start, d.version,
                         d.decision, d.upheld_php, d.overbilled_php, d.withheld_php, d.allowed_php, d.clauses::STRING clauses,
                         d.evidence_refs::STRING refs, d.rationale, d.basis::STRING basis,
                         d.computation::STRING computation, d.decided_by, d.created_at, d.override_reason
                  FROM {{DB}}.AUDIT.EXCEPTIONS e
                  JOIN {{DB}}.AUDIT.V_DECISIONS d ON d.exception_id = e.exception_id AND d.is_current
                  WHERE e.contract_id = ? AND (ARRAY_CONTAINS(e.period_start::STRING::VARIANT, PARSE_JSON(?)) OR e.invoice_no = ?)
                  ORDER BY e.period_start, e.invoice_no, e.line_no""",
               [cid, json.dumps(sorted(periods or [])), invoice_no or ""]):
        out.setdefault(r["INVOICE_NO"], []).append(r)
    return out


def kind_of(r, decs):
    t = r["RESOLUTION_TYPE"]
    if t != "APPROVE":
        return {"DISPUTE": "dispute", "HOLD": "hold", "DISPUTE_AND_HOLD": "dispute and hold"}[t]
    return "cleared" if decs else "clean"


def status_chip(r, decs):
    k, ok = kind_of(r, decs), r["STATUS"] == "APPROVED"
    if ok:
        return {"dispute": ":red-background[Dispute approved]", "hold": ":violet-background[Hold approved]",
                "dispute and hold": ":red-background[Dispute and hold approved]"}.get(k, ":green-background[Approved]")
    return {"dispute": ":orange-background[Dispute drafted]", "hold": ":orange-background[Hold drafted]",
            "dispute and hold": ":orange-background[Dispute and hold drafted]",
            "cleared": ":blue-background[Flag cleared]"}.get(k, ":blue-background[Clean]")


def finding_text(d):
    """The check's own summary without its rule-code prefix and trailing amount; the rule wording from RULE_LABELS."""
    s = re.sub(r"^[A-Z_]+:\s*", "", d["SUMMARY"] or "")
    s = re.sub(r":\s*PHP\s*[\d,.\s]+$", "", s).strip()
    lbl = RULE_LABEL.get(d["RULE_TYPE"])
    return (lbl[0].upper() + lbl[1:] + (f" ({s})" if s else "")) if lbl else s


def cell(t):
    return md_safe(str(t or "")).replace("|", "\\|").replace("\n", " ")


def render_briefing(cid, p):
    """D27.13: plain opening sentence, one line per contractor, then a collapsed Details with the findings table, rule
    labels, decision codes and run timings. Rendered fresh from the tables on every rerun (D12)."""
    if p.get("kind") != "RANGE":                         # a 12a/12b per-period briefing (migrated thread)
        st.markdown(md_safe(php_to_peso(p.get("summary"))))
        st.caption(f"Earlier briefing, week of {p.get('period_start')}")
        return
    periods = p.get("periods") or []
    fails = p.get("failed") or []
    if fails:
        stopped = [f for f in fails if (f.get("step") or "") == "STOPPED"]
        broke = [f for f in fails if f not in stopped]
        msg = []
        if stopped:
            msg.append(f"I stopped before finishing {fmt_periods([f['period_start'] for f in stopped])}, as you asked.")
        if broke:
            msg.append(f"I couldn't finish {fmt_periods([f['period_start'] for f in broke])}: something went wrong "
                       + and_words(sorted({STAGE_WORD.get(f.get('step') or '', 'during the run') for f in broke})) + ".")
        if periods:
            msg.append(f"{fmt_periods(periods)} {'is' if len(periods) == 1 else 'are'} done.")
        st.warning(" ".join(msg))
    res, decs, facts = range_data(qd, cid, periods) if periods else ({}, {}, {})
    if periods:
        b = briefing(res, decs, facts, periods, p.get("patterns"), p.get("verify_flags"))
        st.markdown(md_safe(briefing_markdown(b)))
        n_ok = sum(1 for r in res.values() if r["STATUS"] == "APPROVED")
        if n_ok:
            st.caption(f"{n_ok} of {len(res)} approved so far.")
    with st.expander("Details"):
        render_briefing_details(cid, p, periods, fails, res, decs)


def and_words(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def render_briefing_details(cid, p, periods, fails, res, decs):
    for f in fails:
        st.markdown(f"- Not audited, {fmt_range([f['period_start']])}: stopped at {f.get('step') or f.get('status')}: "
                    f"{md_safe((f.get('error') or f.get('status') or '').replace('not audited: ', ''))}")
    ss = p.get("stage_seconds") or {}
    if ss.get("TOTAL"):
        cc = p.get("concurrency") or {}
        st.caption(f"Took {ss['TOTAL']:.0f} s (expected about {run_estimate(len(periods) + len(fails)):.0f} s): "
                   + " · ".join(f"{k.lower()} {ss[k]:.0f} s" for k in ("EXTRACT", "CHECKS", "REVIEW", "DRAFT") if ss.get(k) is not None)
                   + (f". Agent calls: {cc.get('calls', 0)} (at most {cc.get('max_in_flight', 0)} at once, limit "
                      f"{cc.get('max_concurrent')}), {cc.get('hedges', 0)} hedged, {cc.get('retries', 0)} retried, "
                      f"{cc.get('throttled_429', 0)} throttled." if cc else ""))
    if not periods:
        return
    allx = [d for v in decs.values() for d in v]
    billed = sum(float(r["BILLED_PHP"] or 0) for r in res.values())
    flagged = sum(float(d["FLAGGED_PHP"] or 0) for d in allx)
    st.caption(f"{php(billed)} billed. My checks flagged {len(allx)} item{'s' if len(allx) != 1 else ''} worth "
               f"{php(flagged)}; I reviewed each against the contract and the site records.")
    for pt in p.get("patterns") or []:
        occ = "; ".join(f"{o['invoice_no']} ({fmt_range([o['period_start']])})" for o in pt.get("occurrences") or [])
        st.caption(f"{md_safe(pattern_line(pt))}: {md_safe(occ)}.")
    for v in p.get("verify_flags") or []:
        st.caption(f"Verify {v.get('doc_no')}: {md_safe(v.get('detail'))}")
    if allx:
        lines = ["| Invoice | Check | Decision | Clauses | Amount |", "|---|---|---|---|---:|"]
        for d in allx:
            r = res.get(d["INVOICE_NO"])
            who = short_name(r["CONTRACTOR_NAME"]) if r is not None and r["CONTRACTOR_NAME"] else ""
            amt = php(d["UPHELD_PHP"])
            if d["DECISION"] in ("CORRECT", "DISMISS"):
                amt = f"~~{php(d['FLAGGED_PHP'])}~~ {amt}"
            note = f"<br>*Changed by analyst: {cell(d['OVERRIDE_REASON'])}*" if d["OVERRIDE_REASON"] else ""
            lines.append(f"| **{d['INVOICE_NO']}**<br>{cell(who)} | {cell(finding_text(d))}{note} | "
                         f"{d['DECISION']} ({STATUS_WORD.get(d['DECISION'], '')}) | "
                         f"{cell(', '.join(str(c) for c in (j(d['CLAUSES']) or [])))} | {amt} |")
        st.markdown("\n".join(lines), unsafe_allow_html=True)
    clean = [i for i in res if not decs.get(i)]
    if clean:
        st.caption("No issues on " + ", ".join(clean) + ".")


# ---------------------------------------------------------------- run progress (read from JOBS / PERIOD_RUNS)
def run_jobs(run_id):
    return q("""SELECT job_id, args:period_start::STRING ps, status, error, result:seconds:TOTAL::FLOAT secs,
                       result:seconds::STRING sx, requested_at, started_at, finished_at,
                       DATEDIFF('second', requested_at, CURRENT_TIMESTAMP()) age_s
                FROM {{DB}}.APP.JOBS WHERE kind = 'PROCESS_PERIOD' AND args:run_id::STRING = ?
                QUALIFY ROW_NUMBER() OVER (PARTITION BY args:period_start ORDER BY requested_at DESC) = 1
                ORDER BY ps""", [run_id])


def current_step(cid, jb):
    """Step of this run, not of the period's previous audit."""
    r = q("""SELECT r.current_step FROM {{DB}}.AUDIT.PERIOD_RUNS r, {{DB}}.APP.JOBS x
             WHERE r.contract_id = ? AND r.period_start = ?::DATE AND x.job_id = ?
               AND r.started_at >= DATEADD('second', -5, x.started_at::TIMESTAMP_NTZ)""", [cid, jb["PS"], jb["JOB_ID"]])
    return r[0]["CURRENT_STEP"] if r else None


def render_run(cid, p):
    """D27.13: one plain line while the audit runs ("Checking Aug 31 – Sep 27 now, about 4 minutes"); the per-period
    steps and timings are in Details. A run the worker started by itself (D27.14, payload.auto) says so."""
    jobs = run_jobs(p.get("run_id"))
    rng = fmt_range(p.get("periods") or [])
    pending = [x for x in jobs if x["STATUS"] in ("QUEUED", "RUNNING")]
    n = len(p.get("periods") or [])
    mins = max(1, round(run_estimate(n) / 60))
    lead = "Nothing needed your input, so I started right away. " if p.get("auto") else ""
    if pending:
        slow = any(x["STATUS"] == "RUNNING" and x["AGE_S"] > STALL_MINUTES * 60 for x in jobs)
        st.markdown(f"{lead}Checking {rng} now, about {mins} minute{'s' if mins > 1 else ''}. It keeps going if you "
                    f"close this page." + (" It's taking longer than usual." if slow else ""))
    elif any(x["STATUS"] == "CANCELLED" for x in jobs):
        st.markdown(f"{lead}I was checking {rng}; you stopped it.")
    else:
        st.markdown(f"{lead}Checked {rng}.")
    with st.expander("Details"):
        for x in jobs:
            lbl = fmt_range([x["PS"]])
            sx = j(x["SX"]) or {}
            if x["STATUS"] == "DONE":
                st.markdown(f"✓ **{lbl}**: " + " · ".join(f"{k.lower()} {sx[k]:.0f} s" for k, _ in RUN_STEPS
                                                          if sx.get(k) is not None))
            elif x["STATUS"] == "QUEUED":
                st.markdown(f"○ {lbl}: waiting")
            elif x["STATUS"] == "RUNNING":
                cur = current_step(cid, x) if x["STARTED_AT"] else None
                key = (cur or "EXTRACT").split()[0]
                idx = next((i for i, (k, _) in enumerate(RUN_STEPS) if k == key), 0)
                st.markdown(f"◐ **{lbl}**: " + " · ".join(("✓ " if i < idx else "◐ " if i == idx else "○ ") + s_
                                                          for i, (k, s_) in enumerate(RUN_STEPS)))
            else:
                st.markdown(f"✗ {lbl}: {(x['ERROR'] or x['STATUS'])[:200]}")


# ---------------------------------------------------------------- explain (stored data only)
@st.cache_data(show_spinner=False, ttl=3600)
def exception_context(exception_id, version):
    return j(q("CALL {{DB}}.AGENT.GET_EXCEPTION_CONTEXT(?)", [exception_id])[0][0])


def stored_trace(exception_id, version):
    r = q("""SELECT trace::STRING t FROM {{DB}}.AUDIT.REVIEW_CALLS WHERE exception_id = ? AND is_winner
             AND decision_version = ? ORDER BY started_at DESC LIMIT 1""", [exception_id, version])
    return j(r[0]["T"]) if r and r[0]["T"] else None


def clause_texts(cid, nos):
    if not nos:
        return []
    return q("""SELECT clause_no, title, article, clause_text FROM {{DB}}.CONTRACT.CLAUSES
                WHERE contract_id = ? AND ARRAY_CONTAINS(clause_no::VARIANT, PARSE_JSON(?)) ORDER BY clause_no""",
             [cid, json.dumps(nos)])


def basis_text(d):
    b, c = j(d["BASIS"]) or {}, j(d["COMPUTATION"])
    parts = [f"Flagged {peso(d['FLAGGED_PHP'])}"]
    if d["DECISION"] == "CORRECT":
        parts.append(f"allowed under the contract {peso(d['ALLOWED_PHP'])}")
    parts.append({"CONFIRM": f"overbilled {peso(d['OVERBILLED_PHP'])}", "CORRECT": f"overbilled {peso(d['OVERBILLED_PHP'])}",
                  "WITHHOLD": f"withheld {peso(d['WITHHELD_PHP'])}", "DISMISS": "nothing disputed"}[d["DECISION"]])
    s = " · ".join(parts)
    bt = (b or {}).get("basis") if isinstance(b, dict) else None
    if bt and bt != "NONE":
        extra = {k: v for k, v in b.items() if k != "basis"}
        s += f"  \nBasis: {bt.replace('_', ' ').lower()}" + (f" ({', '.join(f'{k} {v}' for k, v in extra.items())})" if extra else "")
    return s, c


def render_explain(cid, inv, decs):
    if not decs:
        st.markdown(f"{inv} has no findings: every line matched the contract and the site records.")
        return
    for d in decs:
        st.markdown(f"**{md_safe(d['SUMMARY'])}** · :{DEC_COLOR[d['DECISION']]}-background[{DEC_LABEL[d['DECISION']]}]")
        st.markdown(md_safe(php_to_peso(d["RATIONALE"])))
        s, comp = basis_text(d)
        st.markdown(s)
        if comp:
            with st.expander("Computation (SQL)"):
                st.json(comp)
        cl = [str(x) for x in (j(d["CLAUSES"]) or [])]
        for c in clause_texts(cid, cl):
            lbl = f"Exhibit {c['CLAUSE_NO']}" if c["ARTICLE"] == "EXHIBIT" else f"Clause {c['CLAUSE_NO']}"
            txt = (c["CLAUSE_TEXT"] or "").strip()
            st.markdown(f"> **{lbl} {c['TITLE'] or ''}.** " + md_safe(txt[:900] + ("…" if len(txt) > 900 else "")).replace("\n", "\n> "))
        refs = set(j(d["REFS"]) or [])
        ctx = exception_context(d["EXCEPTION_ID"], d["VERSION"]) or {}
        shown = False
        for name, rows in (ctx.get("site_records") or {}).items():
            sel = [x for x in rows if x.get("REF") in refs]
            if sel:
                st.caption(f"Cited {name.replace('_', ' ')}")
                st.dataframe(pd.DataFrame(sel).drop(columns=["REF"], errors="ignore"), hide_index=True,
                             width="stretch")
                shown = True
        tk = [x for x in ctx.get("tickets") or [] if x.get("REF") in refs]
        if tk:
            st.caption("Cited field tickets")
            st.dataframe(pd.DataFrame(tk).drop(columns=["REF"], errors="ignore"), hide_index=True, width="stretch")
            shown = True
        other = sorted(r for r in refs if not r.startswith(("RAW.", "CURATED.TICKETS")))
        if other:
            st.caption("Also cited: " + ", ".join(other))
        elif not shown:
            st.caption("Cited: " + ", ".join(sorted(refs)))
        tr = stored_trace(d["EXCEPTION_ID"], d["VERSION"])
        with st.expander(f"Review steps ({len(tr)} tool calls)" if tr else "Review steps"):
            if tr:
                for i, t in enumerate(tr, 1):
                    st.markdown(f"{i}. **{t.get('tool')}** `{(t.get('input') or '')[:300]}`")
            else:
                st.caption("This decision was recorded before tool steps were stored. It cites: "
                           + ", ".join(sorted(refs)))
            st.caption(f"Decision v{d['VERSION']} by {d['DECIDED_BY'].lower()}, {d['CREATED_AT']:%d %b %H:%M}")



# ---------------------------------------------------------------- letter (inline, editable)
def render_letter(r, live, firmer):
    inv, ver = r["INVOICE_NO"], r["VERSION"]
    if firmer:
        st.markdown("Firmer version. The amounts, the payment line, the clauses and \"partially disputed\" are unchanged.")
    else:
        st.markdown(f"Here's the {'hold notice' if r['RESOLUTION_TYPE'] == 'HOLD' else 'dispute'} for {inv}. "
                    f"It covers every finding on the invoice.")
    if not live:
        st.caption(f"Letter for {inv}: the latest copy is further down.")
        return
    if r["STATUS"] == "APPROVED":
        st.text(r["LETTER_TEXT"] or "")
        if r["PDF_PATH"]:
            st.success(f"Approved by {r['APPROVED_BY']} and saved as {r['PDF_PATH']}. Nothing was sent.")
            try:
                data = session.file.get_stream(f"{STAGE}/{r['PDF_PATH']}").read()
                st.download_button("Download PDF", data, file_name=r["PDF_PATH"].split("/")[-1],
                                   mime="application/pdf", key=f"pdf:{inv}:{ver}")
            except Exception as e:
                st.caption(f"PDF on stage; download unavailable here ({str(e).splitlines()[0][:120]}).")
        return
    st.text_area("Letter", r["LETTER_TEXT"] or "", height=380, key=f"lettertext:{inv}:{ver}", label_visibility="collapsed")
    st.caption("Amounts, the payment line and clause numbers are locked. Edit anything else directly; your edits are "
               "saved when you approve.")


# ---------------------------------------------------------------- conversation focus (D29.5)
def set_focus(tid, cid, action, invoice_no=None, contractor=None):
    """The thread's current subject (APP.THREAD_FOCUS), overwritten on every invoice-specific action and whenever a
    typed question names an invoice or contractor. The contractor of an invoice comes from its header."""
    if invoice_no and not contractor:
        r = q("""SELECT c.contractor_name n, h.contractor_code k FROM {{DB}}.CURATED.INVOICE_HEADERS h
                 LEFT JOIN {{DB}}.CONTRACT.CONTRACTORS c ON c.contract_id = h.contract_id AND c.contractor_code = h.contractor_code
                 WHERE h.contract_id = ? AND h.invoice_no = ?""", [cid, invoice_no])
        contractor = (short_name(r[0]["N"]) or r[0]["K"]) if r else None
    q("""MERGE INTO {{DB}}.APP.THREAD_FOCUS f USING (SELECT ? t, ? i, ? c, ? a) s ON f.thread_id = s.t
         WHEN MATCHED THEN UPDATE SET invoice_no = s.i, contractor = s.c, last_action = s.a,
              at = CURRENT_TIMESTAMP()::TIMESTAMP_NTZ
         WHEN NOT MATCHED THEN INSERT (thread_id, invoice_no, contractor, last_action, at)
              VALUES (s.t, s.i, s.c, s.a, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ)""", [tid, invoice_no, contractor, action])


def thread_focus(tid, cid):
    """{"invoice_no", "contractor", "fact"} for chat.standalone, or None. The fact is the focus's last action as a
    plain sentence from APP.V_DASH_ACTIVITY (records only; MERRY's answers are never used, D26)."""
    r = q("SELECT invoice_no, contractor, last_action, at FROM {{DB}}.APP.THREAD_FOCUS WHERE thread_id = ?", [tid])
    if not r:
        return None
    f = {"invoice_no": r[0]["INVOICE_NO"], "contractor": r[0]["CONTRACTOR"], "fact": None}
    kind = {"OVERRIDE": "OVERRIDE", "APPROVE": "APPROVAL"}.get(r[0]["LAST_ACTION"])
    if f["invoice_no"] and kind:
        a = q("""SELECT who, summary, why FROM {{DB}}.APP.V_DASH_ACTIVITY WHERE contract_id = ? AND subject = ? AND kind = ?
                 ORDER BY at DESC LIMIT 1""", [cid, f["invoice_no"], kind])
        if a:
            verb = "just changed" if kind == "OVERRIDE" else "just approved"
            f["fact"] = (f"{f['invoice_no']} is the invoice {a[0]['WHO']} {verb}: {a[0]['SUMMARY']}"
                         + (f". Reason given: {a[0]['WHY'].rstrip('. ')}" if a[0]["WHY"] else "") + ".")
    return f


def focus_from_text(tid, cid, text, focus):
    """A typed question that names an invoice (the last one named) or a contractor becomes the focus."""
    invs = INVOICE.findall(text or "")
    if invs:
        set_focus(tid, cid, "ASK", invs[-1])
        return
    try:
        who = sorted(mentioned(text, parties(qd, cid)))
    except Exception:
        who = []
    if who and (not focus or focus.get("contractor") not in who):
        set_focus(tid, cid, "ASK", None, who[-1])


# ---------------------------------------------------------------- agent chat (12a/12b behaviour)
def agent_request(body):
    """POST to the agent's :run endpoint. Warehouse runtime: _snowflake. Container runtime (the chat as a tab in the
    root app): requests with the connector session's token, same role and endpoint."""
    try:
        import _snowflake
    except ImportError:
        _snowflake = None
    if _snowflake:
        resp = _snowflake.send_snow_api_request("POST", AGENT_URL, {}, {}, body, None, AGENT_TIMEOUT_MS)
        if resp.get("status") != 200:
            raise RuntimeError(f"agent HTTP {resp.get('status')}: {str(resp.get('content'))[:300]}")
        return json.loads(resp["content"])
    import requests
    con = session.connection
    r = requests.post(f"https://{con.host}{AGENT_URL}", json=body, timeout=AGENT_TIMEOUT_MS / 1000,
                      headers={"Authorization": f'Snowflake Token="{con.rest.token}"',
                               "Content-Type": "application/json", "Accept": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"agent HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def call_agent(cid, periods, pairs, question):
    """pairs: earlier (question, answer) turns of this thread. The first user message carries the scope (chat mode):
    the contract and the weekly service periods of the latest audited range (D27.9)."""
    ps = sorted(date.fromisoformat(str(p)[:10]) for p in periods or [])
    if ps:
        spans = "; ".join(f"{p} to {p + timedelta(days=6)}" for p in ps)
        scope = (f"Chat mode. Contract {cid}, service period{'s' if len(ps) > 1 else ''} {spans}. Answer for this contract "
                 f"and {'these periods' if len(ps) > 1 else 'period'} unless I ask about history.")
    else:
        scope = f"Chat mode. Contract {cid}. No service period has been audited yet."
    msgs = []
    for qn, an in pairs[-3:]:
        msgs += [{"role": "user", "content": [{"type": "text", "text": qn}]},
                 {"role": "assistant", "content": [{"type": "text", "text": an}]}]
    msgs.append({"role": "user", "content": [{"type": "text", "text": question}]})
    me = current_user()
    scope += (f" I am {me}: call me \"you\", never by that user name. A decision made by {me} is mine (\"you\"); one "
              f"made by MERRY is yours (\"I\").")                 # D33: the person asking is "you"
    msgs[0]["content"][0]["text"] = scope + "\n\n" + msgs[0]["content"][0]["text"]
    body = {"messages": msgs, "stream": False}
    data = agent_request(body)
    text, steps, revisions, parts, actions = "", [], [], [], []      # parts: answer in order, text and result tables
    results = []                                                     # D34: raw tool results, for the Sources line

    def walk(o):
        if isinstance(o, dict):
            if o.get("status") == "PENDING" and o.get("revision_id"):
                revisions.append(o["revision_id"])
            if o.get("status") == "PENDING_ACTION" and o.get("action_id"):
                actions.append(o["action_id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and "revision_id" in o:
            try:
                walk(json.loads(o))
            except Exception:
                pass

    for c in data.get("content", []):
        if c.get("type") == "text":
            text += c.get("text", "")
            parts.append({"type": "text", "text": c.get("text", "")})
        elif c.get("type") == "table":
            rs = (c.get("table") or {}).get("result_set") or {}
            cols = [x.get("name") for x in ((rs.get("resultSetMetaData") or {}).get("rowType") or [])]
            parts.append({"type": "table", "columns": cols, "data": (rs.get("data") or [])[:200]})
        elif c.get("type") == "tool_use":
            tu = c.get("tool_use", {})
            steps.append({"tool": tu.get("name"), "input": json.dumps(tu.get("input"), default=str)[:600]})
        elif c.get("type") == "tool_result":
            tr = c.get("tool_result", {})
            walk(tr.get("content"))
            results.append(tr.get("content"))
            sql = None
            for x in tr.get("content") or []:
                if isinstance(x, dict) and isinstance(x.get("json"), dict) and x["json"].get("sql"):
                    sql = x["json"]["sql"]
            if sql and steps:
                steps[-1]["sql"] = sql[:1500]
    try:
        sources = collect_sources(results, cid)
    except Exception as e:                                           # sources never block the answer
        sources = {"error": str(e)[:200]}
    return text.strip(), steps, sorted(set(revisions)), parts, list(dict.fromkeys(actions)), sources



# D32: questions about everything ("all files", "status report") cover all contracts, grouped by contract, from the
# same view as the root app's Dashboard (APP.V_UI_KPIS_BY_CONTRACT), so the chat and the screens can't disagree.
EVERYTHING = re.compile(r"(?i)\b(status report|status update|overall status|where (are|do) we stand|where are we|"
                        r"everything|all (the |my |our )?(files|documents|contracts)|across (all )?contracts)\b")


def is_everything(text):
    return bool(EVERYTHING.search(text or ""))


def status_report(tid):
    rows = qd("""SELECT * FROM {{DB}}.APP.V_UI_KPIS_BY_CONTRACT WHERE invoices > 0 OR findings > 0 ORDER BY contract_id""")
    if not rows:
        say(tid, "There are no invoices yet. Drop invoices and field tickets here, and I'll audit them.")
        return
    lead = (f"Status of all {len(rows)} contracts with invoices, by contract:" if len(rows) > 1 else
            f"Only one contract has invoices, so this covers {rows[0]['CONTRACT_ID']}.")
    lines = [lead]
    for r in rows:
        lines.append(f"**{r['CONTRACT_ID']}**: {r['INVOICES']} invoices ({r['INVOICES_APPROVED']} approved, "
                     f"{r['INVOICES_WAITING']} waiting), {r['FINDINGS']} findings on {r['INVOICES_WITH_FINDINGS']} invoices "
                     f"({r['OPEN_FLAGS']} open, {r['CONFIRMED_FLAGS']} confirmed, {r['DISMISSED_FLAGS']} dismissed, "
                     f"{r['RECOVERED_FLAGS']} recovered); {php(r['UPHELD_PHP'])} upheld, {php(r['POTENTIAL_ISSUES'])} "
                     f"still open, {php(r['RECOVERED'])} recovered.")
    table = {"type": "table", "columns": ["Contract", "Invoices", "Approved", "Waiting", "Findings", "Open", "Confirmed",
                                          "Dismissed", "Recovered", "Upheld", "Open ₱", "Recovered ₱"],
             "data": [[r["CONTRACT_ID"], r["INVOICES"], r["INVOICES_APPROVED"], r["INVOICES_WAITING"], r["FINDINGS"],
                       r["OPEN_FLAGS"], r["CONFIRMED_FLAGS"], r["DISMISSED_FLAGS"], r["RECOVERED_FLAGS"],
                       php(r["UPHELD_PHP"]), php(r["POTENTIAL_ISSUES"]), php(r["RECOVERED"])] for r in rows]}
    text = "\n\n".join(lines)
    post(tid, "AGENT", "TEXT", {"kind": "ANSWER", "text": text, "parts": [{"type": "text", "text": text}, table],
                                "revision_ids": [], "action_ids": [], "periods": [],
                                "sources": {"views": ["Dashboard numbers (APP.V_UI_KPIS_BY_CONTRACT)"]}})


# ---------------------------------------------------------------- sources (D34)
# Every agent answer ends with a Sources line built from the agent's tool results only (never from its text). Each
# candidate is looked up in the database and dropped if not found. A decision row found in the results also brings its
# stored EVIDENCE_REFS, shown separately as "Records behind this decision".
REF = re.compile(r"\bRAW\.(GATE_LOG|EQUIPMENT_LOG|TIMESHEETS|DAILY_REPORTS):([A-Za-z0-9_\-]+(?:@\d{4}-\d{2}-\d{2})?)")
SKIP_KEYS = {"citable_refs", "clauses_index", "valid_bases", "custom_instructions", "relationships", "tables",
             "verified_queries", "sql"}          # context lists and the semantic model itself: not consulted rows
TABLE_WORD = {"EQUIPMENT_LOG": "Equipment log", "DAILY_REPORTS": "Daily report", "GATE_LOG": "Gate log",
              "TIMESHEETS": "Timesheet"}


def _rows_of(content):
    """Tool result content -> plain dict rows (result sets become one dict per row) and every string in it."""
    rows, strings = [], []

    def walk(o, key=None):
        if isinstance(o, str):
            if o[:1] in "[{":
                try:
                    return walk(json.loads(o), key)
                except Exception:
                    pass
            strings.append(o)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, dict):
            rs = o.get("result_set")
            if isinstance(rs, dict):
                cols = [(c.get("name") or "").upper() for c in (rs.get("resultSetMetaData") or {}).get("rowType") or []]
                for r in rs.get("data") or []:
                    d = dict(zip(cols, r))
                    rows.append(d)
                    walk([v for v in d.values() if isinstance(v, str)])
            rows.append({str(k).upper(): v for k, v in o.items() if not isinstance(v, (dict, list))})
            for k, v in o.items():
                if str(k).lower() not in SKIP_KEYS and k != "result_set":
                    walk(v, k)
    walk(content)
    return rows, strings


def _split(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    v = str(v).strip()
    if v.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(v)]
        except Exception:
            pass
    return [x.strip() for x in v.split(",") if x.strip()]


def _record_rows(refs):
    """refs like RAW.EQUIPMENT_LOG:PU-101@2026-09-10 -> {ref: [row dicts]} for those that exist."""
    out = {}
    for ref in sorted(set(refs)):
        m = REF.fullmatch(ref)
        if not m:
            continue
        t, key = m.group(1), m.group(2)
        k, _, d = key.partition("@")
        sql = {"EQUIPMENT_LOG": "SELECT * FROM {{DB}}.RAW.EQUIPMENT_LOG WHERE equipment_id = ? AND log_date = ?::DATE",
               "TIMESHEETS": "SELECT * FROM {{DB}}.RAW.TIMESHEETS WHERE worker_id = ? AND work_date = ?::DATE",
               "GATE_LOG": "SELECT * FROM {{DB}}.RAW.GATE_LOG WHERE gate_entry_id = ?",
               "DAILY_REPORTS": "SELECT * FROM {{DB}}.RAW.DAILY_REPORTS WHERE report_date = ?::DATE"}[t]
        params = [k, d] if t in ("EQUIPMENT_LOG", "TIMESHEETS") else [k]
        if t in ("EQUIPMENT_LOG", "TIMESHEETS") and not d:
            continue
        try:
            rows = [{c: (str(v) if v is not None else None) for c, v in as_plain(r).items()} for r in q(sql, params)]
        except Exception:
            rows = []
        if rows:
            out[ref] = rows[:5]
    return out


def _group_records(found):
    """{ref: rows} -> [{label, refs, rows}] grouped by table and subject, dates listed."""
    g = {}
    for ref, rows in found.items():
        t, key = REF.fullmatch(ref).group(1), REF.fullmatch(ref).group(2)
        subj, _, d = key.partition("@")
        if t == "DAILY_REPORTS":
            subj, d = "", subj
        elif t == "GATE_LOG":
            r0 = rows[0]
            subj, d = r0.get("SUBJECT_NAME") or r0.get("SUBJECT_ID") or subj, r0.get("ENTRY_DATE") or ""
        e = g.setdefault((t, subj), {"table": t, "subject": subj, "dates": set(), "refs": [], "rows": []})
        e["dates"].add(d)
        e["refs"].append(ref)
        e["rows"] += rows
    out = []
    for (t, subj), e in sorted(g.items()):
        ds = sorted(x for x in e["dates"] if x)
        when = ", ".join(f"{as_date_word(x)}" for x in ds)
        label = TABLE_WORD[t] + (f" {subj}" if subj else "") + (f", {when}" if when else "")
        out.append({"label": label, "table": t, "refs": e["refs"], "rows": e["rows"][:12]})
    return out


def as_date_word(d):
    try:
        x = date.fromisoformat(str(d)[:10])
        return f"{x:%b} {x.day}"
    except Exception:
        return str(d)


def collect_sources(results, cid):
    rows, strings = [], []
    for c in results:
        r, s = _rows_of(c)
        rows += r
        strings += s
    clause_nos, refs, invoices, tickets, exc_ids, acts = set(), set(), set(), set(), set(), []
    for r in rows:
        for k in ("CLAUSE_NO", "DECISION_CLAUSES", "CLAUSES"):
            for x in _split(r.get(k)):
                clause_nos.add(x)
        for k in ("INVOICE_NO", "SOURCE_DOC", "SUBJECT"):
            invoices |= set(INVOICE.findall(str(r.get(k) or "").upper()))
        for k in ("TICKET_NO",):
            if r.get(k):
                tickets.add(str(r[k]))
        for x in _split(r.get("TICKET_REFS")):
            tickets.add(x)
        if r.get("EXCEPTION_ID") and (r.get("DECISION") or r.get("VERSION") is not None):
            exc_ids.add(str(r["EXCEPTION_ID"]))
            invoices |= set(INVOICE.findall(str(r["EXCEPTION_ID"])))
        if r.get("KIND") and r.get("WHO") and (r.get("ACTIVITY_AT") or r.get("AT")):
            acts.append(r)
        if r.get("LOG_DATE") and r.get("EQUIPMENT_ID"):
            refs.add(f"RAW.EQUIPMENT_LOG:{r['EQUIPMENT_ID']}@{str(r['LOG_DATE'])[:10]}")
        if r.get("REPORT_DATE") and (r.get("OPS_STATUS") or r.get("WEATHER")):
            refs.add(f"RAW.DAILY_REPORTS:{str(r['REPORT_DATE'])[:10]}")
        if r.get("GATE_ENTRY_ID"):
            refs.add(f"RAW.GATE_LOG:{r['GATE_ENTRY_ID']}")
        if r.get("WORK_DATE") and r.get("WORKER_ID"):
            refs.add(f"RAW.TIMESHEETS:{r['WORKER_ID']}@{str(r['WORK_DATE'])[:10]}")
    for s in strings:
        refs |= {f"RAW.{m.group(1)}:{m.group(2)}" for m in REF.finditer(s)}
        tickets |= {x for x in re.findall(r"CURATED\.TICKETS:([A-Za-z0-9\-]+)", s)}

    out = {}
    if clause_nos:
        out["clauses"] = [{"no": r["CLAUSE_NO"], "title": r["TITLE"], "text": (r["CLAUSE_TEXT"] or "")[:1500]}
                          for r in qd("""SELECT clause_no, title, clause_text FROM {{DB}}.CONTRACT.CLAUSES
                                         WHERE contract_id = ? AND ARRAY_CONTAINS(clause_no::VARIANT, PARSE_JSON(?))
                                         ORDER BY TRY_TO_DOUBLE(clause_no) NULLS LAST, clause_no""",
                                      [cid, json.dumps(sorted(clause_nos))])]
    decs = []
    if exc_ids:
        decs = qd("""SELECT f.flag_id, f.source_doc, f.line_no, f.rule, f.decision_word, f.decided_by, f.decided_at::STRING at,
                            f.override_reason, d.evidence_refs::STRING refs, d.clauses::STRING cls
                     FROM {{DB}}.APP.V_UI_FLAGS f
                     JOIN {{DB}}.AUDIT.V_DECISIONS d ON d.exception_id = f.flag_id AND d.is_current
                     WHERE ARRAY_CONTAINS(f.flag_id::VARIANT, PARSE_JSON(?))""", [json.dumps(sorted(exc_ids))])
    me = (current_user() or "").lower()
    if decs:
        out["decisions"] = [{"label": f"Decision on {d['SOURCE_DOC']} line {d['LINE_NO']}",
                             "who": "MERRY" if d["DECIDED_BY"] == "MERRY" else
                                    ("you" if (d["DECIDED_BY"] or "").lower() == me else d["DECIDED_BY"]),
                             "at": d["AT"], "word": d["DECISION_WORD"], "rule": d["RULE"],
                             "reason": d["OVERRIDE_REASON"]} for d in decs]
    stored = {c for d in decs for c in (j(d["CLS"]) or [])} - {c["no"] for c in out.get("clauses", [])}
    if stored:                                     # clauses the decision rests on (stored with it), shown with its records
        out["behind_clauses"] = [{"no": r["CLAUSE_NO"], "title": r["TITLE"], "text": (r["CLAUSE_TEXT"] or "")[:1500]}
                                 for r in qd("""SELECT clause_no, title, clause_text FROM {{DB}}.CONTRACT.CLAUSES
                                                WHERE contract_id = ? AND ARRAY_CONTAINS(clause_no::VARIANT, PARSE_JSON(?))
                                                ORDER BY clause_no""", [cid, json.dumps(sorted(stored))])]
    direct = _record_rows(refs)
    if direct:
        out["records"] = _group_records(direct)
    behind = {r for d in decs for r in (j(d["REFS"]) or []) if REF.fullmatch(str(r))} - set(direct)
    behind = _record_rows(behind)
    if behind:
        out["behind"] = _group_records(behind)
    docs = []
    if invoices:
        docs += [{"kind": "Invoice", "no": r["INVOICE_NO"], "path": r["SOURCE_FILE"]}
                 for r in qd("""SELECT invoice_no, source_file FROM {{DB}}.CURATED.INVOICE_HEADERS
                                WHERE ARRAY_CONTAINS(invoice_no::VARIANT, PARSE_JSON(?)) ORDER BY invoice_no""",
                             [json.dumps(sorted(invoices))])]
    if tickets:
        docs += [{"kind": "Ticket", "no": r["TICKET_NO"], "path": r["SOURCE_FILE"]}
                 for r in qd("""SELECT ticket_no, source_file FROM {{DB}}.CURATED.TICKETS
                                WHERE ARRAY_CONTAINS(ticket_no::VARIANT, PARSE_JSON(?)) ORDER BY ticket_no""",
                             [json.dumps(sorted(tickets))])]
    if docs:
        out["documents"] = docs
    if acts:
        keys = {(str(a.get("KIND")), str(a.get("WHO")), str(a.get("ACTIVITY_AT") or a.get("AT"))[:19]) for a in acts}
        real = qd("""SELECT kind, who, at::STRING at, subject, summary FROM {{DB}}.APP.V_ACTIVITY
                     WHERE contract_id = ?""", [cid])
        out["activity"] = [{"kind": r["KIND"], "who": "you" if (r["WHO"] or "").lower() == me else r["WHO"],
                            "at": r["AT"], "subject": r["SUBJECT"], "summary": as_you(r["SUMMARY"])}
                           for r in real if (r["KIND"], r["WHO"], (r["AT"] or "")[:19]) in keys]
        if not out["activity"]:
            del out["activity"]
    return out


def doc_bytes(path):
    """The PDF's bytes, read by the app from DOCS.DOC_STAGE (cached per session). D35: no external URL, because a
    presigned URL made in the container runtime only works from inside Snowflake."""
    k = f"pdf:{path}"
    if k not in st.session_state:
        st.session_state[k] = session.file.get_stream(f"{STAGE}/{path}").read()
    return st.session_state[k]


def doc_link(d, key):
    """A document source: a popover that shows the PDF inline (st.pdf where the runtime has it) with a download button."""
    if not d.get("path"):
        st.caption(d["no"])
        return
    with st.popover(f"📄 {d['no']}", width="stretch"):
        try:
            data = doc_bytes(d["path"])
        except Exception as e:
            st.caption(f"{d['kind']} {d['no']}: the file could not be read ({str(e)[:120]}).")
            return
        st.download_button(f"Download {d['no']}.pdf", data, file_name=d["path"].split("/")[-1],
                           mime="application/pdf", key=key)
        if hasattr(st, "pdf"):
            try:
                st.pdf(data, height=520)
            except Exception:
                st.caption("Inline preview isn't available in this runtime; use Download.")

def render_sources(src, key):
    if not src:
        return
    st.caption("**Sources**")
    items = []
    for c in src.get("clauses") or []:
        items.append(("clause", f"Clause {c['no']} {c['title'] or ''}".strip(), c))
    for r in src.get("records") or []:
        items.append(("records", r["label"], r))
    for d in src.get("documents") or []:
        items.append(("doc", d["no"], d))
    for d in src.get("decisions") or []:
        items.append(("decision", d["label"], d))
    for a in src.get("activity") or []:
        items.append(("activity", f"{a['kind'].replace('_', ' ').capitalize()} · {a['who']} · {str(a['at'])[:10]}", a))
    for v in src.get("views") or []:
        items.append(("view", v, None))

    def chips(its, tag):
        for n in range(0, len(its), 4):
            cols = st.columns(4)
            for col, (kind, label, x) in zip(cols, its[n:n + 4]):
                with col:
                    if kind == "doc":
                        doc_link(x, f"dl:{key}:{tag}:{x['no']}")
                    elif kind == "view":
                        st.caption(label)
                    else:
                        with st.popover(label, width="stretch"):
                            if kind == "clause":
                                st.markdown(f"**Clause {x['no']} · {x['title'] or ''}**")
                                st.write(x["text"] or "(no text stored)")
                            elif kind == "records":
                                st.dataframe(pd.DataFrame(x["rows"]), hide_index=True)
                            elif kind == "decision":
                                st.markdown(f"**{x['label']}** ({x['rule']}): {x['word']}")
                                st.caption(f"Decided by {x['who']} on {str(x['at'])[:10]}"
                                           + (f". Reason: {x['reason']}" if x.get("reason") else ""))
                            else:
                                st.write(php_to_peso(x["summary"]))
    chips(items, "c")
    if src.get("behind") or src.get("behind_clauses"):
        st.caption("Records behind this decision")
        chips([("clause", f"Clause {c['no']} {c['title'] or ''}".strip(), c) for c in src.get("behind_clauses") or []]
              + [("records", r["label"], r) for r in src.get("behind") or []], "b")


def as_you(text):
    """D33 safety net: the person asking is "you" in every answer, never their raw user name (as in welcome back)."""
    me = (current_user() or "").strip()
    if not text or not me:
        return text
    t = re.sub(rf"(?i)\bby {re.escape(me)}\b", "by you", text)
    t = re.sub(rf"(?i)(^|(?<=[.!?]\s)|(?<=\n)|(?<=\*\*)){re.escape(me)}\b", "You", t)
    return re.sub(rf"(?i)\b{re.escape(me)}\b", "you", t)


def ask(tid, cid, periods, msgs, question, suggested=False):
    # Each question goes to the agent on its own, scoped to the contract and range (12a: no earlier turns). Follow-ups
    # that depend on earlier turns are first rewritten into a standalone question (12b, chat.standalone).
    if not suggested and is_everything(question):
        status_report(tid)
        return
    with st.spinner("MERRY is checking the contract and site records..."):
        try:
            focus = thread_focus(tid, cid)
            sent, mode = standalone(qd, msgs, question, cid, suggested, focus)
            focus_from_text(tid, cid, question, focus)
            if mode == "CLARIFY":
                say(tid, sent)
                return
            text, steps, revs, parts, acts, sources = call_agent(cid, periods, [], sent)
        except Exception as e:
            say(tid, f"The agent could not answer: {str(e)[:300]}")
            return
    text, parts = as_you(text), [dict(x, text=as_you(x["text"])) if x.get("type") == "text" else x for x in parts or []]
    post(tid, "AGENT", "TEXT", {"kind": "ANSWER", "text": text or "(no answer)", "parts": parts, "revision_ids": revs,
                                "action_ids": acts, "periods": periods, "sources": sources})
    post(tid, "AGENT", "TOOL_TRACE", {"steps": steps, "asked_as": sent if mode in ("REWRITTEN", "FALLBACK") else None,
                                      "periods": periods})


def render_answer(p, key=""):
    for part in p.get("parts") or [{"type": "text", "text": p.get("text")}]:
        if part["type"] == "table":
            st.dataframe(pd.DataFrame(part["data"], columns=part["columns"] or None), hide_index=True,
                         width="stretch")
        elif (part.get("text") or "").strip():
            st.markdown(md_safe(part["text"]))
    for aid in p.get("action_ids") or []:
        r = q("SELECT action, status, items::STRING it FROM {{DB}}.APP.PENDING_ACTIONS WHERE action_id = ?", [aid])
        if r:
            items = j(r[0]["IT"]) or []
            st.markdown(f"**Proposed: {action_label(r[0]['ACTION'], items)}** · {r[0]['STATUS'].lower()}")
            st.dataframe(pd.DataFrame([{"Invoice": i["invoice_no"], "Contractor": i.get("contractor_name"),
                                        "Dispute": php(i["disputed_php"]), "Hold": php(i["held_php"]),
                                        "Pay": php(i["pay_php"])} for i in items]), hide_index=True,
                         width="stretch")
    for rid in p.get("revision_ids") or []:
        r = q("""SELECT p.status, p.proposed::STRING pj, p.rationale, e.invoice_no, e.summary FROM {{DB}}.AUDIT.PENDING_REVISIONS p
                 JOIN {{DB}}.AUDIT.EXCEPTIONS e ON e.exception_id = p.exception_id WHERE p.revision_id = ?""", [rid])
        if r:
            pj = j(r[0]["PJ"]) or {}
            st.markdown(f"**Proposed revision on {r[0]['INVOICE_NO']}**: {DEC_LABEL.get(pj.get('decision'), pj.get('decision'))}, "
                        f"upheld {php(pj.get('upheld_php'))} · {r[0]['STATUS'].lower()}")
            st.caption(md_safe(r[0]["RATIONALE"]))
    render_sources(p.get("sources"), key)            # D34


def action_label(action, items):
    names = ", ".join(i["invoice_no"] for i in items)
    return {"APPROVE_PAYMENT": f"Approve {names} for payment",
            "APPROVE_DISPUTE": f"Approve {names} and save the dispute letters",
            "APPROVE_HOLD": f"Approve {names} and save the hold notices"}.get(action, f"{action} {names}")


# ---------------------------------------------------------------- change a decision (D26.4 via SP_OVERRIDE_DECISION)
def render_change_form(tid, cid, p, live):
    if not live:
        st.caption("Which finding, and what should it be?")
        return
    st.markdown("Which finding, and what should it be?")
    decs = [d for v in range_decisions(cid, p.get("periods") or []).values() for d in v
            if not p.get("invoice_no") or d["INVOICE_NO"] == p["invoice_no"]]
    if not decs:
        st.caption("No findings in this range.")
        return
    k = f"chg:{p.get('form_id')}"
    pick = st.selectbox("Finding", range(len(decs)), key=f"{k}:f", format_func=lambda i: (
        f"{decs[i]['INVOICE_NO']}: {finding_text(decs[i])} ({DEC_LABEL[decs[i]['DECISION']]}, {php(decs[i]['UPHELD_PHP'])})"))
    d = decs[pick]
    dec = st.selectbox("New decision", list(NEW_DEC), format_func=NEW_DEC.get, key=f"{k}:d:{d['EXCEPTION_ID']}")
    basis = {"basis": "USER_OVERRIDE"}
    if dec == "CORRECT":
        cur = j(d["BASIS"]) if d["DECISION"] == "CORRECT" else None
        bt = st.selectbox("Payable part is based on", ["STANDBY_EQUIPMENT", "TRAVEL_ALLOWANCE"],
                          index=1 if (cur or {}).get("basis") == "TRAVEL_ALLOWANCE" else 0, key=f"{k}:b:{d['EXCEPTION_ID']}",
                          format_func=lambda x: {"STANDBY_EQUIPMENT": "Standby rate for idle equipment",
                                                 "TRAVEL_ALLOWANCE": "Travel allowance"}[x])
        basis = {"basis": bt}
        if bt == "STANDBY_EQUIPMENT":
            ds = st.text_input("Standby dates (YYYY-MM-DD, comma-separated)", ", ".join((cur or {}).get("dates") or []),
                               key=f"{k}:dt:{d['EXCEPTION_ID']}")
            basis["dates"] = [x.strip() for x in ds.split(",") if x.strip()]
    reason = st.text_input("Reason (required)", key=f"{k}:r:{d['EXCEPTION_ID']}",
                           placeholder="e.g. Approved by email from the company rep")
    st.caption("Amounts are recalculated from the contract. The letter is redrafted, and your reason appears on the finding.")
    if st.button("Save decision", type="primary", key=f"{k}:save:{d['EXCEPTION_ID']}:{d['VERSION']}",
                 disabled=len(reason.strip()) < 10):
        inv = d["INVOICE_NO"]
        before = q("SELECT pay_php, status FROM {{DB}}.AUDIT.V_RESOLUTIONS WHERE contract_id = ? AND invoice_no = ?",
                   [cid, inv])
        with st.spinner("Recording your decision and redrafting the letter..."):
            r = j(q("CALL {{DB}}.APP.SP_OVERRIDE_DECISION(?, ?, ?, ?, ?)",
                    [d["EXCEPTION_ID"], dec, json.dumps(basis), reason.strip(), current_user()])[0][0])
        post(tid, "USER", "TEXT", {"text": f"{NEW_DEC[dec]}: {inv}. Reason: {reason.strip()}", "act": "override",
                                   "arg": d["EXCEPTION_ID"], "periods": p.get("periods")})
        if r.get("status") != "OVERRIDDEN":
            say(tid, f"I couldn't record that: {r.get('error')}", periods=p.get("periods"))
            return
        set_focus(tid, cid, "OVERRIDE", inv)
        after = q("SELECT pay_php FROM {{DB}}.AUDIT.V_RESOLUTIONS WHERE contract_id = ? AND invoice_no = ?", [cid, inv])
        post(tid, "AGENT", "TEXT", {"kind": "OVERRIDDEN", "invoice_no": inv, "decision": r.get("decision"),
                                    "pay_before": float(before[0]["PAY_PHP"]) if before else None,
                                    "pay_after": float(after[0]["PAY_PHP"]) if after else None,
                                    "was_approved": bool(before and before[0]["STATUS"] == "APPROVED"),
                                    "periods": p.get("periods")})
        st.rerun()
    if len(reason.strip()) < 10:
        st.caption("A reason of at least 10 characters is required.")


# ---------------------------------------------------------------- actions behind suggestions
def current_resolution(cid, inv):
    r = q("SELECT * FROM {{DB}}.AUDIT.V_RESOLUTIONS WHERE contract_id = ? AND invoice_no = ?", [cid, inv])
    return r[0] if r else None


def approve_one(tid, cid, inv, ver, periods):
    r = current_resolution(cid, inv)
    if r is None or r["VERSION"] != ver or r["STATUS"] != "DRAFT":
        say(tid, f"{inv} changed since that suggestion was shown"
            + (f" (it is now version {r['VERSION']}, {r['STATUS'].lower()})" if r is not None else "")
            + ". Nothing was approved; the suggestions below are current.", periods=periods)
        return None
    text = st.session_state.get(f"lettertext:{inv}:{ver}")
    a = j(q("CALL {{DB}}.AUDIT.SP_APPROVE_RESOLUTION(?, ?, ?)", [inv, text, current_user()])[0][0])
    if a.get("status") != "APPROVED":
        say(tid, f"{inv} was not approved: {a.get('error') or a.get('status')}"
            + (f" Added {a.get('added')}, missing {a.get('missing')}." if a.get("added") or a.get("missing") else ""),
            periods=periods)
        return None
    r = current_resolution(cid, inv)
    return {"invoice_no": inv, "type": r["RESOLUTION_TYPE"], "pay": float(r["PAY_PHP"]), "vat": float(r["PAY_VAT_PHP"]),
            "total": float(r["PAY_TOTAL_PHP"]), "pdf_path": a.get("pdf_path")}


def approve_all(tid, cid, items, periods):
    """D27.14 "Approve all as recommended": re-check every invoice's version and DRAFT status first; if any changed,
    approve nothing. Otherwise approve each resolution (SP_APPROVE_RESOLUTION saves its PDF) and report what was done."""
    changed = []
    for inv, ver in items:
        r = current_resolution(cid, inv)
        if r is None or r["VERSION"] != ver or r["STATUS"] != "DRAFT":
            changed.append(inv)
    if changed:
        say(tid, f"{and_words(changed)} changed since I offered that, so I didn't approve anything. The suggestion "
                 f"below has the current amounts.", periods=periods)
        return
    done = []
    with st.spinner(f"Approving {len(items)} invoices and saving the PDFs..."):
        for inv, ver in items:
            x = approve_one(tid, cid, inv, ver, periods)
            if x:
                done.append(x)
    post(tid, "AGENT", "TEXT", {"kind": "APPROVED_ALL", "items": done, "asked": len(items), "periods": periods})


def post_next_one(tid, cid, periods, first=False):
    """D27.14 one by one: the next DRAFT invoice with issues, as one short message."""
    res, decs, _ = range_data(qd, cid, periods)
    todo = [i for i, r in res.items() if decs.get(i) and r["STATUS"] == "DRAFT"]
    total = sum(1 for i in res if decs.get(i))
    if todo:
        post(tid, "AGENT", "TEXT", {"kind": "ONE", "invoice_no": todo[0], "left": len(todo), "total": total,
                                    "periods": periods})
    elif not first:
        say(tid, f"That's all {total} invoices with issues.", periods=periods)
    else:
        say(tid, "There are no invoices with issues left to go through.", periods=periods)


def stop_run(tid, run_id):
    """D27.14 Stop: periods not started are cancelled; a running range stops at its next stage or agent slot."""
    q("""UPDATE {{DB}}.APP.JOBS SET status = 'CANCELLED', finished_at = CURRENT_TIMESTAMP(), error = 'stopped by the analyst'
         WHERE kind = 'PROCESS_PERIOD' AND args:run_id::STRING = ? AND status = 'QUEUED'""", [run_id])
    q("""UPDATE {{DB}}.APP.JOBS SET args = OBJECT_INSERT(args, 'cancel_requested', TRUE, TRUE)
         WHERE kind = 'PROCESS_PERIOD' AND args:run_id::STRING = ? AND status = 'RUNNING'""", [run_id])
    left = q("""SELECT COUNT(*) n FROM {{DB}}.APP.JOBS WHERE kind = 'PROCESS_PERIOD' AND args:run_id::STRING = ?
                AND status IN ('QUEUED', 'RUNNING')""", [run_id])[0]["N"]
    if not left:                                   # nothing was running: the worker won't post the briefing itself
        cid = q("SELECT ANY_VALUE(args:contract_id::STRING) c FROM {{DB}}.APP.JOBS WHERE args:run_id::STRING = ?", [run_id])[0]["C"]
        queue("RUN_BRIEFING", f"{cid}|{run_id}", {"contract_id": cid, "run_id": run_id, "thread_id": tid})
        kick_worker()
    say(tid, "Stopping. Anything already finished stays; the rest won't be checked. You can run it again any time.")


def start_run(tid, cid, periods):
    c = q("SELECT status FROM {{DB}}.CONTRACT.CONTRACTS WHERE contract_id = ?", [cid])
    if not c or c[0]["STATUS"] != "CONFIRMED":
        say(tid, f"{cid} isn't confirmed yet, so I can't audit against it. Review or confirm it first.")
        return
    busy = q("""SELECT COUNT(*) n FROM {{DB}}.APP.JOBS WHERE kind IN ('PROCESS_PERIOD', 'POST_AUDIT') AND status IN ('QUEUED', 'RUNNING')
                AND ARRAY_CONTAINS(job_key::VARIANT, PARSE_JSON(?))""", [json.dumps([f"{cid}|{p}" for p in periods])])[0]["N"]
    if busy:
        say(tid, "An audit for these dates is already running.")
        return
    run_id = uuid.uuid4().hex
    for ps in sorted(periods):
        queue("PROCESS_PERIOD", f"{cid}|{ps}", {"contract_id": cid, "period_start": ps, "force": False, "run_id": run_id,
                                                  "thread_id": tid})
    post(tid, "AGENT", "DIVIDER", {"label": fmt_range(periods), "periods": sorted(periods)})
    post(tid, "AGENT", "RUN", {"run_id": run_id, "periods": sorted(periods)})
    kick_worker()


def reintake(path, field, value):
    q(f"""UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'NEW', intake = OBJECT_INSERT(COALESCE(intake, OBJECT_CONSTRUCT()),
          '{field}', ?::VARIANT, TRUE) WHERE file_path = ?""", [value, path])
    b = q("SELECT batch_id FROM {{DB}}.APP.UPLOADS WHERE file_path = ?", [path])[0]["BATCH_ID"]
    queue("INTAKE_FILES", b, {"batch_id": b})
    kick_worker()


def do_act(tid, cid, s, state, msgs):
    act, arg = s["act"], s["arg"]
    periods = state.get("range_periods") or []
    text = s["label"] + (f": {s['amt']}" if s.get("amt") else "")
    post(tid, "USER", "TEXT", {"text": text, "act": act, "arg": arg if isinstance(arg, (str, type(None))) else
                               json.dumps(arg), "periods": arg if act == "run" else periods})
    inv = {"explain": arg, "why": arg, "letter": arg, "firmer": arg, "change_one": arg,
           "approve": (arg or [None])[0] if isinstance(arg, list) else None,
           "approve_next": (arg or [None])[0] if isinstance(arg, list) else None}.get(act)
    if inv:                                            # D29.5: invoice-specific actions set the thread's focus
        set_focus(tid, cid, {"explain": "EXPLAIN", "why": "EXPLAIN", "letter": "LETTER", "firmer": "LETTER",
                             "change_one": "CHANGE_FORM"}.get(act, "CLICK"), inv)
    if act == "run":
        start_run(tid, cid, arg)
    elif act == "sorted":
        post(tid, "AGENT", "TEXT", {"kind": "SORTED", "batch_id": arg})
    elif act in ("explain", "why"):
        post(tid, "AGENT", "TEXT", {"kind": "EXPLAIN", "invoice_no": arg, "periods": periods})
    elif act == "letter":
        post(tid, "AGENT", "LETTER", {"invoice_no": arg, "periods": periods})
    elif act == "firmer":
        with st.spinner("Revising the wording..."):
            rv = j(q("CALL {{DB}}.AUDIT.SP_REVISE_LETTER(?, ?)", [arg, "Make it firmer"])[0][0])
        if rv.get("status") == "REVISED":
            post(tid, "AGENT", "LETTER", {"invoice_no": arg, "firmer": True, "periods": periods})
        else:
            say(tid, f"I couldn't revise {arg}: {rv.get('error')}", periods=periods)
    elif act == "audit_partial":
        n = q("""UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'NEW' WHERE batch_id = ? AND intake_status = 'STOPPED'""",
              [arg])[0][0]
        q("""UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'NOT_RECEIVED', excluded = TRUE
             WHERE batch_id = ? AND intake_status = 'UPLOADING'""", [arg])
        post(tid, "AGENT", "TEXT", {"kind": "INTAKE", "batch_id": arg, "n": n})
        queue("INTAKE_FILES", arg, {"batch_id": arg})
        kick_worker()
    elif act == "approve_all":
        approve_all(tid, cid, arg, periods)
    elif act == "one_by_one":
        post_next_one(tid, cid, periods, first=True)
    elif act == "approve_next":
        with st.spinner("Approving and saving..."):
            x = approve_one(tid, cid, arg[0], arg[1], periods)
        if x:
            set_focus(tid, cid, "APPROVE", arg[0])
            post(tid, "AGENT", "TEXT", {"kind": "APPROVAL", "items": [x], "periods": periods})
            post_next_one(tid, cid, periods)
    elif act == "change_one":
        post(tid, "AGENT", "TEXT", {"kind": "CHANGE_FORM", "form_id": uuid.uuid4().hex[:8], "periods": periods,
                                    "invoice_no": arg})
    elif act == "stop":
        stop_run(tid, arg)
    elif act in ("approve", "approve_many"):
        items = [arg] if act == "approve" else arg
        with st.spinner("Approving..." if len(items) > 1 else "Approving and saving..."):
            done = [x for x in (approve_one(tid, cid, i, ver, periods) for i, ver in items) if x]
        if done and act == "approve":
            set_focus(tid, cid, "APPROVE", done[0]["invoice_no"])
        if done:
            post(tid, "AGENT", "TEXT", {"kind": "APPROVAL", "items": done, "periods": periods})
    elif act == "ask":
        focus_from_text(tid, cid, arg, thread_focus(tid, cid))     # a clicked question naming a subject
        ask(tid, cid, periods, msgs, arg, suggested=True)
    elif act == "change":
        post(tid, "AGENT", "TEXT", {"kind": "CHANGE_FORM", "form_id": uuid.uuid4().hex[:8], "periods": arg or periods})
    elif act in ("action_yes", "action_no"):
        x = j(q("CALL {{DB}}.APP.SP_EXECUTE_ACTION(?, ?, ?)", [arg, act == "action_yes", current_user()])[0][0])
        if act == "action_yes":
            ok = [o["invoice_no"] for o in (x.get("results") or []) if o.get("status") == "APPROVED"]
            bad = [o["invoice_no"] for o in (x.get("results") or []) if o.get("status") != "APPROVED"]
            items = []
            for inv in ok:
                r = current_resolution(cid, inv)
                items.append({"invoice_no": inv, "type": r["RESOLUTION_TYPE"], "pay": float(r["PAY_PHP"]),
                              "vat": float(r["PAY_VAT_PHP"]), "total": float(r["PAY_TOTAL_PHP"]), "pdf_path": r["PDF_PATH"]})
            if items:
                post(tid, "AGENT", "TEXT", {"kind": "APPROVAL", "items": items, "periods": periods})
            if bad:
                say(tid, "Not approved, because they changed since I proposed it: " + ", ".join(bad), periods=periods)
        else:
            say(tid, "Done: nothing was approved.", periods=periods)
    elif act in ("revision_yes", "revision_no"):
        a = j(q("CALL {{DB}}.APP.SP_ACCEPT_REVISION(?, ?, ?)", [arg, act == "revision_yes", current_user()])[0][0])
        say(tid, ("Revised decision recorded; I redrafted the letter." if a.get("status") == "ACCEPTED" else
                  f"I couldn't apply it: {a.get('error')}") if act == "revision_yes" else "Kept the current decision.",
            periods=periods)
    elif act == "write_briefing":
        queue("RUN_BRIEFING", f"{cid}|{arg}", {"contract_id": cid, "run_id": arg, "thread_id": tid})
        kick_worker()
    elif act == "file_under":
        path, ps = arg
        q("""UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'FILED', target_period = ?::DATE, period_start = ?::DATE,
             excluded = FALSE WHERE file_path = ?""", [ps, ps, path])
        q("""UPDATE {{DB}}.AUDIT.PERIOD_RUNS SET is_stale = TRUE WHERE contract_id = (SELECT contract_id FROM {{DB}}.APP.UPLOADS
             WHERE file_path = ?) AND period_start = ?::DATE AND status IN ('DRAFTED', 'REVIEWED')""", [path, ps])
        say(tid, f"Filed {path.split('/')[-1]} under {fmt_range([ps])}.")
    elif act == "leave_out":
        q("UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'LEFT_OUT', excluded = TRUE WHERE file_path = ?", [arg])
        say(tid, f"Left {arg.split('/')[-1]} out. It won't be audited.")
    elif act == "set_type":
        reintake(arg[0], "forced_type", arg[1])
        post(tid, "AGENT", "TEXT", {"kind": "INTAKE", "batch_id": q("SELECT batch_id FROM {{DB}}.APP.UPLOADS WHERE file_path = ?",
                                                                     [arg[0]])[0]["BATCH_ID"], "n": 1})
    elif act == "set_contract":
        reintake(arg[0], "forced_contract", arg[1])
        post(tid, "AGENT", "TEXT", {"kind": "INTAKE", "batch_id": q("SELECT batch_id FROM {{DB}}.APP.UPLOADS WHERE file_path = ?",
                                                                     [arg[0]])[0]["BATCH_ID"], "n": 1})
    elif act == "replace":
        u = q("SELECT intake::STRING it FROM {{DB}}.APP.UPLOADS WHERE file_path = ?", [arg])[0]
        it = j(u["IT"]) or {}
        old, route = (it.get("replaces") or {}).get("file_path"), it.get("route") or {}
        q("UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'REPLACED', excluded = TRUE WHERE file_path = ?", [old])
        q("""UPDATE {{DB}}.APP.UPLOADS SET intake_status = ?, target_period = ?::DATE, period_start = ?::DATE WHERE file_path = ?""",
          [route.get("status") or "ASK_DATES", route.get("target_period"), route.get("target_period"), arg])
        for p_ in {(it.get("replaces") or {}).get("period"), route.get("target_period")} - {None}:
            q("""UPDATE {{DB}}.AUDIT.PERIOD_RUNS SET is_stale = TRUE WHERE contract_id = (SELECT contract_id FROM {{DB}}.APP.UPLOADS
                 WHERE file_path = ?) AND period_start = ?::DATE AND status IN ('DRAFTED', 'REVIEWED')""", [arg, p_])
        say(tid, f"Replaced {arg.split('/')[-1]}: the new copy is filed and the old one is excluded.")
    elif act == "keep_existing":
        q("UPDATE {{DB}}.APP.UPLOADS SET intake_status = 'DUPLICATE', excluded = TRUE WHERE file_path = ?", [arg])
        say(tid, f"Kept the {arg.split('/')[-1]} already filed; I ignored the new copy.")
    elif act == "confirm_contract":
        r = j(q("CALL {{DB}}.CONTRACT.SP_CONFIRM_CONTRACT(?, ?, 'AS_EXTRACTED_ACK')", [arg, current_user()])[0][0])
        say(tid, r.get("error") or f"{arg} is confirmed as extracted, with its rate warnings acknowledged.")
    elif act == "review_values":
        st.session_state["want_contract"], st.session_state["want_tab"] = arg, "Documents"
    elif act == "open_contract":
        st.session_state["want_contract"] = arg
    elif act == "tab":
        st.session_state["want_tab"] = arg


# ---------------------------------------------------------------- state for the suggestions (D27.7)
def contract_latest(cid, msg_type):
    """D28: the latest RANGE briefing (or RUN) of the contract across all its threads."""
    kind = "AND payload:kind::STRING = 'RANGE'" if msg_type == "BRIEFING" else ""
    r = q(f"""SELECT thread_id, seq, payload::STRING p, created_at FROM {{DB}}.APP.CHAT_MESSAGES
               WHERE (thread_id = ? OR STARTSWITH(thread_id, ? || '#')) AND msg_type = ? {kind}
               ORDER BY created_at DESC, seq DESC LIMIT 1""", [cid, cid, msg_type])
    return {"seq": r[0]["SEQ"], "thread_id": r[0]["THREAD_ID"], "type": msg_type, "p": j(r[0]["P"]) or {},
            "at": r[0]["CREATED_AT"]} if r else None


def thread_batches(msgs):
    return [m["p"]["batch_id"] for m in msgs if m["type"] == "TEXT" and m["p"].get("kind") == "INTAKE"]


def next_state(tid, cid, msgs):
    st_ = {"done": set()}
    for m in msgs:
        if m["role"] == "USER" and m["p"].get("act"):
            st_["done"].add((m["p"]["act"], m["p"].get("arg")))
    batches = list(dict.fromkeys(thread_batches(msgs)))
    busy = q("""SELECT COUNT(*) n FROM {{DB}}.APP.JOBS WHERE status IN ('QUEUED', 'RUNNING') AND (
                  (kind = 'INTAKE_FILES' AND ARRAY_CONTAINS(job_key::VARIANT, PARSE_JSON(?)))
               OR (kind = 'ONBOARD_CONTRACT' AND ARRAY_CONTAINS(args:batch_id, PARSE_JSON(?)))
               OR (kind IN ('PROCESS_PERIOD', 'RUN_BRIEFING') AND args:contract_id::STRING = ?))""",
             [json.dumps(batches), json.dumps(batches), cid or ""])[0]["N"]
    open_stop = [m["p"] for m in msgs if m["p"].get("kind") == "STOPPED_DROP"]
    if open_stop:
        left = {r["BATCH_ID"]: r["N"] for r in q("""SELECT batch_id, COUNT(*) n FROM {{DB}}.APP.UPLOADS WHERE intake_status = 'STOPPED'
                AND ARRAY_CONTAINS(batch_id::VARIANT, PARSE_JSON(?)) GROUP BY 1""", [json.dumps([p["batch_id"] for p in open_stop])])}
        st_["stopped"] = [{"batch_id": b, "got": n} for b, n in left.items()]
    st_["busy"] = bool(busy)
    if busy:
        run = q("""SELECT args:run_id::STRING r FROM {{DB}}.APP.JOBS WHERE kind = 'PROCESS_PERIOD' AND args:contract_id::STRING = ?
                   AND status IN ('QUEUED', 'RUNNING') AND args:run_id IS NOT NULL AND NOT COALESCE(args:cancel_requested::BOOLEAN, FALSE)
                   ORDER BY requested_at DESC LIMIT 1""", [cid or ""])
        st_["stoppable"] = run[0]["R"] if run else None
        return st_
    rows = [low(r) for r in q("""SELECT file_path, file_name, intake_status, doc_kind, doc_no, contract_id, target_period,
                                        printed_start, printed_end, intake::STRING intake, batch_id
                                 FROM {{DB}}.APP.UPLOADS WHERE ARRAY_CONTAINS(batch_id::VARIANT, PARSE_JSON(?))""",
                              [json.dumps(batches)])]
    st_["asks"] = [r for r in rows if (r["intake_status"] or "").startswith("ASK_")]
    last_batch = batches[-1] if batches else None
    lb = [r for r in rows if r["batch_id"] == last_batch]
    st_["ask_periods"] = sorted({str(r["target_period"]) for r in lb if r.get("target_period")})
    cons = sorted({r["contract_id"] for r in rows if r["intake_status"] == "CONTRACT" and r.get("contract_id")})
    if cons:
        cs = {r["CONTRACT_ID"]: r for r in q("""SELECT c.contract_id, c.status, c.confirmation_mode,
                (SELECT COUNT(*) FROM {{DB}}.CONTRACT.RATE_WARNINGS w WHERE w.contract_id = c.contract_id AND w.severity = 'WARN') w
                FROM {{DB}}.CONTRACT.CONTRACTS c WHERE ARRAY_CONTAINS(c.contract_id::VARIANT, PARSE_JSON(?))""", [json.dumps(cons)])}
        st_["confirm"] = [{"contract_id": c, "warnings": cs[c]["W"]} for c in cons if c in cs and cs[c]["STATUS"] != "CONFIRMED"]
        st_["review_offer"] = [c for c in cons if c in cs and cs[c]["CONFIRMATION_MODE"] == "AS_EXTRACTED_AUTO"]
        st_["open_contract"] = [c for c in cons if c != cid and c in cs and ("open_contract", c) not in st_["done"]]
    # run suggestions (D27.11): every filed period of the contract without a current audit, from any batch
    i_batch = max([k for k, m in enumerate(msgs) if m["p"].get("kind") == "INTAKE" and m["p"].get("batch_id") == last_batch]
                  or [-1])
    if cid and last_batch and not any(m["type"] == "RUN" for m in msgs[i_batch + 1:]):
        st_["sorted_batch"] = last_batch
    if cid:
        filed = {}
        for r in q("""SELECT target_period::STRING ps, doc_kind, COUNT(*) n FROM {{DB}}.APP.UPLOADS
                      WHERE contract_id = ? AND intake_status = 'FILED' AND target_period IS NOT NULL
                        AND NOT COALESCE(excluded, FALSE) GROUP BY 1, 2""", [cid]):
            d = filed.setdefault(r["PS"], {"inv": 0, "tix": 0})
            d["inv" if r["DOC_KIND"] == "INVOICE" else "tix"] += r["N"]
        pr = q("""SELECT period_start::STRING ps, status, is_stale FROM {{DB}}.AUDIT.PERIOD_RUNS WHERE contract_id = ?""", [cid])
        audited = {r["PS"] for r in pr if r["STATUS"] in ("DRAFTED", "REVIEWED") and not r["IS_STALE"]}
        stale = {r["PS"] for r in pr if r["STATUS"] in ("DRAFTED", "REVIEWED") and r["IS_STALE"]}
        lb_brief = contract_latest(cid, "BRIEFING")
        retry = (lb_brief["p"].get("retry_periods") or []) if lb_brief else []
        st_["runs"] = run_suggestions(filed, audited, stale, retry)
    # the latest range briefing scopes invoices, patterns, verify flags, chat
    # D28: the contract's latest range briefing and run, from whichever thread they were posted in
    brief = contract_latest(cid, "BRIEFING") if cid else None
    run = contract_latest(cid, "RUN") if cid else None
    if run and (not brief or brief["at"] < run["at"]):
        posted = q("""SELECT COUNT(*) n FROM {{DB}}.APP.CHAT_MESSAGES WHERE msg_type = 'BRIEFING'
                      AND payload:run_id::STRING = ?""", [run["p"]["run_id"]])[0]["N"]
        if not posted:
            st_["briefing_missing"] = run["p"]["run_id"]
    if brief and cid:
        periods = brief["p"].get("periods") or []
        st_["range_periods"] = periods
        st_["patterns"] = brief["p"].get("patterns") or []
        st_["verify"] = brief["p"].get("verify_flags") or []
        res = range_resolutions(cid, periods)
        decs = range_decisions(cid, periods)
        st_["invoices"] = [{"invoice_no": i, "ctr": short_name(r["CONTRACTOR_NAME"]) or r["CONTRACTOR_CODE"] or "",
                            "kind": kind_of(r, decs.get(i, [])), "status": r["STATUS"], "version": r["VERSION"],
                            "disputed": float(r["DISPUTED_PHP"] or 0), "held": float(r["HELD_PHP"] or 0),
                            "pay": float(r["PAY_PHP"] or 0), "findings": len(decs.get(i, [])),
                            "overridden": any(d["DECIDED_BY"] == "USER" for d in decs.get(i, [])),
                            "firmer": bool(r["REVISION_NOTE"])} for i, r in res.items()]
        ans = [m for m in msgs if m["p"].get("kind") == "ANSWER"]
        aids = [a for m in ans for a in m["p"].get("action_ids") or []]
        rids = [r for m in ans for r in m["p"].get("revision_ids") or []]
        if aids:
            st_["actions"] = []
            for r in q("""SELECT action_id, action, items::STRING it FROM {{DB}}.APP.PENDING_ACTIONS WHERE status = 'PENDING'
                          AND ARRAY_CONTAINS(action_id::VARIANT, PARSE_JSON(?))""", [json.dumps(aids)]):
                items = j(r["IT"]) or []
                st_["actions"].append({"action_id": r["ACTION_ID"], "label": action_label(r["ACTION"], items),
                                       "pay": sum(float(i["pay_php"] or 0) for i in items)})
        if rids:
            st_["revisions"] = [{"revision_id": r["REVISION_ID"], "invoice_no": r["INVOICE_NO"],
                                 "decision": DEC_LABEL.get((j(r["PJ"]) or {}).get("decision"), "revised").lower(),
                                 "upheld": (j(r["PJ"]) or {}).get("upheld_php")}
                                for r in q("""SELECT p.revision_id, p.proposed::STRING pj, e.invoice_no
                                              FROM {{DB}}.AUDIT.PENDING_REVISIONS p JOIN {{DB}}.AUDIT.EXCEPTIONS e
                                                ON e.exception_id = p.exception_id
                                              WHERE p.status = 'PENDING' AND ARRAY_CONTAINS(p.revision_id::VARIANT, PARSE_JSON(?))""",
                                           [json.dumps(rids)])]
    last = next((m for m in reversed(msgs) if m["role"] == "AGENT" and m["type"] != "TOOL_TRACE"), None)
    if last:
        st_["last"] = {"kind": "LETTER" if last["type"] == "LETTER" else last["p"].get("kind"),
                       "invoice_no": last["p"].get("invoice_no")}
    return st_


# ---------------------------------------------------------------- polling (one fragment for the whole thread)
def signature(tid, cid, msgs):
    batches = thread_batches(msgs)
    r = q("""SELECT LISTAGG(job_id || status, ',') WITHIN GROUP (ORDER BY job_id) s,
                    COUNT_IF(status = 'QUEUED' AND DATEDIFF('second', requested_at, CURRENT_TIMESTAMP()) > 30) stuck
             FROM {{DB}}.APP.JOBS WHERE requested_at > DATEADD('hour', -6, CURRENT_TIMESTAMP()) AND (
                  (kind = 'INTAKE_FILES' AND ARRAY_CONTAINS(job_key::VARIANT, PARSE_JSON(?)))
               OR (kind = 'ONBOARD_CONTRACT' AND ARRAY_CONTAINS(args:batch_id, PARSE_JSON(?)))
               OR (kind IN ('PROCESS_PERIOD', 'RUN_BRIEFING') AND args:contract_id::STRING = ?))""",
          [json.dumps(batches), json.dumps(batches), cid or ""])[0]
    steps = q("""SELECT LISTAGG(period_start || COALESCE(current_step, ''), ',') s FROM {{DB}}.AUDIT.PERIOD_RUNS
                 WHERE contract_id = ? AND status NOT IN ('DRAFTED', 'REVIEWED')""", [cid or ""])[0]["S"]
    logs = q("""SELECT MAX(logged_at)::STRING s FROM {{DB}}.CONTRACT.ONBOARD_LOG WHERE STARTSWITH(source_file, '_inbox/')""")[0]["S"]
    n = q("SELECT COALESCE(MAX(seq), 0) n FROM {{DB}}.APP.CHAT_MESSAGES WHERE thread_id = ?", [tid])[0]["N"]
    return f"{r['S']}|{steps}|{logs}|{n}", r["STUCK"]


def poller(tid, cid, msgs):
    sig0, _ = signature(tid, cid, msgs)

    @st.fragment(run_every=POLL_SECS)
    def poll():
        sig, stuck = signature(tid, cid, msgs)
        if stuck:
            kick_worker()                     # a job that landed as the previous worker run was ending
        if sig != sig0:
            st.rerun(scope="app")
    poll()


# ---------------------------------------------------------------- thread view
def render_message(tid, cid, m, live_letter, last_agent_seq):
    p = m["p"]
    k = p.get("kind")
    if m["type"] == "STEPS":                    # 12a/12b per-period steps (migrated)
        with st.expander(p.get("title") or "Audit steps"):
            for s in p.get("steps") or []:
                st.markdown(f"✓ {s['label']}" + (f" ({s['secs']:.0f} s)" if s.get("secs") is not None else ""))
    elif m["type"] == "BRIEFING":
        render_briefing(cid, p)
    elif m["type"] == "INVOICE_CARDS":          # 12a/12b cards, replaced by the briefing table and suggestions
        st.caption(f"{p.get('intro')} {', '.join(p.get('invoices', []))}")
    elif m["type"] == "RUN":
        render_run(cid, p)
    elif m["type"] == "LETTER":
        r = current_resolution(cid, p.get("invoice_no"))
        if r is not None:
            render_letter(r, live_letter.get(p.get("invoice_no")) == m["seq"], p.get("firmer"))
    elif m["type"] == "TOOL_TRACE":
        with st.expander(f"Used {len(p.get('steps', []))} tool steps"
                         + (" · asked as a standalone question" if p.get("asked_as") else "")):
            if p.get("asked_as"):
                st.markdown(f"**Asked as:** {md_safe(p['asked_as'])}")
            for i, s in enumerate(p.get("steps", []), 1):
                st.markdown(f"{i}. **{s.get('tool')}** `{(s.get('input') or '')[:300]}`")
                if s.get("sql"):
                    st.code(s["sql"], language="sql")
    elif k == "WELCOME_BACK":
        st.markdown(md_safe(welcome_markdown(cid, p)))
    elif k == "INTAKE":
        render_intake(tid, cid, p, m["seq"] == last_agent_seq)
    elif k == "SORTED":
        render_sorted(p)
    elif k == "RECEIVED":
        n, miss = p.get("n"), p.get("missing") or []
        st.markdown((f"Got all {n} files" if not miss else f"Got {n} files; {len(miss)} couldn't be saved ("
                     + ", ".join(miss[:5]) + ("…" if len(miss) > 5 else "") + "). Drop those again")
                    + (f" ({p['merged']} from your earlier drop)" if p.get("merged") else "")
                    + ". You can close this tab now.")
        with st.expander("Details"):
            st.caption(f"Saved in {p.get('upload_secs')} s, {p.get('threads')} at a time.")
    elif k == "STOPPED_DROP":
        miss = p.get("missing") or []
        st.markdown(f"I got {p.get('got')} of {p.get('expected')} files from your last drop; the upload stopped before it "
                    f"finished. Missing: {', '.join(miss[:6])}{f' and {len(miss) - 6} more' if len(miss) > 6 else ''}. "
                    f"Drop them again and I'll add them.")
        if len(miss) > 6:
            with st.expander("Details"):
                st.caption("Missing: " + ", ".join(miss))
    elif k == "CONTRACT_WELCOME":
        contract_card(p.get("contract_id"))
        st.markdown("No invoices have arrived for it yet. Drop them here when they do.")
    elif k == "EXPLAIN":
        inv = p.get("invoice_no")
        render_explain(cid, inv, range_decisions(cid, invoice_no=inv).get(inv, []))
        st.caption("From the stored audit, no new lookup needed.")
    elif k == "APPROVAL":
        for it in p.get("items") or []:
            r = current_resolution(cid, it["invoice_no"])
            what = {"DISPUTE": "the dispute letter", "HOLD": "the hold notice",
                    "DISPUTE_AND_HOLD": "the dispute letter"}.get(it.get("type"))
            st.markdown(f"Approved **{it['invoice_no']}**. "
                        + (f"I saved {what} as a PDF for you to send; nothing was sent. " if what and it.get("pdf_path") else "")
                        + f"Pay **{php(it['pay'])}** + VAT {php(it['vat'])} = {php(it['total'])}.")
            if it.get("pdf_path"):
                with st.expander("Details"):
                    st.caption(it["pdf_path"] + (f" · version {r['VERSION']}" if r is not None else ""))
        if not p.get("items"):                 # 12a/12b approval message (migrated)
            st.markdown(f"Approved {', '.join(p.get('invoices') or [p.get('invoice_no')])}.")
        if len(p.get("items") or []) > 1:
            tot = sum(it["pay"] for it in p["items"])
            st.markdown(f"Together: **{php(tot)}** + VAT.")
    elif k == "APPROVED_ALL":
        items = p.get("items") or []
        res = {i: current_resolution(cid, i) for i in [it["invoice_no"] for it in items]}
        pay = sum(it["pay"] for it in items)
        tot = sum(it["total"] for it in items)
        disp = sum(float(r["DISPUTED_PHP"] or 0) for r in res.values() if r is not None)
        held = sum(float(r["HELD_PHP"] or 0) for r in res.values() if r is not None)
        n_l = sum(1 for it in items if it["type"] in ("DISPUTE", "DISPUTE_AND_HOLD"))
        n_n = sum(1 for it in items if it["type"] == "HOLD")
        bits = [f"pay **{php(pay)}** before VAT ({php(tot)} with VAT)"]
        if disp:
            bits.append(f"dispute {php(disp)} in {n_l} letter{'s' if n_l != 1 else ''}")
        if held:
            bits.append(f"hold {php(held)} in {n_n} notice{'s' if n_n != 1 else ''}")
        st.markdown(f"Done. I approved {len(items)} of {p.get('asked')} invoices as recommended: {and_words(bits)}. "
                    f"Every PDF is saved in the audit record for you to send; nothing was sent."
                    + ("" if len(items) == p.get("asked") else " The rest weren't approved; see the messages above."))
        with st.expander("Details"):
            for it in items:
                st.caption(f"{it['invoice_no']}: pay {php(it['pay'])} + VAT {php(it['vat'])}"
                           + (f" · {it['pdf_path']}" if it.get("pdf_path") else ""))
    elif k == "ONE":
        inv = p.get("invoice_no")
        res, decs, facts = range_data(qd, cid, p.get("periods") or [])
        r = res.get(inv)
        if r is not None:
            row = {"invoice_no": inv, "ctr": short_name(r["CONTRACTOR_NAME"]) or r["CONTRACTOR_CODE"],
                   "pay": float(r["PAY_PHP"] or 0)}
            st.markdown(md_safe(one_by_one_text(row, decs.get(inv, []), facts)))
            st.caption(f"{p.get('total', 0) - p.get('left', 0) + 1} of {p.get('total')}"
                       + (" · approved" if r["STATUS"] == "APPROVED" else ""))
            with st.expander("The letter"):
                st.text(r["LETTER_TEXT"] or "")
    elif k == "OVERRIDDEN":
        st.markdown(f"Recorded as your decision. **{p['invoice_no']}** now pays **{php(p.get('pay_after'))}** + VAT "
                    f"(was {php(p.get('pay_before'))})."
                    + (" The approved letter stays in the history, and I've drafted a new version." if p.get("was_approved")
                       else " I redrafted the letter."))
    elif k == "CHANGE_FORM":
        render_change_form(tid, cid, p, m["seq"] == last_agent_seq)
    elif k == "ANSWER":
        render_answer(p, f"{tid}:{m['seq']}")
    elif k == "EXPLAIN_LEGACY" or k in ("LETTER_INTRO",):
        st.caption(f"{k.replace('_', ' ').lower()}: {p.get('invoice_no')}")
    else:
        st.markdown(md_safe(php_to_peso(p.get("text"))))


def render_suggestions(tid, cid, sugg, state, msgs, seq):
    sugg = [s for s in sugg or [] if (s.get("label") or "").strip()]
    if not sugg:                                       # D29.2: no header without at least one suggestion
        return
    # D33: a click is only recorded here and acted on at the top of the next run (run_clicked), so a long action
    # (an agent answer, an approval) never runs under a header whose buttons were not drawn yet
    box = st.container()
    box.caption("What I can do next")
    for i, s in enumerate(sugg):
        label = s["label"] + (f": {s['amt']}" if s.get("amt") else "")
        box.button(label, key=f"sg:{tid}:{seq}:{i}:{s['act']}", type="primary" if s.get("money") else "secondary",
                   on_click=st.session_state.__setitem__, args=(f"clicked:{tid}", {"seq": seq, "s": s}))


def run_clicked(tid, cid, msgs):
    """Act on a suggestion clicked in the previous run, if it is still the latest message's suggestion."""
    c = st.session_state.pop(f"clicked:{tid}", None)
    agent_msgs = [m for m in msgs if m["role"] == "AGENT" and m["type"] not in ("TOOL_TRACE", "DIVIDER")]
    if not c or not agent_msgs or agent_msgs[-1]["seq"] != c["seq"] or msgs[-1]["role"] != "AGENT":
        return False
    do_act(tid, cid, c["s"], next_state(tid, cid, msgs), msgs)
    return True


# ---------------------------------------------------------------- threads and welcome back (step 12d, D28)
def threads(cid):
    return qd("""SELECT thread_id, title, created_by, created_at, last_message_at FROM {{DB}}.APP.THREADS
                 WHERE contract_id = ? ORDER BY COALESCE(last_message_at, created_at) DESC""", [cid])


def new_thread(cid):
    tid = f"{cid}#{uuid.uuid4().hex[:8]}"
    q("""INSERT INTO {{DB}}.APP.THREADS (thread_id, contract_id, title, created_by, created_at, last_message_at)
         SELECT ?, ?, NULL, ?, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ""",
      [tid, cid, current_user()])
    return tid


def auto_title(tid, msgs):
    """Title a thread from its first meaningful message (chat.thread_title). A run's dates win over an upload or a
    question, so an auto title is replaced once, when the thread's first run appears (D28)."""
    ts = [(m["type"] == "RUN", thread_title(as_plain(m))) for m in msgs]
    t = next((x for run, x in ts if run and x), None) or next((x for _, x in ts if x), None)
    if t:
        q("""UPDATE {{DB}}.APP.THREADS SET title = ? WHERE thread_id = ?
             AND (title IS NULL OR (STARTSWITH(?, 'Audit ') AND NOT STARTSWITH(title, 'Audit ')))""", [t, tid, t])
    return t


def welcome_back(tid, cid):
    """D28: on opening a thread, if anything happened on the contract since this user's last visit (their own actions
    included), post one WELCOME_BACK message. Its text is rebuilt from V_ACTIVITY on every render. Records the visit."""
    if not cid:
        return None
    me = current_user()
    v = q("SELECT MAX(last_seen_at) t FROM {{DB}}.APP.USER_VISITS WHERE LOWER(user_name) = LOWER(?) AND contract_id = ?",
          [me, cid])
    since = v[0]["T"] if v else None
    now = q("SELECT CURRENT_TIMESTAMP()::TIMESTAMP_NTZ t")[0]["T"]
    n = q("""SELECT COUNT(*) n FROM {{DB}}.APP.V_ACTIVITY WHERE contract_id = ? AND at <= ?::TIMESTAMP_NTZ
             AND (? IS NULL OR at > ?::TIMESTAMP_NTZ)""", [cid, str(now), since and str(since), since and str(since)])[0]["N"]
    q("""MERGE INTO {{DB}}.APP.USER_VISITS t USING (SELECT ? u, ? c, ? th, ?::TIMESTAMP_NTZ s) x
         ON LOWER(t.user_name) = LOWER(x.u) AND t.contract_id = x.c
         WHEN MATCHED THEN UPDATE SET last_seen_at = x.s, thread_id = x.th
         WHEN NOT MATCHED THEN INSERT (user_name, contract_id, thread_id, last_seen_at) VALUES (x.u, x.c, x.th, x.s)""",
      [me, cid, tid, str(now)])
    if not n:
        return None
    payload = {"kind": "WELCOME_BACK", "viewer": me, "since": since and str(since), "until": str(now)}
    post(tid, "AGENT", "TEXT", payload)
    return payload


def welcome_markdown(cid, p):
    """WELCOME_BACK text, rebuilt from V_ACTIVITY (the window) and RESOLUTIONS as of the message's time, so it reads the
    same later. SQL amounts only."""
    since, until = p.get("since"), p.get("until")
    groups = qd("""WITH a AS (
                     SELECT * FROM {{DB}}.APP.V_ACTIVITY WHERE contract_id = ? AND at <= ?::TIMESTAMP_NTZ
                       AND (? IS NULL OR at > ?::TIMESTAMP_NTZ)),
                   latest AS (SELECT who, kind, SUM(amount_effect) amt FROM (
                                SELECT * FROM a QUALIFY ROW_NUMBER() OVER (PARTITION BY kind, subject ORDER BY at DESC) = 1)
                              GROUP BY who, kind),
                   g AS (SELECT who, kind, COUNT(*) n, COUNT(DISTINCT subject) subj, MIN(at) first_at,
                                LISTAGG(DISTINCT subject, ', ') WITHIN GROUP (ORDER BY subject) subjects
                         FROM a GROUP BY who, kind)
                   SELECT g.*, l.amt FROM g LEFT JOIN latest l ON l.who = g.who AND l.kind = g.kind
                   ORDER BY g.first_at""",
                [cid, until, since, since])
    # what was waiting when the message was posted: each invoice's latest version by then, not yet approved by then
    waiting = qd("""SELECT COUNT(*) n, SUM(pay_php) pay, SUM(disputed_php) disputed, SUM(held_php) held FROM (
                      SELECT * FROM {{DB}}.AUDIT.RESOLUTIONS WHERE contract_id = ? AND created_at <= ?::TIMESTAMP_NTZ
                      QUALIFY ROW_NUMBER() OVER (PARTITION BY invoice_no ORDER BY version DESC) = 1)
                    WHERE approved_at IS NULL OR approved_at > ?::TIMESTAMP_NTZ""", [cid, until, until])
    # "you" is whoever is reading now, so a shared thread never says "you" about someone else
    return welcome_text(groups, waiting[0] if waiting else {}, since, current_user(), cid)


# D34: deleting a chat. The contract's main thread (thread_id = contract id, or _INTAKE) holds the audit runs and
# briefings the suggestions read, so it is pinned as "Audit history" and cannot be deleted (post-demo: D34 note).
MAIN_HELP = "Holds this contract's audit runs and briefings, which the suggestions read, so it can't be deleted yet."


def is_main_thread(tid):
    return "#" not in (tid or "")


def delete_chat(tid):
    """Hard delete of one chat: its THREADS row, all its CHAT_MESSAGES and its THREAD_FOCUS. Decisions, approvals,
    letters, uploads and activity are not touched."""
    if is_main_thread(tid):
        raise ValueError("The contract's audit history chat can't be deleted.")
    session.sql("BEGIN TRANSACTION").collect()
    try:
        for t in ("CHAT_MESSAGES", "THREAD_FOCUS", "THREADS"):
            q("DELETE FROM {{DB}}.APP." + t + " WHERE thread_id = ?", [tid])      # t: the fixed tuple above
        session.sql("COMMIT").collect()
    except Exception:
        session.sql("ROLLBACK").collect()
        raise


def delete_control(tid, key):
    """🗑 with a confirm step (the popover). Returns True when the chat was deleted."""
    with st.popover("🗑", help="Delete this chat"):
        st.markdown("Delete this chat permanently? Its messages are removed for everyone. Decisions, approvals and "
                    "letters stay.")
        if st.button("Delete", key=f"del:{key}:{tid}", type="primary"):
            delete_chat(tid)
            return True
    return False


def thread_sidebar(cid):
    """Shared threads of the contract, newest first, labelled with who started each; New chat on top."""
    key = f"thread:{cid}"
    ts = threads(cid)
    me = (current_user() or "").lower()
    with st.sidebar:
        st.markdown(f"**Chats on {cid}**")
        if st.button("New chat", key=f"newchat:{cid}", width="stretch"):
            st.session_state[key] = new_thread(cid)
            st.rerun()
        ids = [t["THREAD_ID"] for t in ts]
        if st.session_state.get(key) not in ids:
            st.session_state[key] = ids[0] if ids else cid
        for t in sorted(ts, key=lambda t: not is_main_thread(t["THREAD_ID"])):    # D34: audit history pinned on top
            who = "you" if (t["CREATED_BY"] or "").lower() == me else (t["CREATED_BY"] or "MERRY")
            at = t["LAST_MESSAGE_AT"] or t["CREATED_AT"]
            main = is_main_thread(t["THREAD_ID"])
            label = ("📌 Audit history" if main else (t["TITLE"] or "New chat")) + f"  \n_{who} · {at:%b} {at.day}_"
            cur = t["THREAD_ID"] == st.session_state[key]
            left, right = st.columns([5, 1])
            if left.button(label, key=f"th:{t['THREAD_ID']}", width="stretch", type="primary" if cur else "secondary",
                           help=MAIN_HELP if main else None):
                st.session_state[key] = t["THREAD_ID"]
                st.rerun()
            if not main:
                with right:
                    if delete_control(t["THREAD_ID"], "sb"):
                        if cur:
                            st.session_state.pop(key, None)
                        st.rerun()
    return st.session_state[key]


def greeting(cid, open_chat=False):
    with st.chat_message("assistant"):
        if not cid and open_chat:
            st.markdown("Hi, I'm MERRY. Drop invoices, field tickets or a contract here, or ask about a contractor, an "
                        "invoice or a contract, and I'll pick up the contract from that.")
        elif not cid:
            st.markdown("Hi, I'm MERRY. I check contractor invoices against your contracts and what actually happened "
                        "on site.\n\nDrop a service contract in the box below to start. Later, drop invoices and field "
                        "tickets in the same place, and I'll sort them by contract and dates.")
        else:
            st.markdown(f"Drop invoices and field tickets for **{cid}** here. I'll sort them by the dates they cover and "
                        f"check them against the gate log, equipment log, timesheets and daily reports, which are already "
                        f"in Snowflake.")


def review_tab(cid):
    load_labels(qd)
    tid = thread_sidebar(cid) if cid else INTAKE        # D28: shared threads per contract
    thread_view(tid, cid)


def thread_view(tid, cid, on_text=None):
    """One thread: welcome back, messages, suggestions, polling, chat box. on_text(tid, text) replaces the reply to a
    typed question while the thread has no contract (the in-tab chat binds the thread to one, merry_tab.py)."""
    if cid and not st.session_state.get(f"wb:{tid}"):
        st.session_state[f"wb:{tid}"] = True            # once per opening of a thread in this browser session
        welcome_back(tid, cid)
    stopped_drops(tid)
    msgs = messages(tid)
    if run_clicked(tid, cid, msgs):                     # D33: a suggestion clicked in the previous run
        st.rerun()
    if cid:
        auto_title(tid, msgs)
    if not [m for m in msgs if m["p"].get("kind") != "WELCOME_BACK"]:
        greeting(cid, open_chat=on_text is not None)
    live_letter = {}
    for m in msgs:
        if m["type"] == "LETTER":
            live_letter[m["p"].get("invoice_no")] = m["seq"]
    agent_msgs = [m for m in msgs if m["role"] == "AGENT" and m["type"] not in ("TOOL_TRACE", "DIVIDER")]
    last_agent_seq = agent_msgs[-1]["seq"] if agent_msgs and msgs[-1]["role"] == "AGENT" else None
    state = next_state(tid, cid, msgs) if msgs else {"busy": False}
    sugg = next_steps(state) if last_agent_seq else []
    for m in msgs:
        if m["type"] == "DIVIDER":
            st.markdown(f"<div style='text-align:center;color:gray;margin:18px 0 6px'>── <b>{m['p'].get('label')}</b> ──</div>",
                        unsafe_allow_html=True)
            continue
        if m["role"] == "USER":
            with st.chat_message("user"):
                st.markdown(md_safe(m["p"].get("text")) or "")
                if m["p"].get("files"):
                    fs = m["p"]["files"]
                    st.caption("📄 " + " · ".join(fs[:12]) + (f" · + {len(fs) - 12} more" if len(fs) > 12 else ""))
            continue
        with st.chat_message("assistant"):
            render_message(tid, cid, m, live_letter, last_agent_seq)
            if m["seq"] == last_agent_seq:
                render_suggestions(tid, cid, sugg, state, msgs, m["seq"])
    if state.get("busy"):
        poller(tid, cid, msgs)
    got = st.chat_input("Drop files here, or ask MERRY" + (f" about {cid}" if cid else ""), key=f"chat:{tid}",
                        accept_file="multiple", file_type=["pdf", "csv"])
    if got:
        text = (got.text if hasattr(got, "text") else str(got)) or ""
        files = list(got.files) if hasattr(got, "files") and got.files else []
        if files:
            drop_files(tid, text.strip(), files)
        elif text.strip():
            post(tid, "USER", "TEXT", {"text": text.strip()})
            if not cid and on_text:
                on_text(tid, text.strip())
            elif not cid:
                say(tid, "Drop a contract first, and I'll read it. Use the paperclip, or drag files onto the message box.")
            else:
                ask(tid, cid, state.get("range_periods") or [], msgs, text.strip())
        st.rerun()
