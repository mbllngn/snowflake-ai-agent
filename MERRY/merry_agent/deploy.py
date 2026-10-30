"""MERRY database setup: creates or updates everything MERRY needs in one database. Usually run through setup.py.

    python3 merry_agent/deploy.py                  # the database named in settings.py
    python3 merry_agent/deploy.py --db MERRY_TEST  # or any other database name

All names (database, role, warehouse, models) come from settings.py at the repository root. Safe to run again on a
database that already has data: tables are only ever created if missing (never dropped, replaced or emptied), and the
seed rows (reference data only) add what is missing. Views, functions, procedures, the semantic view, the agent and
the task are replaced with the versions in this folder. The Cortex Search service is created once and then kept.

Needs: a Snowflake connection (settings.CONNECTION) whose user can use ACCOUNTADMIN."""
import argparse, os, re, sys, time, snowflake.connector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import settings  # noqa: E402

SCHEMAS = ["RAW", "DOCS", "CONTRACT", "CURATED", "AUDIT", "AGENT", "APP"]
SQL_FILES = ["10_tables.sql", "15_seed_rows.sql", "20_functions.sql", "30_views.sql", "40_procedures.sql",
             "50_search_service.sql", "60_semantic_view.sql", "70_agent.sql", "80_task.sql"]
LIB_FILES = ["lib/merry_sql.py"]     # imported by the Python procedures from APP_STAGE

# Anything that could lose data is refused before it is sent.
DANGER = re.compile(r"^\s*(TRUNCATE|DELETE|REMOVE|ALTER\s+TABLE\s+\S+\s+(DROP|SWAP)|DROP)\b|\bCREATE\s+OR\s+REPLACE\s+"
                    r"(TRANSIENT\s+|TEMPORARY\s+|DYNAMIC\s+|ICEBERG\s+)?(TABLE|SCHEMA|DATABASE|STAGE)\b", re.I)


def render(txt, db):
    return settings.render(txt, db)


def blocks(path, db):
    """(owner, sql) per '-- @owner' block of a generated file."""
    parts = re.split(r"^-- @owner (ADMIN|APP)\n", open(path).read(), flags=re.M)
    return [(parts[i], render(parts[i + 1], db).strip()) for i in range(1, len(parts), 2)]


def head(sql):
    """The statement's own first line, skipping comments, for refusal checks and the log."""
    return next((l for l in sql.splitlines() if l.strip() and not l.lstrip().startswith("--")), "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=settings.DB)
    ap.add_argument("--connection", default=settings.CONNECTION)
    db = ap.parse_args().db.upper()
    conn = ap.parse_args().connection
    assert re.fullmatch(r"[A-Z][A-Z0-9_]{0,200}", db), f"bad database name {db}"
    role = settings.values(db)["{{ROLE}}"]
    WAREHOUSE = settings.WAREHOUSE
    con = snowflake.connector.connect(connection_name=conn)
    k = con.cursor()
    who = {"ADMIN": None}

    def as_(owner):
        if who.get("now") == owner:
            return
        if owner == "ADMIN":
            k.execute("USE ROLE ACCOUNTADMIN")
            k.execute("USE SECONDARY ROLES ALL")
        else:
            k.execute(f"USE ROLE {role}")
            k.execute("USE SECONDARY ROLES NONE")
        if who.get("wh"):   # the warehouse only exists once step 1 has created it
            k.execute(f"USE WAREHOUSE {WAREHOUSE}")
        who["now"] = owner

    def run(owner, sql, label=None):
        h = head(sql)
        is_table_stmt = re.match(r"\s*CREATE\s+(TRANSIENT\s+)?TABLE\s+IF\s+NOT\s+EXISTS", h, re.I)
        # The body of a procedure may legitimately DELETE/TRUNCATE at run time; only the DDL head is checked, and
        # table DDL must be CREATE TABLE IF NOT EXISTS.
        if DANGER.search(h) or (re.match(r"\s*CREATE\b.*\bTABLE\b", h, re.I) and not is_table_stmt):
            sys.exit(f"REFUSED (could lose data): {h[:120]}")
        as_(owner)
        k.execute(sql)
        print(f"  ok  {label or h[:110]}")

    t0 = time.time()
    print(f"== MERRY setup into {db} (role {role}, warehouse {WAREHOUSE})")
    print("-- 1. warehouse, database, schemas, stages, role")
    as_("ADMIN")
    for s in [f"CREATE WAREHOUSE IF NOT EXISTS {WAREHOUSE} WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
              f"INITIALLY_SUSPENDED = TRUE",
              f"CREATE DATABASE IF NOT EXISTS {db}"] + \
             [f"CREATE SCHEMA IF NOT EXISTS {db}.{s}" for s in SCHEMAS] + \
             [f"CREATE STAGE IF NOT EXISTS {db}.DOCS.DOC_STAGE ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE') DIRECTORY = (ENABLE = TRUE)",
              f"CREATE STAGE IF NOT EXISTS {db}.APP.APP_STAGE ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')",
              f"CREATE ROLE IF NOT EXISTS {role}",
              f"GRANT ROLE {role} TO ROLE ACCOUNTADMIN"]:
        run("ADMIN", s)
    k.execute(f"USE WAREHOUSE {WAREHOUSE}")
    who["wh"] = True

    grants = blocks(os.path.join(HERE, "sql", "95_grants.sql"), db)
    early = [g for g in grants if re.search(r" ON (ACCOUNT|DATABASE|SCHEMA|WAREHOUSE|STAGE|FUTURE) |DATABASE ROLE", g[1])]
    print(f"-- 2. role grants needed before {role} creates its objects ({len(early)})")
    for o, s in early:
        run(o, s)

    print("-- 3. lib/merry_sql.py to APP_STAGE (rendered for this database)")
    build = os.path.join("/tmp", f"merry_build_{db}")
    os.makedirs(os.path.join(build, "lib"), exist_ok=True)
    for rel in LIB_FILES:
        open(os.path.join(build, rel), "w").write(render(open(os.path.join(HERE, "app", rel)).read(), db))
        sub = "/lib" if rel.startswith("lib/") else ""
        k.execute(f"PUT 'file://{os.path.join(build, rel)}' @{db}.APP.APP_STAGE{sub} AUTO_COMPRESS = FALSE OVERWRITE = TRUE")
        print(f"  put {rel}")

    print("-- 4. objects")
    for f in SQL_FILES:
        bl = blocks(os.path.join(HERE, "sql", f), db)
        print(f"   {f} ({len(bl)})")
        for o, s in bl:
            run(o, s)
        if f == "10_tables.sql":       # the app role writes tables it does not own; grant before seeding
            for o, s in grants:
                if " ON TABLE " in s:
                    run(o, s, label=None)

    print(f"-- 5. object grants ({len(grants) - len(early)})")
    for o, s in grants:
        if (o, s) not in early:
            run(o, s)
    print(f"== database done in {time.time() - t0:.0f}s ({db}, role {role})")


if __name__ == "__main__":
    main()
