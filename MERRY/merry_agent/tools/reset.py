"""Admin only: empty MERRY's user data so the app returns to its first-ever state, without redeploying.

    python3 merry_agent/tools/reset.py                       # everything (asks you to type the database name)
    python3 merry_agent/tools/reset.py --contract <ID>       # one contract only
    python3 merry_agent/tools/reset.py --db NAME --yes       # another database, no prompt (scripts)

Full reset: deletes every row of every table in RAW, DOCS, CONTRACT, CURATED, AUDIT and APP except the reference data
(APP.RULE_LABELS, APP.STATUS_WORDS, APP.PLAIN_PHRASES): contracts, uploads, documents, extractions, decisions, letters,
chats, activity, jobs and site records. Removes every file on DOCS.DOC_STAGE (uploads, contracts, letter PDFs) and
refreshes the contract search service. Objects, grants and the app itself are not touched.

--contract: deletes only that contract's rows (tables with a CONTRACT_ID column, and rows linked to them through its
exceptions, threads, tickets, invoices or jobs) and its files. Site records belong to the site, not to a contract,
and are kept.

Tables are found from the database itself, so nothing here names a table or an ID. Runs as ACCOUNTADMIN (the tables'
owner). Uses the connection and database from settings.py unless --db is given."""
import argparse, json, os, re, sys, snowflake.connector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import settings  # noqa: E402

REFERENCE = {("APP", "RULE_LABELS"), ("APP", "STATUS_WORDS"), ("APP", "PLAIN_PHRASES")}
SCHEMAS = ("RAW", "DOCS", "CONTRACT", "CURATED", "AUDIT", "APP")
SITE = {"TIMESHEETS", "GATE_LOG", "EQUIPMENT_LOG", "DAILY_REPORTS", "SITE_RECORD_SOURCES"}   # RAW: site, not contract

ap = argparse.ArgumentParser()
ap.add_argument("--db", default=settings.DB)
ap.add_argument("--contract")
ap.add_argument("--yes", action="store_true", help="skip the confirm prompt")
o = ap.parse_args()
db = o.db.upper()
assert re.fullmatch(r"[A-Z][A-Z0-9_$]{0,200}", db), f"bad database name {db}"
if o.contract:
    assert re.fullmatch(r"[A-Za-z0-9._-]+", o.contract), "bad contract id"

k = snowflake.connector.connect(connection_name=settings.CONNECTION).cursor()
for s in ("USE ROLE ACCOUNTADMIN", f"USE WAREHOUSE {settings.WAREHOUSE}"):
    k.execute(s)


def rows(sql, p=None):
    k.execute(sql, p)
    cols = [c[0] for c in k.description]
    return [dict(zip(cols, r)) for r in k.fetchall()]


tables = {}
for r in rows(f"""SELECT t.table_schema s, t.table_name n, ARRAY_AGG(c.column_name) cols
                  FROM {db}.INFORMATION_SCHEMA.TABLES t JOIN {db}.INFORMATION_SCHEMA.COLUMNS c
                    ON c.table_schema = t.table_schema AND c.table_name = t.table_name
                  WHERE t.table_type = 'BASE TABLE' AND t.table_schema IN ({', '.join("'" + s + "'" for s in SCHEMAS)})
                  GROUP BY 1, 2"""):
    if (r["S"], r["N"]) not in REFERENCE:
        tables[(r["S"], r["N"])] = set(json.loads(r["COLS"]))
if o.contract and not rows(f"SELECT 1 FROM {db}.CONTRACT.CONTRACTS WHERE contract_id = %s", [o.contract]):
    sys.exit(f"No contract {o.contract} in {db}.")

what = f"contract {o.contract} in {db}" if o.contract else f"ALL user data in {db} ({len(tables)} tables and every uploaded file)"
print(f"This permanently deletes {what}. Reference data (rule labels, status words, plain phrases) is kept.")
if not o.yes and input(f"Type {o.contract or db} to confirm: ").strip() != (o.contract or db):
    sys.exit("Cancelled; nothing was changed.")

c = o.contract
# per contract: rows linked through these keys go before the tables that hold the keys
LINKS = [("EXCEPTION_ID", f"SELECT exception_id FROM {db}.AUDIT.EXCEPTIONS WHERE contract_id = %s"),
         ("THREAD_ID", f"SELECT thread_id FROM {db}.APP.THREADS WHERE contract_id = %s"),
         ("TICKET_NO", f"SELECT ticket_no FROM {db}.CURATED.TICKETS WHERE contract_id = %s"),
         ("INVOICE_NO", f"SELECT invoice_no FROM {db}.CURATED.INVOICE_HEADERS WHERE contract_id = %s")]
files = []
if c:
    files = [r["P"] for r in rows(f"""SELECT file_path p FROM {db}.APP.UPLOADS WHERE contract_id = %s
                                      UNION SELECT source_file FROM {db}.CONTRACT.CONTRACTS WHERE contract_id = %s""", [c, c])
             if r["P"]] + [f"periods/{c}/", f"resolutions/{c}/"]
done, kept = 0, []
k.execute("BEGIN")
try:
    order = sorted(tables, key=lambda t: "CONTRACT_ID" in tables[t])        # linked tables first, then CONTRACT_ID ones
    for (s, n) in order:
        cols, fq = tables[(s, n)], f"{db}.{s}.{n}"
        if not c:
            k.execute(f"DELETE FROM {fq}")
        elif s == "RAW" and n in SITE:
            kept.append(fq)
            continue
        elif "CONTRACT_ID" in cols:
            k.execute(f"DELETE FROM {fq} WHERE contract_id = %s", [c])
        elif n == "JOBS":
            k.execute(f"DELETE FROM {fq} WHERE args:contract_id::STRING = %s", [c])
        else:
            link = next(((col, sub) for col, sub in LINKS if col in cols), None)
            if not link:
                kept.append(fq)
                continue
            k.execute(f"DELETE FROM {fq} WHERE {link[0]} IN ({link[1]})", [c])
        done += k.rowcount or 0
    k.execute("COMMIT")
except Exception:
    k.execute("ROLLBACK")
    raise
if c:
    for f in files:
        k.execute(f"REMOVE @{db}.DOCS.DOC_STAGE/{f}")
else:
    k.execute(f"REMOVE @{db}.DOCS.DOC_STAGE")
try:
    k.execute(f"ALTER CORTEX SEARCH SERVICE {db}.AGENT.CONTRACT_SEARCH REFRESH")
except Exception as e:
    print(f"  (search service refresh skipped: {str(e)[:120]})")
print(f"== reset {what}: {done} rows deleted" + (f"; kept (not per contract): {', '.join(kept)}" if kept else ""))
left = rows(f"SELECT (SELECT COUNT(*) FROM {db}.CONTRACT.CONTRACTS) contracts, (SELECT COUNT(*) FROM {db}.APP.RULE_LABELS) labels,"
            f" (SELECT COUNT(*) FROM {db}.APP.STATUS_WORDS) words, (SELECT COUNT(*) FROM {db}.APP.PLAIN_PHRASES) phrases")[0]
print(f"   now: {left['CONTRACTS']} contracts; reference data: {left['LABELS']} rule labels, {left['WORDS']} status words, "
      f"{left['PHRASES']} plain phrases")
