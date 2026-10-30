"""Load the app's own modules (review.py, chat.py, common.py) against a real Snowpark session, as {{ROLE}}, with the
st_stub streamlit (step 12c polish). Query results are real Snowpark Rows, exactly as in the app."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, HERE)
sys.path.insert(0, APP)
from snowflake.snowpark import Session  # noqa: E402
import snowflake.snowpark.context as ctx  # noqa: E402
session = Session.builder.config("connection_name", "default").create()   # before the stub: Snowpark probes streamlit
import st_stub  # noqa: E402
st = st_stub.install()
for s in ("USE ROLE {{ROLE}}", "USE SECONDARY ROLES NONE", "USE WAREHOUSE {{WAREHOUSE}}"):
    session.sql(s).collect()
ctx.get_active_session = lambda: session
import common  # noqa: E402
common.kick_worker = lambda: session.sql("EXECUTE TASK {{DB}}.APP.TK_RUN_JOBS").collect()
common.current_user = lambda: session.sql("SELECT CURRENT_USER() u").collect()[0]["U"]
import review  # noqa: E402
review.kick_worker, review.current_user = common.kick_worker, common.current_user
import chat  # noqa: E402

# Step 12d: review.call_agent imports _snowflake, which exists only inside Streamlit in Snowflake. Stand-in with the same
# send_snow_api_request signature, posting over REST with this session's token (role {{ROLE}}, as in the app).
import types, requests  # noqa: E402


def _send(method, url, headers, params, body, request_guid, timeout_ms):
    con = session._conn._conn
    r = requests.request(method, f"https://{con.host}{url}", json=body, timeout=timeout_ms / 1000,
                         headers={"Authorization": f'Snowflake Token="{con.rest.token}"', "Content-Type": "application/json",
                                  "Accept": "application/json"})
    return {"status": r.status_code, "content": r.text}


sys.modules["_snowflake"] = types.SimpleNamespace(send_snow_api_request=_send)
