# Setting up MERRY in your Snowflake account

MERRY checks contractor invoices against the service contract and the site records, and talks the analyst through
the findings in a chat. Everything lives in **one database** in your account, plus one warehouse, one role and one
Streamlit app. This guide takes you from an empty account to a working app.

There are two ways to run the setup:

- **From a Snowflake workspace, with Cortex Code (CoCo)** (recommended, no local install). Follow section 0, then
  use the ready-made CoCo prompts in each section.
- **From your own terminal**, with Python and a Snowflake connection. Follow sections 1 to 9 and run the commands
  yourself.

Either way, setup **creates everything from nothing**. You don't need any existing database; your account starts with
none of MERRY's objects and no data.

---

## 0. Setting up from a Snowflake workspace with CoCo

### 0.1 Put the code in a workspace
In Snowsight: **Projects > Workspaces**, then either:
- **From Git repository**, pointing at the repository that holds this code, or
- create an empty shared workspace name it MERRY and **upload** the code (unzip it first, keeping the folder structure).

The root of the workspace should contain `AGENTS.md`, `SETUP.md`, `README.md`, `settings.py`,
`streamlit_app.py` and the `merry_agent/` folder.

### 0.2 Open CoCo from that workspace
Open Cortex Code **from inside this workspace**, not from another page. CoCo only reads `AGENTS.md` and can only
save files when it's opened from the workspace that holds them.

Before you start, check CoCo's session scope (near the approvals control in the CoCo panel) isn't **restricted**. A
restricted session can't run AI functions or create objects, even as ACCOUNTADMIN, and looks like missing
permissions.

### 0.3 How to work with CoCo
- **One task per conversation.** Start a new conversation for each section below.
- **Let it plan first.** Read the plan before saying "proceed"; most mistakes are caught there.
- **Ask for proof.** If CoCo says something is set up, ask it to show the query result.
- **Check anything you'd see on screen in the browser yourself.** CoCo can't click in the app.
- If CoCo says "I'm working on a copy" or "the workspace isn't mounted", stop and reopen CoCo from the workspace.

### 0.4 First prompt: account checks (read-only)
Paste this into a new CoCo conversation:

```
This workspace holds the MERRY codebase, being set up in this account for the first time. Read AGENTS.md, SETUP.md and README.md first. Plan first, keep reports short and in plain English.
1. Confirm you're working on this mounted workspace (list the root files) and that AGENTS.md loaded. If the mount isn't there, stop.
2. Run every check in SETUP.md section 1, read-only: role, AI_COMPLETE with both models in settings.py, AI_EXTRACT and AI_PARSE_DOCUMENT on a small test, Cortex Search, Cortex Agents, compute pools, whether cross-region inference is on, and whether a database with the name in settings.py already exists.
3. Report what passes and what's missing, and propose settings.py values for this account. Change nothing.
```

Fix anything missing (see section 1 and section 9) before moving on.

---

## 1. What your account needs

