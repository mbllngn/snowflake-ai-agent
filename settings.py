"""MERRY settings: the only place for account-specific names. Fill these in for your account before running setup
(see SETUP.md). Every value can also be overridden by an environment variable of the same name with a MERRY_ prefix,
e.g. MERRY_DB=MERRY_TEST python3 merry_agent/setup.py, without editing this file.

The SQL, app and tool files use {{DB}}, {{ROLE}}, {{WAREHOUSE}}, {{COMPUTE_POOL}}, {{MODEL}} and {{CONTRACT_MODEL}};
setup replaces them with the values below (render())."""
import os
import re


def _env(name, default):
    return os.environ.get(f"MERRY_{name}", default)


CONNECTION = _env("CONNECTION", "default")            # connection name in ~/.snowflake/connections.toml (ACCOUNTADMIN)
DB = _env("DB", "MERRY").upper()                      # one database holds everything MERRY creates
ROLE = _env("ROLE", f"{DB}_APP").upper()              # role that owns and runs the app (created by setup)
WAREHOUSE = _env("WAREHOUSE", "MERRY_WH").upper()     # created by setup if missing (XSMALL, auto-suspend 60 s)
COMPUTE_POOL = _env("COMPUTE_POOL", "SYSTEM_COMPUTE_POOL_CPU").upper()   # for the container-runtime Streamlit app
MODEL = _env("MODEL", "claude-sonnet-4-6")            # agent, letters, chat rewrites, document vision reads
CONTRACT_MODEL = _env("CONTRACT_MODEL", "claude-sonnet-4-5")   # contract reading (onboarding)
APP_NAME = "MERRY"                                    # Streamlit object: <DB>.APP.MERRY

for _n, _v in (("DB", DB), ("ROLE", ROLE), ("WAREHOUSE", WAREHOUSE), ("COMPUTE_POOL", COMPUTE_POOL)):
    assert re.fullmatch(r"[A-Z][A-Z0-9_$]{0,200}", _v), f"settings: bad {_n} {_v!r}"
for _n, _v in (("MODEL", MODEL), ("CONTRACT_MODEL", CONTRACT_MODEL)):
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,100}", _v), f"settings: bad {_n} {_v!r}"


def values(db=None):
    """Placeholder -> value. db overrides DB (and the default role that follows it)."""
    d = (db or DB).upper()
    role = ROLE if (db is None or "MERRY_ROLE" in os.environ) else f"{d}_APP"
    return {"{{DB}}": d, "{{ROLE}}": role, "{{WAREHOUSE}}": WAREHOUSE, "{{COMPUTE_POOL}}": COMPUTE_POOL,
            "{{MODEL}}": MODEL, "{{CONTRACT_MODEL}}": CONTRACT_MODEL}


def render(text, db=None):
    """Replace every placeholder; {{ROLE}} before {{DB}} is irrelevant since neither contains the other."""
    for k, v in values(db).items():
        text = text.replace(k, v)
    return text
