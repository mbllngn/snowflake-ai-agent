"""MERRY smoke test. Read-only by default; reads no EVAL object.
Run through tools/run.py:

    python3 merry_agent/tools/run.py merry_agent/tools/smoke_test.py                  # objects, role, app, data state
    python3 merry_agent/tools/run.py merry_agent/tools/smoke_test.py --findings 9 --upheld 287000   # after the sample audit
    python3 merry_agent/tools/run.py merry_agent/tools/smoke_test.py --ask --approve-one   # also writes: a chat Q&A, an approval
    python3 merry_agent/tools/run.py merry_agent/tools/smoke_test.py --snapshot before.json / --compare before.json
(add --db NAME after run.py for another database). Works on an empty database (checks the empty state) and on one
with data (renders the first contract's audit thread).

--ask posts one question and MERRY's answer into the contract thread through the app's own review.ask.
--approve-one approves one DRAFT dispute letter (which saves its PDF) through the app's own review.approve_one.
--snapshot writes the counts and keys of chats, decisions, approvals and activity; --compare checks that every one of
them is still there (used before and after a redeploy). Prints check / expected / actual / PASS-FAIL."""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo"))
import app_harness as a

ap = argparse.ArgumentParser()
ap.add_argument("--findings", type=int)
ap.add_argument("--upheld", type=float)
ap.add_argument("--ask", action="store_true")
ap.add_argument("--approve-one", action="store_true")
ap.add_argument("--snapshot")
ap.add_argument("--compare")
o = ap.parse_args()
DB, ROLE, WH = "{{DB}}", "{{ROLE}}", "{{WAREHOUSE}}"
review, chat, st, s = a.review, a.chat, a.st, a.session
q = review.q
_c = q(f"SELECT contract_id FROM {DB}.CURATED.INVOICE_HEADERS GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1")
CID = _c[0][0] if _c else None                     # the contract with the most invoices, or none (empty database)
PERIODS = [str(r[0]) for r in q(f"SELECT period_start FROM {DB}.AUDIT.PERIOD_RUNS WHERE contract_id = ? ORDER BY 1", [CID])]
rows, fails = [], 0


def check(name, expected, actual, ok):
    global fails
    fails += not ok
    rows.append((name, str(expected), str(actual), "PASS" if ok else "FAIL"))
    print(f"{'PASS' if ok else 'FAIL'}  {name} | expected {expected} | actual {actual}", flush=True)


def n(sql, p=None):
    return q(sql, p)[0][0]


# ---- objects (counts as exported from MERRY on 2026-10-27)
EXPECT = {"BASE TABLE": 44, "VIEW": 26}   # D32: +3 tables, +13 APP.V_UI_* views
for kind, want in EXPECT.items():
    got = n(f"SELECT COUNT(*) FROM {DB}.INFORMATION_SCHEMA.TABLES WHERE table_type = ? AND table_schema <> 'INFORMATION_SCHEMA'", [kind])
    check(f"{kind.lower()}s", want, got, got == want)