| Need | Why | How to check |
|---|---|---|
| **ACCOUNTADMIN** (for setup only) | Setup creates a database, a warehouse, a role and grants | `SELECT CURRENT_ROLE();` after `USE ROLE ACCOUNTADMIN;` |
| **Cortex AI functions**: `AI_COMPLETE`, `AI_EXTRACT`, `AI_PARSE_DOCUMENT` | Reading contracts, invoices and field tickets; letter wording | See "Check the AI functions and models" below |
| **The two models in `settings.py`**, available in your region | Same | Same |
| **Cortex Search** | Finding the contract clauses behind a finding | `SHOW CORTEX SEARCH SERVICES;` runs without error |
| **Cortex Agents** | The chat (MERRY's agent) | `SHOW AGENTS;` runs without error |
| **Streamlit in Snowflake on the container runtime**, and a **compute pool** you can use | The app | `SHOW COMPUTE POOLS;` (the default pool is `SYSTEM_COMPUTE_POOL_CPU`) |
| Database roles `SNOWFLAKE.CORTEX_USER` and `SNOWFLAKE.CORTEX_AGENT_USER` | Granted to MERRY's role by setup | (granted automatically) |
| **Terminal route only:** Python 3.11 with `snowflake-connector-python` and `snowflake-snowpark-python`, and a connection in `~/.snowflake/connections.toml` | Running the setup from your machine | `python3 -c "import snowflake.connector, snowflake.snowpark"` |

You do **not** need any existing database. In particular, MERRY doesn't use any answer-key or test database from its
original account.

### Check the AI functions and models
MERRY calls two models through Cortex: `MODEL` (agent, letters, chat, reading signatures on scans; default
`claude-sonnet-4-6`) and `CONTRACT_MODEL` (reading contracts; default `claude-sonnet-4-5`). Run these as the setup
user:

```sql
SELECT AI_COMPLETE('claude-sonnet-4-6', 'Reply with OK');
SELECT AI_COMPLETE('claude-sonnet-4-5', 'Reply with OK');
```

Then check document reading works, on any small PDF uploaded to a stage with server-side encryption:

```sql
SELECT AI_PARSE_DOCUMENT(TO_FILE('@<stage>', '<file>.pdf'), {'mode': 'LAYOUT'});
SELECT AI_EXTRACT(file => TO_FILE('@<stage>', '<file>.pdf'), responseFormat => {'title': 'What is the document title?'});
```

(Or let CoCo run these for you with the prompt in section 0.4.)

If a model is **not available in your region**:
- Either turn on cross-region inference (an ACCOUNTADMIN setting; agree it with your Snowflake admin first):
  `ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';` (or a narrower value such as `'AWS_US'`), then run
  the checks again. **What this means:** your data stays stored in your region, but AI calls may be processed in
  another region. Check that's acceptable for your data before turning it on.
- Or pick models that are available to you (see Snowflake's "Cortex AI functions: models and regions" page) and set
  `MODEL` and `CONTRACT_MODEL` in `settings.py`. `MODEL` must also be a model Cortex Agents can use for
  orchestration.

**Models get retired.** If a model has been deprecated, use its named successor. Results were tuned on the defaults;
with other models, run the sample in section 6 and check the totals.

---

## 2. Fill in the settings
Everything account-specific is in **`settings.py`** at the top of the repository. The defaults work in most accounts:

| Setting | Default | What it is |
|---|---|---|
| `CONNECTION` | `default` | Terminal route: the connection name in `~/.snowflake/connections.toml`; its user must be able to use ACCOUNTADMIN |
| `DB` | `MERRY` | The database setup creates. If a database with this name already exists in your account, pick another name |
| `ROLE` | `<DB>_APP` | The role setup creates; it owns and runs the app. Grant it to the people who will use MERRY |
| `WAREHOUSE` | `MERRY_WH` | Created if missing (XSMALL, suspends after 60 s) |
| `COMPUTE_POOL` | `SYSTEM_COMPUTE_POOL_CPU` | Runs the Streamlit app; use any pool the setup user can grant |
| `MODEL`, `CONTRACT_MODEL` | see above | Cortex models |

Each can also be set without editing the file, with an environment variable of the same name and a `MERRY_` prefix,
e.g. `MERRY_DB=MERRY_TEST`.

---

## 3. Run the setup (one command)

**With CoCo**, in a new conversation:

```
Run MERRY's setup in this account. Plan first.
1. Confirm the workspace is mounted and AGENTS.md loaded.
2. Set settings.py to the values we agreed: [paste the values from the account-check report].
3. Run python3 merry_agent/setup.py using your connection to this account.
4. Report the smoke test result (expect 17/17 PASS and "MERRY is set up"), the app's name and how to open it, and the GRANT ROLE line for other users.
If anything fails, show the first error and stop; don't work around it.
```

**From a terminal**, at the repository root:

```
python3 merry_agent/setup.py
```

It creates the warehouse, the database and its schemas, the role and its grants, every table (empty), the
reference data (rule labels, status words, plain phrases), the views, procedures, contract search service, semantic
view, agent and worker task, then the app, and finally runs the smoke test. It takes a few minutes and ends with
`17/17 PASS` and "MERRY is set up". It is safe to run again: it never drops or empties a table.

To check later: `python3 merry_agent/tools/run.py merry_agent/tools/smoke_test.py`.

### Files you shouldn't edit by hand
Setup generates these, and overwrites them each time it runs:
- `merry_chat/` (the chat tab inside the app, built from `merry_agent/app/`)
- `snowflake.yml` (the app definition, built from `settings.py`)

To change the chat, edit `merry_agent/app/` and run setup again.

---

## 4. Open the app
Snowsight > **Projects > Streamlit > MERRY** (in the database from `DB`). Users need the role from `ROLE`:
`GRANT ROLE MERRY_APP TO USER <name>;` (use your role name). A new app shows **"No contracts yet. Drop your first
contract in Ask MERRY."**

---

## 5. First use, in this order
1. **Site records**: upload the CSVs in the sidebar (Add files > Submit): timesheets, gate log, daily reports,
   equipment log. They are recognised by their column headers. Rows already loaded are ignored. Rate files are
   refused: rates come from the contract.
2. **The service contract** (PDF): in Ask MERRY or the sidebar. MERRY reads it; with no rate warnings it confirms it
   automatically, otherwise it asks you to review its values. Site records loaded before the first contract take that
   contract's site.
3. **Invoices and field tickets** (PDFs): drop them all at once. Wait for "Got all N files. You can close this tab
   now." before closing the page. MERRY sorts them into service weeks and starts the audit (or asks first if
   something is unclear).
4. Review the findings in the chat or the Issues tab, then approve the letters.

---

## 6. Try it with the sample data
`merry_agent/data/out/` holds a synthetic pack: one contract, 19 invoices, 41 field tickets and the four site-record
CSVs (`merry_agent/data/extra/` has a second contract without invoices). We recommend running the sample once to
prove the setup, then resetting (section 7) before real use.

**With CoCo:**

```
Prove MERRY's setup with the sample data. Run merry_agent/tools/try_sample.py, then the smoke test with --findings 9 --upheld 287000. Report the result. If the numbers differ, show the per-finding differences; don't tune anything. Don't reset afterwards; I'll check the app in my browser first.
```

**From a terminal:**

```
python3 merry_agent/tools/run.py merry_agent/tools/try_sample.py
python3 merry_agent/tools/run.py merry_agent/tools/smoke_test.py --findings 9 --upheld 287000
```

Expected: 9 findings and ₱287,000.00 upheld. The audit makes AI calls, so a different model can give different
numbers.

Then check in your browser: open the app, ask the chat for a status report, and compare it with the Dashboard and
Issues tabs. The numbers should match.

---

## 7. Starting over
`tools/reset.py` (admin only) empties all user data: contracts, uploads, documents, extractions, decisions, letters,
chats, activity, jobs and site records, and every file on the document stage. It keeps the reference data, the
objects and the app, so the app is back to its first-ever state without a redeploy. It asks you to type the database
name first.

**With CoCo:**

```
Reset MERRY to its first-ever state with merry_agent/tools/reset.py (everything, not one contract). Show me what it will empty and wait for my OK. Afterwards, confirm 0 contracts, the reference data counts, and that the smoke test passes.
```

**From a terminal:**

```
python3 merry_agent/tools/reset.py                     # everything
python3 merry_agent/tools/reset.py --contract <ID>     # one contract (site records are kept: they belong to the site)
```

---

## 8. Costs and how to stop things
| What | Costs when | How to stop |
|---|---|---|
| Warehouse `WAREHOUSE` | Queries and the audit worker run; suspends after 60 s idle | `ALTER WAREHOUSE MERRY_WH SUSPEND;` |
| Cortex AI functions and the agent | Per token, when documents are read, audits run and chat questions are asked (most of it at audit time) | Don't upload or run audits |
| Cortex Search (`AGENT.CONTRACT_SEARCH`) | Serving the index while it exists; refreshes daily | `ALTER CORTEX SEARCH SERVICE <DB>.AGENT.CONTRACT_SEARCH SUSPEND SERVING;` |
| The Streamlit app on the compute pool | While someone has it open; it sleeps when idle | Close it; `ALTER COMPUTE POOL <pool> SUSPEND;` if the pool is yours alone |
| Worker task `APP.TK_RUN_JOBS` | Only when the app starts it (no schedule) | `ALTER TASK <DB>.APP.TK_RUN_JOBS SUSPEND;` |
| Storage | Uploaded files and tables | `tools/reset.py`, or `DROP DATABASE <DB>;` to remove everything |

On a trial account, keep an eye on remaining credits and days: when a trial ends, AI functions stop working for every
role.

To remove MERRY completely: `DROP DATABASE <DB>; DROP ROLE <ROLE>;` (and the warehouse if nothing else uses it).

---

## 9. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `Model … is unavailable` / `not available in your region` | Section 1, "Check the AI functions and models" |
| `Unknown function AI_COMPLETE` mentioning `GRANT USE AI FUNCTIONS` | The role lacks the account privilege for AI functions. As ACCOUNTADMIN: `GRANT USE AI FUNCTIONS ON ACCOUNT TO ROLE <role>;` (it is often already granted to PUBLIC), then retry |
| CoCo says permissions are missing, but they look fine in a worksheet | CoCo's session scope is restricted (section 0.2). Run `SHOW GRANTS TO ROLE <role>;` in a normal worksheet to confirm, then reopen CoCo with a full session scope |
| CoCo can't find the files or says it's working on a copy | It wasn't opened from this workspace (section 0.2) |
| Setup fails on `GRANT USAGE ON COMPUTE POOL` | The pool in `COMPUTE_POOL` doesn't exist or the setup user can't grant it: `SHOW COMPUTE POOLS;`, pick one |
| `Insufficient privileges` during setup | The connection's user can't use ACCOUNTADMIN, or a secondary role masks it; run as ACCOUNTADMIN |
| A database named like `DB` already exists | Pick another name in `settings.py` and run setup again |
| The app says the audit data is not set up | Setup didn't finish: run setup again and read the first error |
| A CSV says "couldn't tell which site" | There are several contracts on different sites: add a `site_id` column to the CSV |
| A rate CSV is refused | By design: rates come from the contract |
| The contract stays "extracted" | It has rate warnings: open it in Ask MERRY and choose Review its values or Confirm as extracted |
| Uploads stop partway | The page was closed before "Got all N files". Reopen the chat; MERRY reports what arrived. Drop the rest again |
| Chat answers take long or time out | The first agent calls after setup warm up; check the warehouse is running and the model check passes |
| A PDF source won't open | The app serves PDFs itself (download button, inline preview where supported); check the file exists on `DOCS.DOC_STAGE` |
| You changed a file in `merry_chat/` and it disappeared | That folder is generated; edit `merry_agent/app/` and run setup again |
| App shows 'Connection failed … status 500' right after setup or a redeploy | The app's service is still starting; wait 2–3 minutes and reload |

### Asking CoCo for help
For anything not in this table, start a new CoCo conversation from the workspace and paste:

```
MERRY problem in this account. Read AGENTS.md and SETUP.md first. Symptom: [what you saw, with the exact error text]. Diagnose before changing anything: show me the cause with evidence (query results, logs), propose the smallest fix, and wait for my OK. Never drop or empty tables; test risky changes on a throwaway zero-copy clone first.
```

### Changing MERRY later
After editing code in `merry_agent/` (or asking CoCo to), apply the change with:

```
Apply my code changes to MERRY in this account: test them first on a zero-copy clone of the database (data on the stage isn't copied, so PDF checks can only run on the real database), run the smoke test there, then run setup on the real database and the smoke test again. Report in plain English, and tell me what to check in the browser.
```