got = n(f"SELECT COUNT(*) FROM {DB}.INFORMATION_SCHEMA.PROCEDURES")
check("procedures", 37, got, got == 37)   # D32: +3 review/credit/CSV procedures; D36: +1 site-record claim
got = n(f"SELECT COUNT(*) FROM {DB}.INFORMATION_SCHEMA.FUNCTIONS")
check("functions", 2, got, got == 2)
ss = s.sql(f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {DB}.AGENT").collect()
check("search service serving", "CONTRACT_SEARCH ACTIVE", [(r["name"], r["serving_state"]) for r in ss],
      len(ss) == 1 and ss[0]["serving_state"] == "ACTIVE")
check("semantic view", "AUDIT_SV", [r["name"] for r in s.sql(f"SHOW SEMANTIC VIEWS IN SCHEMA {DB}.AGENT").collect()],
      len(s.sql(f"SHOW SEMANTIC VIEWS IN SCHEMA {DB}.AGENT").collect()) == 1)
ag = s.sql(f"SHOW AGENTS IN SCHEMA {DB}.AGENT").collect()
check("agent", f"MERRY_AGENT owned by {ROLE}", [(r["name"], r["owner"]) for r in ag], len(ag) == 1 and ag[0]["owner"] == ROLE)
tk = s.sql(f"SHOW TASKS IN SCHEMA {DB}.APP").collect()
check("job task", "TK_RUN_JOBS", [r["name"] for r in tk], [r["name"] for r in tk] == ["TK_RUN_JOBS"])
dbs = s.sql(f"SHOW SCHEMAS IN DATABASE {DB}").collect()
check("no EVAL or SCRATCH schema", "absent", [r["name"] for r in dbs if r["name"] in ("EVAL", "SCRATCH")],
      not [r for r in dbs if r["name"] in ("EVAL", "SCRATCH")])

# ---- reference data (seeded by setup; kept by tools/reset.py)
ref = q(f"""SELECT (SELECT COUNT(*) FROM {DB}.APP.RULE_LABELS) l, (SELECT COUNT(*) FROM {DB}.APP.STATUS_WORDS) w,
                   (SELECT COUNT(*) FROM {DB}.APP.PLAIN_PHRASES) p""")[0]
check("reference data", "8 rule labels, 4 status words, plain phrases", f"{ref['L']}, {ref['W']}, {ref['P']}",
      ref["L"] == 8 and ref["W"] == 4 and ref["P"] > 0)

# ---- app opens: the Streamlit object, its files, and the app's own render path
sl = s.sql(f"SHOW STREAMLITS IN SCHEMA {DB}.APP").collect()
check("streamlit app", f"MERRY on {WH}, owned by {ROLE}", [(r["name"], r["query_warehouse"], r["owner"]) for r in sl],
      len(sl) == 1 and sl[0]["name"] == "MERRY" and sl[0]["owner"] == ROLE and sl[0]["query_warehouse"] == WH)
desc = s.sql(f"DESCRIBE STREAMLIT {DB}.APP.MERRY").collect()[0].as_dict() if sl else {}
check("streamlit runtime / main file", "container runtime / streamlit_app.py",
      f"{desc.get('runtime_name')} / {desc.get('main_file')}",
      "CONTAINER" in str(desc.get("runtime_name")) and desc.get("main_file") == "streamlit_app.py")
loc = desc.get("live_version_location_uri") or desc.get("default_version_location_uri")
live = [r["name"].split("/")[-1] for r in s.sql(f"LIST '{loc}'").collect()] if loc else []
want = {"streamlit_app.py", "dashboard.py", "merry_tab.py", "review.py", "chat.py", "common.py", "onboard.py", "merry_sql.py"}
check("app files in the served version", sorted(want), sorted(set(live) & want), want <= set(live))
try:
    chat.load_labels(review.qd)
    if CID:
        msgs = review.messages(CID)
        for m in msgs:
            review.render_message(CID, CID, m, {}, None)
        check("app renders the contract thread (real rows, stub UI)", "no error", f"{CID}: {len(msgs)} messages", True)
    else:
        n_c = q(f"SELECT COUNT(*) n FROM {DB}.CONTRACT.CONTRACTS")[0]["N"]
        k_ = q(f"SELECT findings, invoices FROM {DB}.APP.V_UI_KPIS")[0]
        check("empty state (\"No contracts yet\")", "0 contracts, 0 invoices, 0 findings",
              f"{n_c} contracts, {k_['INVOICES'] or 0} invoices, {k_['FINDINGS'] or 0} findings",
              n_c == 0 and not k_["INVOICES"] and not k_["FINDINGS"])
except Exception as e:
    check("app renders (stub UI)", "no error", repr(e)[:300], False)

# ---- empty state: every tab of the root app (Dashboard, Issues, Ask MERRY, Data) drawn through its own code
if not CID and not q(f"SELECT COUNT(*) n FROM {DB}.CONTRACT.CONTRACTS")[0]["N"]:
    import runpy, traceback, types
    import merry_tab, settings
    root = os.path.dirname(os.path.abspath(settings.__file__))     # the repository root (streamlit_app.py, dashboard.py)
    sys.path.insert(0, root)
    st.connection = lambda *a_, **k_: types.SimpleNamespace(session=lambda: s)
    merry_tab.new_chat = lambda: f"{merry_tab.NEW}#smoketest"      # an unsaved chat: the check writes nothing
    st.calls.clear()
    try:
        g = runpy.run_path(os.path.join(root, "streamlit_app.py"), run_name="__main__")
        for t in g["DATA_TABLES"]:                                   # the Data tab shows one table at a time: load all
            g["load_table"](t)
        outs = [c for c, _ in st.calls]
        ok = ("info", g["NO_CONTRACTS"]) in st.calls and "metric" not in outs
        check("empty state: all tabs render (app code, stub UI)", "no error; Dashboard shows only \"No contracts yet\"",
              f"{len(st.calls)} outputs, {outs.count('metric')} KPI cards, {len(g['DATA_TABLES'])} data tables", ok)
    except Exception as e:
        check("empty state: all tabs render (app code, stub UI)", "no error",
              repr(e)[:200] + " @ " + traceback.format_exc().strip().splitlines()[-3][:150], False)
    left = q(f"SELECT (SELECT COUNT(*) FROM {DB}.APP.THREADS) t, (SELECT COUNT(*) FROM {DB}.APP.CHAT_MESSAGES) m")[0]
    check("empty state: render check wrote nothing", "0 threads, 0 messages", f"{left['T']}, {left['M']}",
          left["T"] == 0 and left["M"] == 0)

# ---- role: the EVAL firewall
gr = s.sql(f"SHOW GRANTS TO ROLE {ROLE}").collect()
bad = [r["name"] for r in gr if ".EVAL" in r["name"] or ".SCRATCH" in r["name"]]
check(f"{ROLE} has nothing on EVAL / SCRATCH", "[]", bad, not bad)

# ---- audit totals (only when asked: they exist after the month audit)
if o.findings is not None or o.upheld is not None:
    t = q(f"""SELECT COUNT(*) n, SUM(IFF(is_upheld, upheld_php, 0)) up FROM {DB}.AUDIT.V_DECISIONS d
              JOIN {DB}.AUDIT.EXCEPTIONS e ON e.exception_id = d.exception_id WHERE d.is_current""")[0]
    if o.findings is not None:
        check("findings (current decisions)", o.findings, t["N"], t["N"] == o.findings)
    if o.upheld is not None:
        check("upheld PHP", f"{o.upheld:,.2f}", f"{float(t['UP'] or 0):,.2f}", abs(float(t["UP"] or 0) - o.upheld) < 0.005)

# ---- chat answers a question, through the app's own path
if o.ask:
    before = n(f"SELECT COALESCE(MAX(seq), 0) FROM {DB}.APP.CHAT_MESSAGES WHERE thread_id = ?", [CID])
    question = "Which findings were upheld in part, and which contract clause applies to each?"
    review.post(CID, "USER", "TEXT", {"text": question})
    review.ask(CID, CID, PERIODS, review.messages(CID), question)
    ans = q(f"""SELECT payload:kind::STRING k, payload:text::STRING t FROM {DB}.APP.CHAT_MESSAGES WHERE thread_id = ?
                AND seq > ? AND role = 'AGENT' AND msg_type = 'TEXT' ORDER BY seq""", [CID, before])
    text = ans[-1]["T"] if ans else ""
    print("   answer:", text[:500])
    check("chat answers a question", "an ANSWER citing a clause", f"{ans[-1]['K'] if ans else None}, {len(text)} chars",
          bool(ans) and ans[-1]["K"] == "ANSWER" and bool(re.search(r"\b[Cc]lause\s+\d", text)))

if o.approve_one:
    r = q(f"""SELECT invoice_no, version FROM {DB}.AUDIT.V_RESOLUTIONS WHERE contract_id = ? AND status = 'DRAFT'
              AND resolution_type = 'DISPUTE' ORDER BY period_start, invoice_no LIMIT 1""", [CID])
    if not r:
        check("approve one resolution", "a DRAFT dispute letter", "none", False)
    else:
        res = review.approve_one(CID, CID, r[0]["INVOICE_NO"], r[0]["VERSION"], PERIODS)
        st_ = q(f"SELECT status FROM {DB}.AUDIT.V_RESOLUTIONS WHERE invoice_no = ?", [r[0]["INVOICE_NO"]])[0]["STATUS"]
        check("approve one resolution", f"{r[0]['INVOICE_NO']} APPROVED with a PDF", f"{st_}, {res and res.get('pdf_path')}",
              st_ == "APPROVED" and bool(res and res.get("pdf_path")))

# ---- persistence snapshot / compare
KEYS = {
    "chat messages": f"SELECT thread_id || '#' || seq FROM {DB}.APP.CHAT_MESSAGES",
    "threads": f"SELECT thread_id FROM {DB}.APP.THREADS",
    "decisions": f"SELECT exception_id || '#' || version FROM {DB}.AUDIT.DECISIONS",
    "approvals": f"SELECT invoice_no || '#' || version FROM {DB}.AUDIT.RESOLUTIONS WHERE status = 'APPROVED'",
    "resolutions": f"SELECT invoice_no || '#' || version FROM {DB}.AUDIT.RESOLUTIONS",
    "activity": f"SELECT HASH(*)::STRING FROM {DB}.APP.V_ACTIVITY",
    "jobs": f"SELECT job_id::STRING FROM {DB}.APP.JOBS",
    "uploads": f"SELECT file_path FROM {DB}.APP.UPLOADS",
    "contract edits": f"SELECT HASH(*)::STRING FROM {DB}.CONTRACT.EDIT_LOG",
}
snap = {}
for name, sql in KEYS.items():
    try:
        snap[name] = sorted(str(r[0]) for r in s.sql(sql).collect())
    except Exception as e:
        snap[name] = [f"ERROR {str(e)[:120]}"]
if o.snapshot:
    json.dump(snap, open(o.snapshot, "w"))
    for k_, v in snap.items():
        print(f"   snapshot {k_}: {len(v)}")
if o.compare:
    old = json.load(open(o.compare))
    for k_, v in old.items():
        missing = sorted(set(v) - set(snap.get(k_, [])))
        check(f"kept after redeploy: {k_}", f"all {len(v)}", f"{len(v) - len(missing)} of {len(v)}"
              + (f", missing {missing[:3]}" if missing else ""), not missing)

print("\n| check | expected | actual | result |\n|---|---|---|---|")
for r in rows:
    print("| " + " | ".join(r) + " |")
print(f"\n{len(rows) - fails}/{len(rows)} PASS")
sys.exit(1 if fails else 0)
