# MERRY: decisions

These decisions override or refine the other docs. Where this file conflicts with `docs/ARCHITECTURE.md` (or PRODUCT.md, README.md, RUNBOOK.md), this file wins.

## D1 Naming
**Decision:** MERRY names everywhere (database `MERRY`, warehouse `MERRY_WH`, agent `AGENT.MERRY_AGENT`, app `APP.MERRY`). The data pack's "FieldAudit" is the same product. EVAL tables are in `MERRY.EVAL`: ANSWER_KEY (9 rows), QA_SAMPLES (13), TRUTH_RATE_CARD (13), TRUTH_TICKETS (41), TRUTH_INVOICE_LINES (91). Spikes go in `MERRY.SCRATCH`.
**Reason:** The runbook and AGENTS.md used different names; one name avoids broken references.

## D2 Amounts are computed in SQL, never by the agent
**Decision:** The agent never sends peso amounts. `RECORD_DECISION` takes a decision plus a structured basis, e.g. `{basis:"STANDBY_EQUIPMENT", dates:[...]}`, `{basis:"TRAVEL_ALLOWANCE", hours:4}`, `{basis:"NONE"}`. SQL computes upheld/overbilled/withheld/allowed from `CONTRACT.RATES` and `RULE_PARAMS`:
- CONFIRM: upholds the full flagged amount (overbilled = flagged).
- WITHHOLD: withholds the full flagged amount (withheld = flagged).
- DISMISS: upholds 0.
- CORRECT: computes the allowed part from the basis; upheld = flagged − allowed.

An unknown basis or missing params rejects the call with an error message the agent can act on.
**Reason:** Enforces hard rule 4 (the LLM never invents money) and makes decisions reproducible.

## D3 Standby rate
**Decision:** `STANDBY_EQUIPMENT_PCT × rate_php` is authoritative for every calculation. `RATES.printed_standby_rate_php` stores the Exhibit A standby rate when one is printed (NULL otherwise). `RATES.standby_rate_php` is derived as `rate_php × STANDBY_EQUIPMENT_PCT` on equipment rows, and is NULL when the contract has no such param. It is recomputed whenever the rate or the percentage is edited, and checked against TRUTH_RATE_CARD at the step 4 gate. The consistency warning (`CONTRACT.RATE_WARNINGS`, shown on the Contracts tab) applies only when a printed value exists and differs from the derived one. A printed value with no percentage param is also a warning. "Not printed" is logged as INFO only.
**Reason:** Avoids two sources of truth; keeps the editable percentage meaningful (step 6 configurability gate).

## D4 EVAL firewall role
**Decision:** Step 3 creates role `MERRY_APP` with usage on `MERRY_WH` and every MERRY schema except `EVAL`, plus the Cortex database roles it needs. Procedures (EXECUTE AS OWNER), the search service, semantic view, agent, and Streamlit app are owned by `MERRY_APP`. Building and `validate*.sql` run as ACCOUNTADMIN. Every step ends with a grep check for `EVAL`, `E1a`–`E8`, and literal invoice/ticket numbers outside `validate*.sql`. Invoice numbers are fine in gate runs and user chat, never in agent instructions, tool descriptions, or verified queries.
**Reason:** Turns the firewall from a convention into an enforced privilege boundary, with a cheap static check on top.

## D5 Travel boundary
**Decision:** Extra billed hours over gate hours are allowed when extra hours ≤ 2 × `TRAVEL_MAX_HOURS_EACH_WAY` (inclusive). Computed deterministically in SQL.
**Reason:** The demo case sits exactly on the boundary; LLM judgement there would be unstable.

## D6 Role-to-rate mapping
**Decision:** Add `CONTRACT.ROLE_RATE_MAP(contract_id, contractor_code, role, rate_code)`, populated at onboarding by AI_COMPLETE constrained to existing rate codes, and editable in the Contracts tab like other values (via SP_APPLY_EDIT, logged).
**Reason:** The RATE check needs to know which rate applies to a worker's role; keeping it in CONTRACT avoids hardcoding.

## D7 Exclusions
**Decision:** `ELIGIBILITY_EXCLUSION` uses a case-insensitive word match of line descriptions against `EXCLUDED_CHARGES`, plus any line matching "fuel" when `RATES_INCLUDE_FUEL` is true.
**Reason:** Deterministic and driven entirely by extracted params.

## D8 Rule param extraction
**Decision:** Fixed param vocabulary in a JSON schema, temperature 0; `source_clause` must exist in `CLAUSES`, otherwise the param is dropped. Expected for MSA-019: `TICKET_COMPANY_SIGNATURE_REQUIRED`, `TICKET_BILL_ONCE`, `RATES_INCLUDE_FUEL`, `PAYMENT_OUTSIDE_TERM` only; no standby, hours, travel, or exclusion params. Step 4 reports what is actually extracted.
**Reason:** Prevents invented params and makes the MSA-019 gate concrete.

## D9 Staleness
**Decision:** An edit sets `CONTRACTS.status` back to `EXTRACTED` (needs re-confirming) and sets `is_stale = TRUE` on that contract's audited `PERIOD_RUNS`. There is no `STALE` status on CONTRACTS; allowed values are `EXTRACTED | CONFIRMED`.
**Reason:** One place for period staleness, one for contract confirmation.

## D10 New objects
**Decision:** Accepted additions:
- `CONTRACT.ONBOARD_LOG` (onboarding progress steps for the app).
- `AUDIT.PENDING_REVISIONS` (written by PROPOSE_REVISION; the analyst confirms or rejects in the app).
- "Make it firmer" writes a new DRAFT version of `RESOLUTIONS`.
- `RESOLUTIONS` gains `pay_vat_php` and `pay_total_php`; the letter amount check covers them.

**Reason:** These were implied by the runbook and product spec but missing from the table contracts.

## D11 Data editor
**Decision:** Contract edits are applied on an explicit Save button by diffing `edited_rows`, never on every change.
**Reason:** Avoids rerun loops and lost edits with `st.data_editor`.

## D12 Status labels and session state
**Decision:** Dismissed flags not yet approved show "Flag cleared"; after approval, "Approved". Header selections and in-flight agent responses may use `st.session_state`; card and letter state may not.
**Reason:** Clear grid semantics; keeps the required state pattern while allowing practical UI state.

## D13 Handwritten tickets
**Decision:** The three handwritten tickets in `out/tickets/` (LCR-T-3306, PCS-T-7111, AQH-T-0416) are still the generated placeholders, not real photos.
**Reason:** Step 5 extraction results for the "handwritten" split must be read with this in mind; real photos may be swapped in later (by the human, not by CoCo).

## D14 Streamlit runtime and version
**Decision:** `APP.MERRY` uses the warehouse runtime with `RUNTIME_NAME = 'SYSTEM$WAREHOUSE_RUNTIME'` set explicitly, `QUERY_WAREHOUSE` set, and no compute pool. Packages come from `environment.yml` with `streamlit=1.52.2` pinned (the newest version in the Snowflake Anaconda channel per `INFORMATION_SCHEMA.PACKAGES`, and listed for warehouse runtimes in the docs, 2026-10-20). Agent calls use `_snowflake.send_snow_api_request("POST", "/api/v2/databases/<db>/schemas/<schema>/agents/<agent>:run", {}, {}, body, None, 120000)` with `stream: false`. Step 11 reuses the DDL in `02_spikes/04_streamlit_app.sql`.
**Reason:** Since BCR 2026_06 an omitted `RUNTIME_NAME` defaults to the container runtime, which ignores `environment.yml`. Without a pin, the warehouse runtime loaded an old Streamlit with no `st.chat_message`. MERRY needs `chat_message`, `chat_input`, `status`, `rerun`, `toast` and `data_editor`, all present in 1.52.2.

## D15 MERRY_APP grant set
**Decision:** Role `MERRY_APP` (created in `03_foundation/05_role.sql`, granted to ACCOUNTADMIN) holds exactly:
- USAGE on warehouse `MERRY_WH`, database `MERRY`, and schemas RAW, DOCS, CONTRACT, CURATED, AUDIT, AGENT, APP. Nothing on `EVAL` or `SCRATCH`.
- RAW: SELECT on all and future tables and views.
- DOCS, CONTRACT, CURATED, AUDIT, APP: SELECT, INSERT, UPDATE, DELETE, TRUNCATE on all and future tables; SELECT on all and future views.
- CREATE TABLE, CREATE VIEW, CREATE PROCEDURE in CONTRACT, CURATED, AUDIT, APP; CREATE FUNCTION in AUDIT (step 10, for `AUDIT.FN_AMOUNTS`); CREATE AGENT, CREATE CORTEX SEARCH SERVICE, CREATE SEMANTIC VIEW, CREATE PROCEDURE in AGENT; CREATE STREAMLIT in APP.
- READ, WRITE on stage `DOCS.DOC_STAGE`.
- Database roles `SNOWFLAKE.CORTEX_USER` and `SNOWFLAKE.CORTEX_AGENT_USER`.
- (Step 11, D25) EXECUTE TASK on account and CREATE TASK in APP, so the app can run its background worker `APP.TK_RUN_JOBS`.

Role tests must run with `USE SECONDARY ROLES NONE`, otherwise the session's secondary roles (ALL) mask the boundary.
**Reason:** Implements D4 with the step 2 findings; the step 3 gate proves RAW is readable and `MERRY.EVAL.ANSWER_KEY` fails with an authorization error.

## D16 Role-to-rate map coverage
**Decision:** `CONTRACT.ROLE_RATE_MAP` covers the (contractor, role) pairs seen in `RAW.TIMESHEETS` at onboarding time. A role that first appears later is not mapped automatically. The step 6 RATE check flags an unmapped role only when an invoice line bills a personnel rate (unit hour or day, i.e. a non-equipment rate billed per person) for a worker whose `RAW.TIMESHEETS` role has no `ROLE_RATE_MAP` entry; that case is an exception, never skipped. Roles of contractors that bill only per unit (e.g. per load, tonne, run, head-day) are never flagged on their own, because no line prices their labour. The analyst can add the mapping in the Contracts tab (via SP_APPLY_EDIT) and re-run.
**Reason:** A silent skip would hide exactly the role/rate mismatches the RATE check exists to catch.

## D17 Worker matching across documents and site records
**Decision:** Workers are matched across field tickets, `RAW.TIMESHEETS` and `RAW.GATE_LOG` by worker ID where one is printed. Name matching is only a fallback, and is fuzzy (normalised case, punctuation and initials, plus a similarity threshold) within the same contractor and date. A name that doesn't match exactly (e.g. an OCR misread such as "Marion" for "Marlon") is logged, but never creates an exception on its own. Step 6 checks must follow this.
**Reason:** Extracted names from scans carry OCR noise. Exceptions must come from billing differences, not spelling differences.

## D18 Ticket extraction design
**Decision:** `CURATED.SP_EXTRACT_PERIOD` reads each document with small, independent AI calls, all submitted concurrently per period:
- Invoice: one AI_EXTRACT (header, totals, line table; dates and amounts exactly as printed).
- Ticket, four calls:
  - `head`, AI_EXTRACT: ticket number, contractor, date, printed date range (header or Remarks), rep name, handwritten flag, remarks, plus the personnel and equipment tables.
  - `qty`, AI_EXTRACT: only rows of the printed quantities/items section, mirroring its columns (Item, Qty, Unit), one output row per printed row. Personnel and Equipment rows are excluded, and a ticket with no quantities section returns no rows. Code also drops any quantity row that repeats a personnel name on the ticket or is billed in hours.
  - `sig`, AI_EXTRACT: a three-way read (SIGNATURE, PRINTED_NAME_ONLY or BLANK) of the contractor box and the company representative box.
  - `vision`, AI_COMPLETE (`claude-sonnet-4-6`) on the PDF via TO_FILE, asking only about the line captioned "Company Representative" (same three answers).

The parts are merged into one `DOCS.PARSED_DOCS` row per file.

Rules:
- **Signature (chosen by measurement):** `signed_by_company_rep` = the `vision` read (TRUE only when it is SIGNATURE). The `sig` AI_EXTRACT read is a cross-check: when it differs from the vision read, an `EXTRACT_ISSUES` row `SIGNATURE_UNCLEAR` ("signature unclear, verify") is written for the app to show, but the value is not changed.
  - Measured on 3 forced rounds × 41 tickets vs truth (step 5 gate, `05_extract/validate.sql` G2c):

    | Method | Correct per round | Stable across rounds |
    |---|---|---|
    | Vision alone | 41/41, 41/41, 41/41 | 0 tickets changed |
    | AI_EXTRACT alone | 40/41, 40/41, 39/41 | 1 ticket changed |
    | Both must agree (earlier rule) | 41/41, 41/41, 40/41 | 1 ticket changed |

    AI_EXTRACT alone was wrong on LCR-T-3306 and, in one round, AQH-T-0410.
  - The earlier both-must-agree rule was replaced because it turned that one AI_EXTRACT wobble into a false "unsigned", and so a false TICKET_BACKING hold.
- **Weekly sheets:** each row's date is the one date in the printed range matching the row's label (day number, else weekday), computed in SQL. Without a printed range, a label that carries its own day and month gives the date directly. `SHEET_DAYS_UNRESOLVED` is logged when several rows share one date, or when a range-printing sheet returns a single row.
- **Dates:** per-contractor day/month order comes from unambiguous numeric dates across all of that contractor's documents (`CURATED.DATE_ORDER`). Line dates are never forced into the period.
- **Speed:**
  - `MERRY_WH` runs with `MAX_CONCURRENCY_LEVEL = 32`.
  - AI calls are read-only async SELECTs; results are written with one MERGE, because concurrent MERGEs lock each other out.
  - A call still running after 12 s gets a duplicate (and a third after 36 s). The first to finish is used and the rest are cancelled. The period fails after 120 s.
  - Rejected alternatives: one combined ticket schema (changes in one part regressed others); a captioned signature-line table (read most signed tickets as unsigned); cropping the box using AI_PARSE_DOCUMENT LAYOUT coordinates (LAYOUT returns Markdown without text coordinates).

**Reason:** A false "unsigned" creates a false TICKET_BACKING hold, and a false "signed" hides a real one. The most reliable read (vision, 41/41 in every measured round) sets the value, and the second read surfaces doubtful cases as "verify" instead of silently changing them. Small schemas keep each part's accuracy independent, and hedged concurrent calls keep the live W2 run under 30 s.

## D19 Exception granularity and re-runs
**Decision:** `SP_RUN_CHECKS` writes one exception per (invoice, rule_type), not per line. `line_no` is the lowest affected line (kept for compatibility); `line_nos ARRAY` lists every affected line; `evidence.lines[]` holds each line's flagged amount with its source-row keys and dates. Two distinct issues of the same rule_type on one invoice are merged into one exception whose evidence lists both. `exception_id = contract_id|period_start|invoice_no|rule_type`, stable across re-runs. A re-run replaces the period's exceptions; when an exception's `flagged_php` or `evidence` changes, or the exception disappears, and `AUDIT.DECISIONS` rows exist for that `exception_id`, those rows are set `is_stale = TRUE` so the step 9 review re-decides them.
**Reason:** Several planted errors span several lines (one worker over three days, one unsigned ticket behind three lines, five out-of-term days). A per-line split turns one finding into many and breaks the one-to-one mapping the agent, resolutions and gate rely on.

## D20 EDIT_LOG is append-only
**Decision:** No MERRY procedure, agent tool or app code ever deletes from `CONTRACT.EDIT_LOG`. Only `validate*` scripts may delete, and only the test rows they inserted themselves.
**Reason:** EDIT_LOG is the audit trail behind every contract value that drives the checks. A trail that the product itself can rewrite is not an audit trail.

## D21 Agent review: retry, fallback and tool argument types
**Decision:**
- When `RECORD_DECISION` returns `REJECTED`, the agent fixes the call from the error message and retries once.
- If the retry is also rejected, it records `CONFIRM` with basis `NONE` and a rationale starting `Needs analyst review:` that says what it could not establish. `SP_EVALUATE_DECISION` accepts this fallback without a clause citation, but still requires a valid source-row ref.
- The agent never calls `RECORD_DECISION` more than three times for one exception, and never picks a decision only because it passes validation.
- On a system error (a tool failure, not a `REJECTED` result), it stops and reports the error without recording or recommending amounts.
- Agent instructions and tool descriptions are generic: no invoice or ticket numbers, no error IDs.
- Cortex Agent generic tools on a warehouse reject `object` and `array` arguments ("argument type array is not supported for execution environment type warehouse", seen in the step 8 gate). So `RECORD_DECISION` and `PROPOSE_REVISION` take `basis`, `clauses` and `evidence_refs` as JSON text (STRING) and parse it. A bare basis name (e.g. `NONE`) and comma-separated lists are also accepted.
- Evidence refs use the form `<SCHEMA.TABLE>:<key>` (e.g. `RAW.EQUIPMENT_LOG:<equipment_id>@<date>`). `GET_EXCEPTION_CONTEXT.citable_refs` lists the only valid refs for an exception.
**Reason:** A bounded retry, then a visible hand-off to the analyst, avoids loops and avoids decisions chosen to satisfy the validator. The STRING signatures are forced by the platform. Validation semantics are unchanged.

## D22 Review runner and first-write-wins
**Decision:** `AGENT.SP_REVIEW_PERIOD(contract_id, period_start, force BOOLEAN, agent_name DEFAULT 'MERRY_AGENT')`:
- Python procedure, owned by `MERRY_APP`, EXECUTE AS OWNER.
- Calls the agent through `_snowflake.send_snow_api_request` (D14), with one thread per exception.
- **Scope:** exceptions with no decision, or whose latest decision `is_stale`. With `force = TRUE`, all of the period's exceptions.
- **Hedging:**
  - A call still running after 75 s gets one duplicate.
  - The first attempt that returns RECORDED wins.
  - An attempt that ends without a RECORD_DECISION is retried once.
  - Each exception has a hard limit of 240 s.
  - The runner never writes a decision itself.
- **PERIOD_RUNS:**
  - While running: `status = REVIEWING`, `current_step = 'REVIEW n/N'`.
  - When done: `REVIEWED`, or `REVIEW_FAILED` if any exception failed.
- **Logging:** every attempt goes to `AUDIT.REVIEW_CALLS`.
- **First write wins, in the database:**
  - Per batch, the runner opens one `AUDIT.REVIEW_CLAIMS` row per exception, with a `review_token`. The agent message is `Review exception <id> review_token <token>`.
  - `RECORD_DECISION` takes an optional 7th argument, `REVIEW_TOKEN`. In one transaction it flips the claim from OPEN to DECIDED and inserts the DECISIONS row, which stores the token.
  - A later call with the same token writes nothing and returns `ALREADY_DECIDED`. The agent is told this is final and not to retry it.
  - A hedge loser therefore never appears in the decision history.
  - Calls without a token (chat-mode tests, manual calls) behave as before.
  - Tested with 6 concurrent calls on one token: 1 RECORDED, 5 ALREADY_DECIDED, 1 row (`09_review/validate_token_race.py`).
  - Retry and hedge paths tested live against the agent (`09_review/validate_fault.py`, step 10 follow-up): a patched copy with a 10 s hedge and a failing first attempt gave FIRST = HTTP_ERROR, then RETRY RECORDED and HEDGE ALREADY_DECIDED or ABANDONED, with one DECISIONS row per token. The procedure is restored from the file afterwards. The test found two logging bugs, now fixed: abandoned attempts had no start time, and NULL binds in the multi-row VALUES insert into REVIEW_CALLS failed. That insert now uses one JSON bind.
- **Semantic view:** `AUDIT_SV.decisions` now reads `AUDIT.V_DECISIONS`, which adds `is_current` (latest version) and `is_upheld` (`upheld_php > 0`). The description lists the stored vocabulary (CONFIRM/CORRECT/WITHHOLD/DISMISS), and the upheld verified query counts distinct periods.
  - Before this, Analyst read "upheld" as `decision = 'CONFIRM'` and missed the repeat pattern.

**Reason:** Hedged calls can both reach RECORD_DECISION. Only a database-level claim guarantees that exactly one write survives, without rewriting history afterwards.

## D23 Review-mode orchestration model
**Decision:** Review mode stays on `claude-sonnet-4-6` (`AGENT.MERRY_AGENT`). `AGENT.MERRY_AGENT_FAST` (`claude-haiku-4-5`, identical spec) was tested and is not used. It was dropped in step 10; `09_review/02_agent_fast.sql` is kept only to recreate the experiment.

Measured on 3 forced 4-period runs each (step 9 gate):

| | sonnet-4-6 | haiku-4-5 |
|---|---|---|
| Decisions and amounts match key | 9/9, 9/9, 9/9 | 9/9, 9/9, 9/9 |
| "Needs analyst review" fallbacks | 0, 0, 0 | 0, 0, 1 (MATH: did its own arithmetic, then handed off) |
| Avg / max per exception | 40.5 s / 52.7 s | 41.2 s / 69.6 s |
| W2 wall (3 in parallel) | 52.7, 55.0, 52.5 s | 68.6, 71.7, 50.6 s |
| 4-period wall | 169.4, 152.2, 154.9 s | 163.9, 176.9, 147.4 s |
| Rationales | name the clause and the specific rows (dates, PU-101 status, gate entries G-00074..76, ticket) | sometimes generic ("4 extra hours ... within the cap", no rows), or pasted raw ref strings |

**Reason:**
- Haiku was not faster: tool and procedure time dominates.
- It produced one fallback.
- It broke the no-arithmetic rule in that fallback.
- Its rationales failed the plain-language citation bar.

The acceptance rule was: all 3 runs match, and the rationales cite the clause and the specific site records.

## D24 Resolutions, letters and the amount check
**Decision:**
- `AUDIT.FN_AMOUNTS(text)` is the single money-token extractor for every amount check. It matches PHP/₱-prefixed numbers, comma-grouped numbers, and numbers with 4+ digits and 2 decimals; dates, document numbers and clause numbers don't match.
- `SP_DRAFT_RESOLUTIONS`:
  - Totals are pure SQL over current decisions. VAT is the invoice's own `vat / subtotal`.
  - It fails if any exception in the period is undecided or stale.
  - Letter findings cite the decision's clauses ∩ (the basis param's `source_clause` ∪ `evidence.params` source clauses). If that intersection is empty, they cite the decision's own clauses. Only MATH findings cite none; any other finding with no clause fails the draft.
  - The one-sentence reasons come from AI_COMPLETE and must contain no amounts. One retry, then a template sentence.
  - Every letter amount must be one of the invoice's DECISIONS/RESOLUTIONS amounts.
- Versioning uses a fingerprint of the current decision ids, versions and amounts:
  - unchanged: no-op;
  - DRAFT: new DRAFT version;
  - APPROVED: the approved row is set `is_stale` (never overwritten) and a new DRAFT is added.
  - `findings` (per finding: amount, clauses, reason, reason source) is stored on the row.
- `SP_REVISE_LETTER` compares amount *sets*: the revised letter's amounts must equal the original's set; repeats are allowed. One retry, else REJECTED with nothing written. On success, a new DRAFT version with `revision_note`.
- A revision may change tone, not facts (step 10 follow-up, before step 11):
  - Deterministic: `AUDIT.FN_CLAUSE_REFS(text)` extracts cited clause numbers (`n.m`, not inside amounts, dates, percentages or document numbers) and `Exhibit X`. The revised letter's refs must be a subset of the original's. This is checked alongside the amount set, in the same retry loop; a failure names which check failed.
  - Prompt: no new clauses, obligations, remedies, deadlines, response periods, interest, penalties, escalation or legal threats unless the analyst's instruction explicitly asks for that item; "firmer" is not such a request.
  - Gate (`10_resolutions/validate_revise.py`): "Make it firmer" on PCS-INV-1042 v3 gave v4, REVISED on the first attempt, same amount set, clauses {4.2} = {4.2}, no deadline/penalty/legal wording added. "...also cite clause 9.9 and Exhibit C" was REJECTED after 2 attempts, nothing written.
  - Known limit: only amounts and clause refs are checked deterministically. In the gate run the model turned "is partially disputed" into "is disputed" (amount unchanged). The analyst reviews the text before approving.
- `SP_APPROVE_RESOLUTION`:
  - An edited letter must pass the same set check.
  - The PDF is rendered with fpdf2 (core Helvetica, Latin-1, "PHP") to `resolutions/<contract_id>/<invoice_no>_v<version>.pdf` via `session.file.put_stream`.
  - APPROVE-type invoices are approved with no PDF.
- `SP_PROCESS_PERIOD(contract_id, period_start, force DEFAULT FALSE)` chains extract → checks → review → draft. PERIOD_RUNS ends DRAFTED, or FAILED with the failing step. `SP_BRIEFING` returns SQL totals, verify flags, repeat patterns (≥ 2 upheld periods via GET_PAST_DECISIONS) and an amount-checked AI summary.

**Reason:** Hard rule 4 for every text the product writes. Approved letters stay as audit history.

## D25 Long-running calls from the app, and identical re-onboarding
**Decision:**
- **Background worker, not the app session (revised after the step 11 refresh test).** The first design started `SP_ONBOARD_CONTRACT` with `collect_nowait()` from the Streamlit session. The refresh test showed that a browser reload ends that session, and Snowflake cancels its queries, async ones included: query 01c7df20… failed with 90230 "Stored procedure child job is cancelled upon parent jobs termination" 0.35 s after step 4 logged OK. The app now inserts a row into `APP.JOBS` (kind, job_key, args, status QUEUED) and runs `EXECUTE TASK APP.TK_RUN_JOBS`. That task has no schedule, overlap off and a 60 min timeout, and calls `APP.SP_RUN_JOBS`. The worker drains the queue in order and dispatches from a fixed kind whitelist with bound arguments (`ONBOARD_CONTRACT` now, `PROCESS_PERIOD` for Run audit in step 12). It marks each job RUNNING, then DONE with the result or FAILED with the error. A job still RUNNING when a new worker run starts was orphaned by a killed run, so it is marked INTERRUPTED. EXECUTE TASK during an active run does not fail; Snowflake queues one follow-up run, so a request that lands as a run ends is still picked up. The app also re-kicks any job QUEUED for more than 30 s.
- **Progress comes from tables only.** State per file comes from the latest `APP.JOBS` row, and steps come from `CONTRACT.ONBOARD_LOG` (`AUDIT.PERIOD_RUNS` and its log in step 12). `session_state` no longer holds query ids.
- **Never spin forever.** A QUEUED or RUNNING job with no new log row (or no start) for `STALL_MINUTES` = 5 is shown as "interrupted (no progress for 5 min)". That is about 10x the longest step gap observed (29 s). The panel shows a **Read again** button, which queues the same file again. FAILED jobs get the same button.
- **One transaction for the contract.** `SP_ONBOARD_CONTRACT` deletes and re-inserts CLAUSES, CONTRACTORS, RATES, RULE_PARAMS and ROLE_RATE_MAP, merges CONTRACTS, resets status and stale flags, and runs `SP_DERIVE_STANDBY` (standby rates plus RATE_WARNINGS), all between one BEGIN and COMMIT. Before this change, `SP_DERIVE_STANDBY` ran after COMMIT. Any abort rolls back to the previous contract.
- **Polling.** A `st.fragment(run_every=3 s)` re-reads JOBS and the log while a job is active, and does one full-app `st.rerun()` when all jobs finish. An idle app does not poll.
- **Outcome of the step 11 refresh test (2026-10-20).** The 019 call was killed by the reload during step 4, before any write. The 019 rows were intact (child tables hash-equal to `SCRATCH.S11_BEFORE_*`, CONTRACTS fingerprint columns equal, status CONFIRMED), so no restore was needed. The panel spun because the log had a RUNNING row and the timeout was 20 min. After the fix, a worker run cancelled with SYSTEM$CANCEL_QUERY during "Save contract" left every 019 table hash-equal to the snapshot. A worker run completed after the CLI session that queued it had closed, with result "identical", status CONFIRMED kept.
- **Identical re-onboarding changes nothing.** `SP_ONBOARD_CONTRACT` takes a `HASH_AGG` fingerprint before and after its write, inside the same transaction. It covers the CONTRACTS header fields (title, operator, site_id, effective_date) and the child rows of CLAUSES, CONTRACTORS, RATES (excluding the derived `standby_rate_php`), RULE_PARAMS and ROLE_RATE_MAP. Only if a table's fingerprint changed does it reset the contract to `EXTRACTED` and set `is_stale` on its audited periods (D9). The `Save contract` log step and the return value report `changed_tables` and `periods_marked_stale`. Edits via `SP_APPLY_EDIT` still always reset status and mark periods stale, including an edit that is later reverted.
- **App deployment.** `APP.APP_STAGE` (source files) is owned by ACCOUNTADMIN, with READ granted to `MERRY_APP`, because D15 gives `MERRY_APP` no CREATE STAGE. The Streamlit object is owned by `MERRY_APP` (`11_app/01_app.sql`).

**Reason:** Onboarding runs for minutes; a blocking call freezes the app and a refresh would lose its progress. Re-uploading an unchanged contract must not invalidate audits.

## D26 Empty state, extraction summary, chat actions, analyst override (step 12)
**Decision:**
1. **Empty state (built in 12a).** With no CONTRACTS rows, all header dropdowns are disabled and every tab shows "No contracts yet: upload your first contract in the Contracts tab." The Contracts tab keeps its uploader below that line. Period and contractor dropdowns populate only once a contract exists.
2. **Extraction summary and confirmation mode (built in 12b).** After onboarding, an extraction summary (contractors, rates, rule params, warnings) offers "Review and edit" and "Confirm as extracted". If any WARN-level rate warning exists, "Confirm as extracted" requires an acknowledgment checkbox. `CONTRACTS.confirmation_mode` records REVIEWED or AS_EXTRACTED.
3. **Chat actions (built in 12b).** Typed requests that change decisions or payments ("approve the clean invoices", "dispute PumpCo", "hold Liftmax") are handled by the agent proposing the action (`AGENT.PROPOSE_ACTION`, chat mode only; review-mode instructions forbid it) as a confirm card listing exact invoices and SQL-computed amounts. `APP.SP_EXECUTE_ACTION` runs it only on the analyst's click. Nothing that affects payment executes without a click. Buttons on cards remain. The 12b gate includes one full 4-period forced review compared to the answer key, to prove review mode is unchanged after the agent redeploy.
4. **Analyst override (built in 12b).** On any finding, "Override" with a required reason lets the analyst set CONFIRM, CORRECT, DISMISS or WITHHOLD. It is stored as a new DECISIONS version with `decided_by = USER` and the reason. Amounts are still computed by `SP_EVALUATE_DECISION` and the D2 validation rules apply. The letter is redrafted. Overrides show on the card and in the decision history.

**Step 12a implementation notes:**
- **Locked letter lines (D24 follow-up).** `SP_REVISE_LETTER` splits the letter into locked template segments (letterhead, To/Date/Subject, salutation, each finding's `n. PHP x <status>.` prefix and `(Clause …)` suffix, payment, credit-note and release lines, sign-off) and free segments (opening, each reason, an optional closing). AI_COMPLETE returns only the free parts as JSON. Each free part must contain no amount and no clause reference. The letter is rebuilt with locked segments copied unchanged, and the D24 amount-set and clause-subset checks still run.
  - A CORRECT finding's locked status reads `disputed (the charge is partially disputed; the balance is payable)`, so the partial-dispute wording can no longer be lost (the D24 known limit).
  - `SP_DRAFT_RESOLUTIONS` carries a `TEMPLATE_VERSION` in its fingerprint, so a template change redrafts each letter once.
  - An instruction that names a clause or an amount is rejected before any AI call. Clauses and amounts are template lines, so a revision can never add them.
- **Worker-written thread.** `APP.SP_RUN_JOBS` fails a PROCESS_PERIOD job unless the run ends DRAFTED. On success it calls `APP.SP_POST_AUDIT_MESSAGES`, which appends STEPS, BRIEFING (SP_BRIEFING) and the "needs your decision" and "ready to approve" INVOICE_CARDS messages. A failed post is retried with a POST_AUDIT job ("Write briefing" in the app). A re-run appends a note and a new briefing; older cards render as a superseded one-liner, so button keys stay `<action>:<invoice_no>:<version>`. Only the latest LETTER message per invoice shows controls.
- **Re-run of a stale period.** This queues the same job with `force = FALSE`. SP_EXTRACT_PERIOD re-reads only files modified after their PARSED_DOCS row. Measured on W1: 0 of 16 documents extracted, 0 exceptions re-reviewed, 36.6 s.
- **Uploads.** Uploads go to `periods/<cid>/<ps>/invoices|tickets/` and are tracked in `APP.UPLOADS`. `APP.SP_CHECK_UPLOADS` reads each invoice's printed billing period with one concurrent AI_EXTRACT. A MISMATCH shows a warning with Move to week of … / Keep in this period / Remove, and blocks Run audit until resolved.
- **Explain.** Explain uses stored data only: the decision rationale, basis and computation, CLAUSES text, the cited rows from GET_EXCEPTION_CONTEXT, and the winning review attempt's tool steps, now kept in `AUDIT.REVIEW_CALLS.trace`. Decisions recorded before step 12a have no trace and show their cited refs instead.
- **Chat.** Chat calls `MERRY_AGENT` synchronously with a spinner. Each question is sent on its own, scoped to the contract and period, with no earlier turns. With the last 3 question/answer pairs as history, the agent treated an analyst hypothetical ("PumpCo says the hold started Sep 9") as an invoice fact in the next answer: 1 of 1 live gate run and 1 of 3 re-runs; a prompt line telling it to treat such statements as claims did not fix it. Without history: 3 of 3 challenge + pattern runs correct (Sep 9 NORMAL, PCS-INV-1041 and PCS-INV-1042 cited, no proposal written). Vague follow-ups ("check that again") are first rewritten into a standalone question (step 12b, below); the agent still never receives prior answers. Answers keep the agent's result tables as parts. PROPOSE_REVISION results render as a card. Accepting calls `APP.SP_ACCEPT_REVISION`, which re-evaluates the proposal with SP_EVALUATE_DECISION (amounts recomputed, D2), inserts a `decided_by = USER` version and redrafts. Suggested prompts come from the briefing: one "Has <first word of contractor name> done this before?" per pattern and one per verify flag.
- **Contracts tab.** Roles of contractors with no hour/day personnel rate show "not billed by role (per-unit contractor)" in a read-only table under the editable role map.
- **Reset.** `APP.SP_RESET_PERIOD(cid, ps)` (MERRY_APP, not in the UI) deletes the period's CURATED, DOCS, AUDIT and APP rows in one transaction. Stage REMOVE is not allowed in an owner's-rights procedure (090236 "Unsupported statement type 'REMOVE_FILES'"), so it returns the stage paths and `12_review/reset_period.py` removes them.

**Step 12b implementation notes:**
- **USER_OVERRIDE (D26.4).** `AGENT.SP_EVALUATE_DECISION` takes an optional 7th argument, `DECIDED_BY` (default AGENT).
  - Basis `USER_OVERRIDE {reason}` is accepted only with `DECIDED_BY = 'USER'`. The agent tools never pass that argument, so RecordDecision and ProposeRevision calls with it are REJECTED ("reserved for analyst overrides"; tested on both).
  - Amounts are still computed there: CONFIRM → overbilled = flagged, WITHHOLD → withheld = flagged, DISMISS → 0. CORRECT always needs a computed basis (STANDBY_EQUIPMENT / TRAVEL_ALLOWANCE), even for an analyst.
  - `APP.SP_OVERRIDE_DECISION` keeps the current decision's clauses and refs, requires a reason of at least 10 characters, writes a `decided_by = USER` version with `override_reason`, closes pending revisions and redrafts the period's resolutions.
  - Cards show "Overridden by analyst: <reason>" and a Decision history expander.
- **ProposeAction (D26.3).**
  - `AGENT.PROPOSE_ACTION` (tool name ProposeAction, chat mode only) writes one `APP.PENDING_ACTIONS` row. Its items and amounts are copied from the current DRAFT resolutions (APPROVE_PAYMENT / APPROVE_DISPUTE / APPROVE_HOLD, with an optional contractor code or name word and invoice). It returns NO_MATCH when nothing qualifies.
  - `APP.SP_EXECUTE_ACTION` runs only on Confirm. It re-checks each item (same version, still DRAFT) before SP_APPROVE_RESOLUTION; Reject approves nothing.
  - Review-mode instructions say "Never call ProposeRevision or ProposeAction in review mode". The redeployed agent's forced 4-period review: 9/9 against the answer key, ₱287,000 / ₱236,500 / ₱50,500 / ₱13,200 exact, 0 fallbacks, 0 Propose* calls in 9 traced reviews, 164 s.
  - The eval script deletes decisions before re-reviewing, so new versions restart at 1 and resolution fingerprints are unchanged: W1 and W2 stayed approved.
- **Confirmation modes (D26.2).** `CONTRACTS.confirmation_mode`.
  - `CONTRACT.SP_CONFIRM_CONTRACT(cid, user, mode)` records REVIEWED or AS_EXTRACTED. AS_EXTRACTED is refused while WARN rate warnings exist unless sent as AS_EXTRACTED_ACK, which the acknowledgment checkbox enables. The 2-argument version calls it with REVIEWED.
  - The Contracts tab shows an Extraction summary for an EXTRACTED contract, with Review and edit (a view toggle, leading to "Confirm contract (reviewed)") and Confirm as extracted.
  - `12_review/remove_contract.py` (admin only, ACCOUNTADMIN, not in the app) removes a contract that has no period, CURATED or AUDIT data, so it can be onboarded as new. EDIT_LOG is never touched (D20).
- **Logical Move / Remove.** `APP.UPLOADS.excluded` / `target_period`; the app runs no stage commands.
  - `SP_EXTRACT_PERIOD`'s file set = the period's folder minus excluded or moved-away files, plus files whose `target_period` is the period. The parse list, the period's documents and the INVOICE_HEADERS delete all use that set.
  - `SP_POST_AUDIT_MESSAGES` counts documents by `PARSED_DOCS.period_start`. `SP_RESET_PERIOD` returns the period's logical file set, so a file moved to another week survives the reset.
  - Tested with renamed copies of two W3 invoices uploaded into W2: Move put one in W3's set only; Remove excluded the other from both; a non-forced W2 extraction left W2's 5 invoices unchanged and parsed neither.
- **Standalone follow-ups** (`11_app/streamlit/chat.py`, shared by the Review tab, 12c and the gate; deployed with the app by `11_app/01_app.sql`).
  - **Rule wording.** `chat.RULE_LABEL` gives each check a plain noun phrase for the briefing banner ("Repeat pattern: PumpCo: idle equipment billed at the full rate, upheld in 2 service periods"). `chat.RULE_ASK` gives a verb phrase for questions. `07_sp_briefing.sql` still has its own copy of the old labels for the briefing facts.
  - **Suggested prompts** come from `chat.suggestions(brief)`, built from briefing data only. A repeat pattern becomes `chat.pattern_prompt(p)`, "Has <contractor> <RULE_ASK[rule_type]> in earlier periods?", e.g. "Has PumpCo billed idle equipment at the full rate in earlier periods?". Pills are sent verbatim and never rewritten.
  - **Whether to rewrite is decided in code** (`depends_on_context`), not by the model. A typed message is rewritten only if it has a reference word (that, this, it, they, again, same, also, too…) or opens elliptically ("what about…", "and…", "same for…"). Scope phrases such as "this contract", "this period" and "this week" don't count. Every other message is sent unchanged.
  - **Rewrite input** is only the analyst's own last 3 questions. MERRY's answers, or summaries of them, are never passed: in the earlier gate that was how a finding ("daily reports indicate … Sep 10") got into a rewritten question. AI_COMPLETE (`claude-sonnet-4-6`, temperature 0) may use only the analyst's words. Claims stay attributed to their original source. The agent gets only the resulting question, and the tool-trace expander shows it as "Asked as".
  - **Guard: no new facts** (`chat.adds_facts`). If a rewrite has a date (ISO, "Sep 10", "10 Sep") or a peso amount (PHP/₱, or 4+ digit numbers; years and document numbers excluded) that is in neither the typed message nor the analyst's earlier questions, it is not sent. The typed text goes instead with `(This follows my earlier question: "<latest earlier question>")`, as mode FALLBACK. A failed or empty rewrite also falls back the same way.
  - **Switching contractor.** The new message may name a contractor (word or code from `CONTRACT.CONTRACTORS`) that isn't in the earlier questions. The rewrite must then be a neutral check of the same issue type, carrying over no claim, attribution, accusation, date or amount.
    - If the model asks to clarify, returns nothing, adds claim wording the analyst didn't type (`chat.adds_claim`), or adds any date or amount, nothing goes to the agent. The thread shows a NOTE asking which issue to check.
  - **Gate** (`12_review/validate_chat_rewrite.py`, 3 runs). Each run: challenge → "check that again" → the briefing's PumpCo pill → probes for a self-contained message and for "what about Liftmax?". Plus offline guard checks.
    - Offline guard checks: 5/5. An answer-derived date is rejected, the analyst's own date is allowed, an added amount is rejected, ISO dates in the analyst's words are allowed, and an added claim is detected.
    - Challenge: 3/3 correct.
    - "check that again": rewritten 3/3, attributed to PumpCo, no added dates or amounts 3/3, answered correctly 3/3. The fallback never fired.
    - Pattern pill: sent verbatim 3/3, no Sep 9 claim 3/3, no "what do you mean" replies.
      - All 3 runs cited PCS-INV-1041, the earlier-period finding (W1, PU-101 on Sep 3, ₱20,000 overbilled, CORRECT under 4.2).
      - Run 1 also cited the current period's PCS-INV-1042.
    - Self-contained message sent unchanged: 3/3.
    - "what about Liftmax?": 3/3 "Are there any instances where Liftmax billed idle equipment at the full rate?". No claim words and no PumpCo details; the clarify guard didn't fire.
    - Pending revisions written: 0.
  - **Redeployed.** `01_app.sql` was run; `APP.MERRY` VERSION$1 contains `chat.py` and runs on SYSTEM$WAREHOUSE_RUNTIME.
  - Step 12b is closed without the UI click-through, because 12c replaces the card UI.

**Reason:** The analyst must never lose a contractually meaningful word to a tone edit. The thread must exist without an open browser. Nothing that changes money happens without a click, and an analyst can always decide, on the record, with the same arithmetic as the agent.

## D27 Conversational Review tab, one drop zone, date ranges (step 12c)
**Decision:**
1. **Header and tabs.** The header has one Contract dropdown, empty and disabled until a contract exists. There is no service-period or contractor dropdown. The tabs are Review, Dashboard and Documents. Until step 14, Documents shows the former Contracts tab content.
2. **One upload place.** Files are dropped in the chat with `st.chat_input(accept_file="multiple", file_type="pdf")`, which is available in streamlit 1.52.2. There are no separate uploaders for contracts, invoices or tickets.
   - The app PUTs each file to `_inbox/<batch_id>/<file>` and records it in `APP.UPLOADS` with its SHA-256 content hash. `_inbox/` is a non-layout prefix like `_landing/`.
   - The app then queues an INTAKE_FILES job (D25), so a reload doesn't cancel the sorting.
3. **Intake.** `APP.SP_INTAKE_FILES(batch_id)` runs one concurrent AI_EXTRACT per file. It reads the type (CONTRACT / INVOICE / TICKET / UNKNOWN), the contractor name, the document number, the printed billing dates (invoices) and the ticket date or dates.
   - **Contracts** are queued as ONBOARD_CONTRACT jobs on the `_inbox` path itself. There is no copy to `contracts/`, because stage commands are refused in owner's-rights procedures (090236). The source_file of a contract onboarded this way is its `_inbox` path.
   - **Invoices and tickets** go to the one contract whose `CONTRACT.CONTRACTORS` match the document. A contractor matches if its code is the document number's prefix, or its name is similar (≥ 0.6). They are filed in the Monday–Sunday period that contains:
     - the whole printed billing range, for an invoice;
     - the ticket's date or dates, for a ticket.
   - **Filing is logical:** `UPLOADS.contract_id`, `doc_kind`, `target_period` and `intake_status = FILED`. Nothing moves on the stage.
   - **Read the doc kind from APP.UPLOADS.** For a file outside a period folder, `SP_EXTRACT_PERIOD` and `SP_RESET_PERIOD` take the doc kind from `UPLOADS.doc_kind`, and match `target_period` only within the same contract.
   - **MERRY asks when** the contract is ambiguous or none matches, the type is UNKNOWN, the dates are unreadable, or an invoice's billing dates span two periods. Each case gets its own suggestions: pick a contract, file it under a period, leave it out. Nothing is guessed.
   - **Re-uploads match on content (hash), not name.**
     - Same content already filed: MERRY says where it is filed and ignores the new copy (`intake_status = DUPLICATE`).
     - Same name, different content: MERRY asks before replacing. Replacing excludes the old upload and marks its period stale if it was audited.
4. **Auto-confirm.** After an ONBOARD_CONTRACT job from intake, the worker confirms a contract that has no WARN-level rate warnings, with `SP_CONFIRM_CONTRACT(cid, 'MERRY', 'AS_EXTRACTED_AUTO')`; the mode is accepted only with 0 WARNs. MERRY says so, with a "Review its values" suggestion. With any WARN, the contract stays EXTRACTED and MERRY asks: "Review its values" or "Confirm as extracted (I've seen the warnings)".
5. **Date ranges, not weeks.** MERRY describes every batch by the dates it actually covers, and work is still processed per weekly period: checks and gates are unchanged.
   - **One contiguous range, every period with tickets:** MERRY states the range and offers one run ("Run the audit for Aug 31 – Sep 27").
   - **Otherwise:** MERRY states the split. The main part is the contiguous block of periods that have field tickets with the most files (by files alone if no period has tickets). The rest is listed by file when it is 3 files or fewer, else by count. For example: "Most files cover Sep 7 – 13. One invoice, AQH-INV-320, covers Sep 14 – 20." The suggestions are "Audit Sep 7 – 13 only" and "Audit Sep 7 – 20". There is no heuristic that decides a file is stray.
   - **Runs.** A run suggestion queues PROCESS_PERIOD for each affected period with one shared `run_id`. When the run's last job ends, the worker posts one range briefing (`APP.SP_RANGE_BRIEFING`) under a divider labelled with the range.
6. **One thread per contract** (`thread_id = contract_id`, and `_INTAKE` before any contract exists). Messages about periods carry `payload.periods`, so `SP_RESET_PERIOD` deletes only that period's messages. The existing per-period rows were moved into the contract thread in created_at order. Before that, `APP.CHAT_MESSAGES` was copied to `SCRATCH.CHAT_MESSAGES_BAK_12C`.
7. **Suggestions replace buttons.** Every MERRY message ends with "What I can do next". `chat.next_steps(state)` builds the suggestions in code from table state only (RESOLUTIONS, DECISIONS, UPLOADS, JOBS, PENDING_ACTIONS, PENDING_REVISIONS), never with the LLM. They show under the latest MERRY message only.
   - A money suggestion's amounts come from `V_RESOLUTIONS` columns. Clicking it is the confirmation; before running, the app re-checks that the invoice is still at the same version and still a DRAFT.
   - "Change a decision…" is always offered when the range has findings.
8. **Findings and letters in messages.** The briefing renders its headline and findings table fresh from the tables on each rerun (D12). Letters appear inline as an editable message, with "Make it firmer" and approve suggestions; only the latest LETTER message for an invoice is editable. Explain is served from stored data.
9. **Typed chat keeps the 12a/12b behaviour:** agent in chat mode, scoped to the contract and the range's periods; follow-up rewrite; PROPOSE_ACTION and PROPOSE_REVISION results shown as money suggestions; overrides via "Change a decision…" (`SP_OVERRIDE_DECISION`). The old card buttons are removed, and every existing procedure is kept.
10. **One source for rule wording.** `APP.RULE_LABELS(rule_type, label, ask_phrase)` is read by `SP_BRIEFING` and by `chat.py`, which has no copy of its own. The briefing, the pattern line and the suggestions therefore word every rule the same way.
11. **Run suggestions cover every period that needs an audit (step 12c.4), not only the latest drop.** `chat.run_suggestions(filed, audited, stale, retry)` is built from table state.
    - **Which periods:** every period with FILED, non-excluded `APP.UPLOADS` rows for the contract, from any batch, minus periods with a current audit (PERIOD_RUNS DRAFTED or REVIEWED, not stale), plus stale periods. FAILED and interrupted periods count as needing an audit again.
    - **Suggestions, in order:**
      - "Retry <dates>": exactly the periods the latest range briefing reports as not audited.
      - One "Run the audit for <range>" per contiguous block. A block made only of stale periods is labelled "Re-run".
      - "Audit <range> only (the dates with field tickets)", only when a block contains periods with no field tickets.
      - "Run the audit for every period that needs it (…)", only when there is more than one block.
    - **No duplicates:** suggestions are deduplicated by period set, so no two have the same effect. At most 4. The separate stale "Re-run" suggestion is merged into this list.
    - **Unchanged:** the batch description text (`batch_summary`) still describes the drop.
    - **This state** (MSA-014 W1–W4 filed across three batches, none audited) gives exactly one suggestion: "Run the audit for Aug 31 – Sep 27".
    - **Gate:** `12_review/validate_run_suggestions.py`, 10/10.
12. **Range runs are staged across periods (step 12c.4).**
    - **Worker:** when the worker picks up a PROCESS_PERIOD job that has a `run_id`, it claims every QUEUED job of that run and makes one call to `AUDIT.SP_PROCESS_RANGE(cid, periods, force, run_id, 6)`.
    - **Stages:**
      1. **Extract:** `SP_EXTRACT_PERIOD` for all periods at once, as async nested CALLs. A spike confirmed that an owner's-rights procedure can do this: 3×15 s waits finished in 22 s. Child transactions stay isolated; one child's ROLLBACK left the other three committed.
      2. **Checks:** one period at a time, in date order, because DUPLICATE looks at earlier invoices. A period runs checks only if every earlier period of the range extracted.
      3. **Review:** all remaining periods in one `AGENT.SP_REVIEW_RANGE` call, under **one shared limit of 6 concurrent agent calls**. First attempts, hedges and retries all take a slot, and the hedge and timeout clocks start when the first call gets its slot. The claim, hedge, retry, timeout and first-write-wins rules are those of D22. `SP_REVIEW_PERIOD` is unchanged.
      4. **Draft:** one period at a time, in date order.
      5. **Briefing:** one range briefing.
    - **PERIOD_RUNS** is kept per period, as `SP_PROCESS_PERIOD` keeps it. `SP_PROCESS_PERIOD` is unchanged and still used by jobs that have no run_id.
    - **Partial failure:**
      - A period that fails at any stage stops there.
      - An extraction failure also stops every later period of the range at CHECKS, with the reason "not audited: the duplicate check needs the earlier period starting …".
      - Earlier and unaffected periods go on through draft and the briefing.
      - Each job ends DONE or FAILED with its own period's result.
      - `SP_RANGE_BRIEFING` returns `failed` (the stage and reason for each period) and `retry_periods`. The briefing opens with "Not audited: <dates>. <dates> were audited in full", lists each reason, and the suggestions offer "Retry <dates>" for exactly those periods.
    - **Timing:** each job's result carries `range.stage_seconds` and `range.concurrency`: calls, the most in flight at once, hedges, retries, HTTP errors, 429s, slot waits and fallbacks. The briefing shows the measured stage times next to the expected time.
    - **Expected time** (`chat.run_estimate`, from cold per-period step times in the step 10 gate and the 12 dry run: extract 28–34 s, checks 18 s, review 51 s, draft 15 s): 45 s extract + 18 s per period for checks + 70 s review (52 s for one period) + 15 s per period for draft + 5 s per period for the briefing.
      - 4 weeks: **267 s (≈4.5 min)**, against about 460 s for the previous one-period-at-a-time run.
      - 1 week: 135 s.
    - **First click (2026-10-26, 3:27 PM): failed at EXTRACT for Aug 31 – Sep 6, after 80 s.**
      - **Error:** `SP_EXTRACT_PERIOD`, line 290: `a.cancel()` on a losing hedged AI_EXTRACT attempt raised 000605 "Identified SQL statement is not currently executing".
      - **Cause:** the attempt finished between `is_done()` and `cancel()`. With four periods extracting at once, calls ran slower, more of them were hedged (HEDGE_S = 12 s), and the race became likely.
      - **Partial-failure handling worked:**
        - W2–W4 extracted (14 invoice headers kept) and were stopped at CHECKS with the dependency reason;
        - one range briefing was posted, with `retry_periods` = all 4;
        - nothing was written for W1, because the failure was in the read phase, before its write transaction.
      - **Fix:** `cancel_quiet(a)` in `05_extract/02_sp_extract.sql`. Cancelling a losing attempt is best-effort, and an attempt that has already finished is ignored. Redeployed. No other procedure cancels queries.
      - **Next run:** a non-forced re-run re-reads only W1's files; W2–W4 parse rows are reused.
    - **Measured** (4 weeks, from "Retry Aug 31 – Sep 27", 2026-10-26; `12_review/validate_range_run.sql`):
      - **Time:** **266.1 s** in the range procedure against 267 s expected; 275 s from the click to the last job.
        - Extract: 36.4 s for all four weeks at once; only W1's files were re-read.
        - Checks: 75.2 s.
        - Review: 93.7 s.
        - Draft: 58.4 s.
      - **Agent calls:** 9 (one per exception), at most 6 in flight, against the limit of 6.
        - **0 hedges, 0 retries, 0 HTTP errors, 0 throttled (429), 0 fallbacks.**
        - 3 calls waited more than 1 s for a slot; the longest wait was 42.7 s. That wait is why review took 93.7 s against 70 s expected.
      - **All 4 periods DRAFTED**, with one range briefing.
      - **Amounts match the D26 forced-review figures exactly:**
        - ₱287,000 upheld = ₱236,500 disputed + ₱50,500 held;
        - ₱13,200 cleared (TLW-INV-5513, SITE_HOURS, DISMISS);
        - 9 decisions, all by AGENT;
        - PCS-INV-1042 CORRECT ₱40,000 under clauses 3.3 and 4.2.
      - **Payment:** 19 invoices, ₱3,170,200 billed, ₱2,883,200 recommended to pay before VAT. All 19 resolutions are DRAFT.

13. **MERRY's voice in chat (step 12c polish).** Applies to chat messages only: the briefing, approvals, run progress and the one-by-one view. Contractor-facing letters and their PDFs keep their locked D24 wording; `SP_DRAFT_RESOLUTIONS` is unchanged.
    - **Opening sentence:** what was checked, what to pay, what to push back on: "I checked 19 invoices for Aug 31 – Sep 27. I'd pay ₱2,883,200 before VAT and push back on ₱287,000 across 8 invoices."
    - **One line per contractor**, contractor first, largest amount first, in everyday words, e.g. "PumpCo charged full price for PU-101 that sat idle during the heavy rain and typhoon on Sep 3, 10 and 11. Fair price is half the rate, so they overcharged ₱60,000."
      - Each line ends with its invoice numbers and plain status words.
      - The main text has no clause numbers, rule codes or decision codes; those are in Details and Explain.
    - **Shared wording:** `APP.STATUS_WORDS` (CORRECT → "overcharged in part", CONFIRM → "overcharged", WITHHOLD → "on hold until signed", DISMISS → "checked, it's fine") and `APP.PLAIN_PHRASES` (one sentence template per check and decision, plus VERIFY notes keyed by `EXTRACT_ISSUES.issue_type`). `chat.load_labels` reads both; there is no copy in code.
    - **Slots** are filled only from `APP.V_ISSUE_FACTS` and SQL amounts:
      - `V_ISSUE_FACTS` is a view over the stored exception evidence, `RAW.DAILY_REPORTS` (the weather on non-NORMAL days) and `CONTRACT.RULE_PARAMS` (`STANDBY_EQUIPMENT_PCT` → "half the rate").
      - A `[ … ]` part whose slot has no fact is dropped, never guessed.
      - Nothing is keyed by invoice number or error ID.
    - **Details** (collapsed) holds the stage timings, agent-call counts, the findings table with rule labels, decision codes and clauses, billed and flagged totals, raw failure reasons and PDF paths.
    - **Length:** about 6 lines before Details (7 for the 4-week range).
    - **Files:** `12_review/14_tables_voice.sql`, `chat.py` (voice section), `review.py`.
14. **Fewer clicks (step 12c polish).**
    - **Auto-start in the worker.** When an INTAKE_FILES job finishes (or an ONBOARD_CONTRACT job from intake), `SP_RUN_JOBS.auto_run(batch)` queues the range run itself, so it works with the browser closed. It starts only when all of these hold:
      - no ASK_* or NEW upload in the batch, and no open question in the thread or contract;
      - no intake or onboarding job still open for the batch;
      - exactly one contract, CONFIRMED;
      - no audit already running;
      - the periods that need an audit (D27.11) form one contiguous block, and every period in it has field tickets.
    - **Otherwise** MERRY asks, exactly as before.
    - **Once per batch:** a RUN message carrying `payload.batch_id` blocks a second start. The run posts "Nothing needed your input, so I started right away. Checking Aug 31 – Sep 27 now, about 4 minutes." with a **Stop** suggestion.
    - **Stop:**
      - QUEUED jobs of the run become CANCELLED, and RUNNING ones get `args.cancel_requested`.
      - `SP_PROCESS_RANGE` checks the flag between stages and between periods of CHECKS and DRAFT, and ends the rest with step STOPPED (job CANCELLED). A stage already in progress, including an agent review in flight, finishes first.
      - The range briefing says which dates were stopped and offers Retry.
    - **Bulk approve** is the first suggestion after a briefing: "Approve all as recommended: pay ₱X, dispute ₱Y (N letters), hold ₱Z (M notices)". Its amounts are sums of `V_RESOLUTIONS` columns.
      - The click re-checks every invoice's version and DRAFT status first, and approves nothing if any changed.
      - Otherwise it calls `SP_APPROVE_RESOLUTION` for each, saving every PDF, then reports the count, pay with and without VAT, disputed and held amounts, and the PDFs (in Details).
    - **One by one** is the second suggestion: "Go through the N invoices with issues one by one". MERRY posts one short message per DRAFT invoice with issues, giving the plain reason with the letter collapsed. Suggestions: "Approve this one" (the next invoice follows), "Change it…" (the change form scoped to that invoice), "Explain", and "Approve all the rest as recommended".
    - **Gate:** `12_review/validate_12c_polish.py`, 16/16 (2026-10-26; output in `validate_12c_polish_output.json`).
      - **Clicks: 2, from drop to everything approved** (the drop, then "Approve all as recommended").
      - Reset W1–W4, then one drop of 60 PDFs. The worker sorted all 60 and started the 4-week run by itself 29 s after the drop. The briefing came 340 s after the drop.
      - The bulk suggestion read "pay ₱2,883,200, dispute ₱236,500 (7 letters), hold ₱50,500 (1 notice)". It approved 19/19 and saved all 8 letter PDFs; payment approvals have no PDF.
      - The briefing before and after the change is in `12_review/briefing_before.md` and `12_review/briefing_after.md`.
      - The drop and the click were driven headless through the same steps as `drop_files` / `approve_all`, not through the browser.
    - **Fix after the first live test (2026-10-26).**
      - **Crash:** the app crashed rendering the briefing with AttributeError "Row object has no attribute get" (`chat.plain_sentence`). The gate had passed because its harness handed `chat.py` plain dicts, while the app hands it Snowpark Rows.
      - **Boundary:** `review.qd()` converts every Row passed to `chat.py` into plain dicts, recursively (`range_data`, `load_labels`, `standalone`). `chat.plain()` asserts that `chat.py` only receives dicts, and names the type if not.
      - **Gates use the app's own code.** `12_review/app_harness.py` loads the real `review.py`, `chat.py` and `common.py` on a Snowpark session as MERRY_APP, with `12_review/st_stub.py` standing in for Streamlit.
        - `validate_12c_polish.py` drops through `review.drop_files`, reads suggestions from `review.next_state` + `chat.next_steps`, and clicks through `review.do_act`.
        - `validate_render.py` renders every message in the live MSA-014 thread through `review.render_message`, plus the one-by-one, approval, letter, explain and change-form kinds built from live data, then the whole `review_tab`. It fails on any exception.
      - **Suggestion order:** the rebuilt gate also found that "Review <other contract>'s values" and "Open <other contract>" led the list after a briefing. They now come after the bulk action once the range has invoices, and before any briefing they still lead. Unit: `bulk_first_over_contract_offers`.
      - **Result:** `validate_render.py` 4/4, `validate_12c_polish.py` 16/16 (2 clicks), `validate_12c.py` 21/21, `validate_run_suggestions.py` 10/10. App redeployed, and W1–W4 reset for the browser test (drop all 60, close the tab, reopen, approve all).

15. **A drop survives a closed tab (step 12c polish).**
    - **Problem (live test, 2026-10-26):** the app saved files one at a time and recorded the drop only after the last one. Closing the tab partway left 27 of 60 files with no message and no sorting job, so the app looked as if nothing had happened.
    - **Record first:** `review.drop_files` posts the user message with the full file list (`payload.expected`) and writes every `APP.UPLOADS` row as UPLOADING in one statement, *before* saving anything.
    - **Save in parallel:** files are saved 8 at a time on the one Snowpark Session, with a progress line ("Saving 27 of 60 files… keep this tab open until I say I've got them"), then one sequential retry for any failure.
      - Session thread safety was checked first: the Snowpark docs say Session has been thread-safe since 1.24, including concurrent file uploads, and it is now on by default. This session runs 1.53 with thread safety on, and the runtime package list goes up to 1.55.
      - Measured: 60 files saved one at a time on one session took 6.7 s, and 8 at a time took 1.6 s, with every name and size checked on the stage. Through `drop_files` itself, saving 60 files took 1.8 s.
      - Only then does it say "Got all 60 files. You can close this tab now." and queue the sorting job.
    - **A cut-off drop is noticed when the thread is opened, with no scheduled sweeper.** `review.stopped_drops` finds a drop that still has UPLOADING rows more than 2 minutes old and no sorting job.
      - What arrived is read from the stage (`LIST _inbox/<batch>/`) and marked STOPPED.
      - MERRY posts once: "I got N of M files from your last drop; the upload stopped before it finished. Missing: … Drop them again and I'll add them."
      - It suggests "Audit what arrived (N files)" and starts nothing on its own.
    - **Re-dropping completes it:** `merge_stopped` moves files that arrived earlier into the new drop, unless the same content was dropped again (then the old copy is REPLACED). Files that never arrived become NOT_RECEIVED. The merged drop is sorted and auto-starts as normal (D27.14).
    - **"Audit what arrived"** sorts the STOPPED files as their own drop. The click is the consent, so the normal auto-start applies.
    - **Bug found in the first browser test (2026-10-26): "Approve all" kept refusing the 8 dispute and hold letters** with "The edited letter changes the amounts", although no letter had been edited.
      - **Cause:** in the warehouse runtime, Snowpark binds a Python `None` parameter as the string 'None', not NULL. A Python procedure's `SELECT ? IS NULL` with None returned FALSE and the text 'None'. The app passes `None` as the letter text when nothing was edited. `SP_APPROVE_RESOLUTION` therefore compared the 4-letter text "None" with the stored letter, found no amounts in it, and refused. Payment-only approvals skip the letter check, so the 11 clean invoices went through.
      - **Why the gates missed it:** the gates run the local Snowpark client, which binds None as NULL.
      - **Fix:** `common.q()` writes NULL into the SQL for every None parameter (`_null_binds`); placeholders inside quoted literals are left alone. This covers every app query. Unit: `none_param_becomes_sql_null`.
    - **12c close-out: None → NULL on every path, with one shared helper.**
      - **Shared helper:** `12_review/lib/merry_sql.py`, deployed by `12_review/15_deploy_lib.sql` to `@MERRY.APP.APP_STAGE/lib/`.
        - `null_binds(sql, params)` writes NULL for every None parameter. Placeholders inside '…', "…" and $$…$$ are left alone.
        - `guard(session)` is a Session whose `.sql()` applies it.
      - **The app:** `common.q` imports `lib.merry_sql.null_binds`; the app root is APP_STAGE, so `lib/` ships with it.
      - **Every MERRY Python procedure (25; SCRATCH spikes excluded)** has `IMPORTS = ('@MERRY.APP.APP_STAGE/lib/merry_sql.py')` and starts its handler with `session = merry_sql.guard(session)`. `12_review/apply_null_guard.py` adds both idempotently across the step files.
      - **Measured in the server-side runtime** (`APP.SP_NULL_BIND_CHECK`):
        - raw `session.sql` turns None into the text 'None';
        - `guard().sql` delivers NULL and keeps the other parameters;
        - `session.call` already delivers NULL.
      - **Gate:** `12_review/validate_null_binds.py`, 6/6: the hazard, guard.sql, session.call, 25/25 procedures guarded in their deployed DDL, guarded procedures run with NULL arguments, and the same helper bytes in the workspace, on the stage and in the deployed app. `validate_12c.py` 22/22, `validate_render.py` 4/4.
    - **The 27 "missing" upload rows were the gate's own, not the analyst's.**
      - Batch `310c4961aeeb4743` was `validate_drop_stop.py`'s simulated cut-off drop (17:02). The analyst's real cut-off drop, `2382d92433f24eb4`, was removed deliberately at 17:04 (`DELETE … WHERE batch_id = '2382d92433f24eb4'`).
      - The gate's re-drop moved the 27 arrived rows into its second batch at 17:05:51 (`review.merge_stopped`).
      - The gate's final reset then deleted them with the rest of W1–W4 at 17:07:53–17:08:37: `DELETE FROM MERRY.APP.UPLOADS WHERE contract_id = ? AND ((period_start = ?::DATE …` from `APP.SP_RESET_PERIOD` via `reset_period.py`; 94, 75, 63 and 50 rows.
      - **Risk fixed:** the gates reset live periods unconditionally. `12_review/gate_guard.py`:
        - `safe_reset` refuses without `MERRY_GATE_RESET=1`, and refuses again if any resolution in those periods is APPROVED unless `MERRY_GATE_RESET_APPROVED=1` is set;
        - `cleanup_own` deletes only the batches the gate itself created, by batch id and `_inbox/<batch>/` path.
      - `validate_drop_stop.py` and `validate_12c_polish.py` use `safe_reset`. Both refusals were checked against the live state (19 approved).
    - **12c closed (2026-10-27).** Stop and "Audit what arrived" are **tested by gate only**, not in the real app.
      - Stop: `validate_drop_stop.py` ended a range run, 4/4 jobs CANCELLED.
      - "Audit what arrived": offered as the first suggestion after a cut-off drop. The gate checks the offer only; the click itself was never run, in the app or the gate.
      - The real-app click test (`12_review/prepare_stop_test.py`) was not run, by decision, to leave the data untouched: W1–W4 of PUP-MSA-2026-014 stay audited and approved, 19/19.
    - **Gate:** `12_review/validate_drop_stop.py`, 11/11, on the app's own functions with real Rows.
      - The drop is cut off after 27 of 60 files, and is recorded before any file is saved.
      - On reopen: one message, "27 of 60", 33 missing; "Audit what arrived (27 files)" offered first; 0 jobs started.
      - Re-dropping the 33 missing files gives "Got all 60 files (27 from your earlier drop). You can close this tab now.", 60 FILED, and the audit auto-starts for all 4 weeks.
      - Stop then ended the run (4/4 CANCELLED; the first time Stop has run).
      - `validate_render.py` 3/3. The briefing check skips when no week has been audited. `validate_12c.py` 21/21 and `validate_run_suggestions.py` 10/10.

**Reason:** The analyst's real input is "a pile of PDFs". Sorting them, stating what they cover and asking only when unsure removes three uploaders, a week picker and every way to file a document in the wrong week. Suggestions computed from state keep the "nothing changes money without a click" rule, and the click itself carries the exact SQL amounts.

## D28 Chat threads, welcome back and history questions (step 12d)

1. **Activity feed.** `APP.V_ACTIVITY(contract_id, at, who, kind, subject, summary, amount_effect)` is read-only, built in `12_review/17_v_activity.sql`. One row per event:
   - UPLOAD (one row per drop), CONTRACT_CONFIRMED, AGENT_DECISION (who = MERRY), OVERRIDE (decided_by USER; who is parsed from the stored rationale, and summary carries the reason), APPROVAL (every approved resolution version), CONTRACT_EDIT (EDIT_LOG), CHAT_ACTION (PENDING_ACTIONS DONE).
   - `amount_effect`: decisions = overbilled + withheld; approvals = pay before VAT; otherwise NULL. Summaries are SQL text over the stored amounts. Step 13's dashboard reuses the view.
2. **Threads.** `APP.THREADS(thread_id, contract_id, title, created_by, created_at, last_message_at)` and `APP.USER_VISITS(user_name, contract_id, thread_id, last_seen_at)` live in `12_review/18_tables_12d.sql`.
   - The existing conversation keeps `thread_id = contract_id` and is the contract's first thread, so no messages were moved. New threads are `<contract_id>#<8 hex>`.
   - Every post (app `review.post`, worker `SP_RUN_JOBS.post`) runs the shared `merry_sql.TOUCH_THREAD` MERGE.
   - The sidebar lists the contract's threads newest first, labelled with who started each, under a **New chat** button. Threads are shared.
   - Titles come from the first meaningful message (`chat.thread_title`): "Audit Aug 31 – Sep 27", "Uploaded 60 files" or the first question. A run's dates take priority: an upload or question title is replaced once by the thread's first run (12d follow-up).
3. **Runs report to their thread.** Range jobs carry `args.thread_id`:
   - app `start_run`: the current thread;
   - worker `auto_run`: the thread the files were dropped in (`UPLOADS.origin_thread`);
   - RUN_BRIEFING: the current thread.
   `finish_run` posts the briefing to `args.thread_id`, else the thread holding the run's RUN message, else the contract's first thread (`merry_sql.run_thread`).
4. **Suggestions are contract-wide.** `next_state` takes the latest RANGE briefing and RUN of the contract from any thread (`review.contract_latest`). Invoice state comes from tables, so an approval in one thread shows in every other thread. Typed-chat proposals (ANSWER action and revision ids) stay with their thread.
5. **Welcome back.** On opening a thread (once per browser session per thread), if V_ACTIVITY has rows since this user's `USER_VISITS.last_seen_at`, one WELCOME_BACK message is posted and the visit recorded.
   - The text is rebuilt on every render (`review.welcome_markdown` → `chat.welcome_text`) from V_ACTIVITY in the stored window and from V_RESOLUTIONS DRAFT totals now. Amounts are SQL sums only.
   - The reader's own actions are included, written as "you"; MERRY says "I". With no earlier visit it starts "So far, …".
6. **History questions.** AUDIT_SV has an `activity` table over V_ACTIVITY, with synonyms (history, who changed, did anyone override, what did we decide) and one VQR (`invoice_decision_history`, written with an `<invoice number>` placeholder: no invoice numbers or error IDs, D4). MERRY_AGENT's chat instructions route history questions to AuditData. The agent still receives no earlier chat (D26).
7. **Gate:** `12_review/validate_12d.py` (app code on real Rows via `app_harness.py`; the answer key is read only by `validate_12d_key.sql`, as ACCOUNTADMIN).
   - Cycle 1 (drop in a new thread) and cycle 2 (drop in the first thread): 9/9 against the key, overbilled ₱236,500, withheld ₱50,500, 0 fallbacks. RUN and BRIEFING were posted to the thread of the drop, and 4/4 jobs carried thread_id.
   - Welcome back in a new thread: "Since your last visit on Oct 26, you uploaded files; I reviewed 9 flags on 9 invoices. Waiting for you: 19 invoices to approve, ₱2,883,200 to pay before VAT, ₱236,500 to push back on and ₱50,500 on hold." It was posted once.
   - Approving PCS-INV-1042 in T1 changed T2's bulk suggestion to "Approve the remaining 18 …: pay ₱2,423,200, dispute ₱196,500 (6 letters), hold ₱50,500 (1 notice)".
   - The history question failed in the full run because the harness had no `_snowflake` module. That gap is fixed with a REST stand-in in `app_harness.py`. `validate_12d.py --history-only` then passed on the final data (not yet approved, no override): corrected, ₱40,000 not payable, "no analyst has changed" it. The approved variant ("approved by …") has not passed a gate yet.
   - Also: `validate_render.py` 4/4, `validate_null_binds.py` 6/6, `validate_12c.py` all units true, `validate_run_suggestions.py` 10/10.
   - Left for the browser check: W1–W4 audited and unapproved (19 DRAFT), one thread.

**Reason:** Several people work one contract over weeks. Shared threads keep separate questions apart without splitting the contract's state. A welcome back built from records, never from chat memory, tells each person what changed and what is waiting. History answers come from the same records, so they cannot drift from what was actually decided.

## D29 Dashboard tab, plain status words in chat (step 13)

1. **Status words in chat answers.** MERRY_AGENT's chat-mode instructions (`08_agent/merry_agent_spec.yaml`, redeployed with `deploy_agent.py`) now forbid the decision codes CONFIRM / CORRECT / WITHHOLD / DISMISS in the main text and give the APP.STATUS_WORDS wording instead: overcharged / overcharged in part / on hold until signed / checked, it's fine. Tool arguments keep the codes. The words are copied into the instructions, so a change to STATUS_WORDS also needs a spec redeploy. The previous agent description is saved in `13_dashboard/agent_backup_before_d29.json`.
2. **"What I can do next".** `review.render_suggestions` drops suggestions with no label and draws the header only when at least one suggestion is left. The earlier code already skipped an empty list; the gate now covers both cases.
3. **Views** (`13_dashboard/01_views.sql`, APP only, read-only, current versions only, so a change made in any chat shows on the next render):
   - `V_DASH_TOTALS`: one row per contract, including contracts with no invoices. Billed, pay, disputed and held are SUMs over V_RESOLUTIONS. `waiting` counts invoices not yet approved, including invoices with no resolution yet.
   - `V_DASH_REPEATS`: the same contractor and check upheld (current decision) in 2 or more service periods. Carries the dates (V_ISSUE_FACTS), invoices and the not-payable total.
   - `V_DASH_GRID`: one row per invoice, with the STATUS_WORDS of its current findings and its resolution status.
   - `V_DASH_ACTIVITY`: V_ACTIVITY in plain voice. Decision rows are reworded through STATUS_WORDS and carry the reason in `why` (rule label for MERRY, override reason for analysts). Approval rows drop the version number.
4. **Tab** (`11_app/streamlit/dashboard.py`). It reads only the V_DASH_* views. Formatting uses `chat.php`; dates use a compact span ("Sep 10–11").
   - Top line: "₱3,170,200 billed, ₱2,883,200 approved to pay, ₱236,500 disputed, ₱50,500 on hold", followed by how many invoices are waiting. "approved to pay" is shown only when nothing is waiting; otherwise it reads "to pay".
   - One callout per repeat pattern.
   - Grid: contractors × weeks, columns labelled by date range, cells such as "overcharged in part · approved", "fine · waiting" or "not reviewed".
   - Recent activity, latest first, with a filter by kind (plain labels).
   - Empty states: no contract (D26.1 text), and a contract with no invoices ("No invoices on PUP-MSA-2026-019 yet…", activity still shown).
5. **Gate:** `13_dashboard/validate_dashboard.py`, run through app code on real Rows via `12_review/app_harness.py`, passed 25/25.
   - Totals equal a direct SUM over AUDIT.RESOLUTIONS latest versions: ₱3,170,200 / ₱2,883,200 / ₱236,500 / ₱50,500, 19 approved, 0 waiting. The rendered top line matches.
   - Callout: "PumpCo billed idle equipment at the full rate again, in 2 periods: Sep 3 and Sep 10–11 (PCS-INV-1041, PCS-INV-1042). ₱60,000 not payable so far."
   - The grid is 5 × 4, labelled by date range, with no decision codes.
   - Live change: `review.render_change_form` with Save pressed changed LCR-INV-2208 line 3 to "checked, it's fine", and `review.approve_one` approved it. The totals moved by ₱10,500 (pay ₱2,893,700, disputed ₱226,000), and activity showed the change with its reason and the approval on top. The same two paths put it back and approved it again (LCR-INV-2208 v3), and the totals returned to the starting figures exactly. The finding is now recorded as an analyst decision (CONFIRM, same amount), not MERRY's.
   - MSA-019 shows its empty state, and the no-contract case shows the D26.1 text.
   - D29.2: no header for an empty list or for an unlabelled suggestion.
   - D29.1: the PCS-INV-1042 history answer uses "overcharged in part", with no codes in the main text.

**Reason:** The dashboard is the contract's state at a glance, for whoever opens it, whichever chat the work happened in. Reading current versions through views keeps every figure tied to the same records the Review tab approves from, so the two can't disagree. Plain status words keep the chat and the dashboard in one voice.

### D29.5 Conversation focus for follow-ups (step 13)

Chat memory was intact: every message is stored per thread, and the gaps in the first thread are the 12d gate's period resets (`SP_RESET_PERIOD`). The fault was reference resolution. "Why was it changed?" was rewritten from the analyst's last 3 messages with no preference for the newest, so it resolved to PCS-INV-1042 (asked at 19:40) instead of PCS-INV-1041 (changed at 21:38).

1. **Focus table.** `APP.THREAD_FOCUS(thread_id, invoice_no, contractor, last_action, at)` is created in `13_dashboard/02_thread_focus.sql`, with one row per thread. `review.set_focus` writes it:
   - after an override is saved in the change form (OVERRIDE);
   - on invoice suggestions: explain, letter, firmer, change it, approve (CLICK / EXPLAIN / LETTER / CHANGE_FORM, then APPROVE once the approval succeeds);
   - on a clicked or typed question that names an invoice (the last one named) or a different contractor (ASK; contractor only, invoice cleared). Bulk approvals don't set a focus.
2. **Resolution.** `chat.standalone(..., focus)` tells the rewrite to resolve "it / that / this decision" to the focus first, then to the newest earlier question that names something. `chat.with_focus` then checks the result in code:
   - an invoice the rewrite names that is neither the focus nor typed by the analyst is replaced by the focus invoice;
   - if no invoice is named, "(about <focus>)" is added;
   - the FALLBACK path does the same.
3. **Fact from the records.** For OVERRIDE and APPROVE, `review.thread_focus` builds one sentence from the latest matching APP.V_DASH_ACTIVITY row, appended as "Context from the records: PCS-INV-1041 is the invoice the analyst just changed: Changed "overcharged in part" to "on hold until signed" on PCS-INV-1041 line 1 (₱40,000 not payable). Reason given: Approved by email." D26 stands: the agent never receives MERRY's earlier answers.
4. **Codes out of the history source.** The D29.1 check failed in 2 of 3 runs on "(WITHHOLD)". Its source was APP.V_ACTIVITY, whose override summaries read "Changed MERRY's correct to withhold". `12_review/17_v_activity.sql` now words MERRY decisions as `MERRY found "<word>" on the …` and overrides as `Changed "<word>" to "<word>" on the …`, both from STATUS_WORDS, so the agent's history tool no longer returns codes.
5. **Gate:** `13_dashboard/validate_focus.py`, 34/34 passed.
   - 2 unit checks of `with_focus`: a wrong invoice is replaced, and an invoice the analyst typed is kept.
   - 3 runs, each in a new thread, through `review.ask`, `review.do_act` and `review.render_change_form`: the history question on PCS-INV-1042, then "Change a decision…" setting PCS-INV-1041 line 1 to on hold ("Approved by email."), then "What was the decision made and why was it changed?". Each time the focus moved 1042 → 1041 / OVERRIDE, the question sent named PCS-INV-1041 with the change and the reason, and the answer was about PCS-INV-1041, gave "on hold" and the reason, did not deny the change, and had no decision codes.
   - Each run started from and was restored to MERRY's finding through the same form ("Correct to the contract amount", standby rate, 2026-09-03), then `review.approve_one`. End state: PCS-INV-1041 corrected, ₱20,000 not payable, approved (v12). Totals are back to ₱3,170,200 / ₱2,883,200 / ₱236,500 / ₱50,500, 0 waiting.
   - Also `12_review/validate_render.py` 4/4.
   - History left behind: PCS-INV-1041 now carries the browser test, three gate change-and-restore cycles and the repeat saves, all with reasons. Its current decision is recorded as an analyst decision with MERRY's amount.

**Reason:** A follow-up is about what the analyst just did, and that is a fact in the records, not something to infer from older questions. Tracking the subject per thread and checking the rewrite in code keeps "it" pointed at the right invoice, while the agent still sees records only.

## D32 One knowledge base: the root app's tabs read MERRY
**Decision:** MERRY is the single engine and source of truth for the whole root app (team-approved). Every upload goes
to MERRY: PDFs to intake, site-record CSVs (timesheets, gate log, daily reports, equipment log, recognised by header) to
`APP.SP_LOAD_SITE_CSV`, which inserts only rows whose natural key is new (identical rows ignored, differing rows
reported, never changed), records each new row's source in `RAW.SITE_RECORD_SOURCES`, and marks audited periods it
touches stale so the chat offers Re-run (no automatic re-run). Rate files are refused ("Rates come from the contract, so
I didn't load this file"). The RAW_DB pipeline no longer runs; RAW_DB is kept as an archive.
The Dashboard, Issues and Data tabs read `APP.V_UI_KPIS`, `APP.V_UI_KPIS_BY_CONTRACT`, `APP.V_UI_FLAGS` and
`APP.V_UI_DATA_*`. Finding status: OPEN = drafted, not settled; CONFIRMED = upheld and settled or approved; DISMISSED =
cleared and settled or approved; RECOVERED = a credit note is recorded (`AUDIT.CREDIT_NOTES`, via
`AUDIT.SP_RECORD_CREDIT_NOTE`, at most the upheld amount). Saving a review in Issues calls `APP.SP_REVIEW_FINDING`:
CONFIRMED/DISMISSED settle one finding (`AUDIT.FINDING_REVIEWS`), overriding MERRY's decision through
`SP_OVERRIDE_DECISION` when the outcome differs (reason required); when every finding on the invoice is settled, the
letter is approved (`SP_APPROVE_RESOLUTION`). OPEN needs a reason and reopens an approved letter as a new draft
version. Reviews, credit notes and site-record loads appear in `APP.V_ACTIVITY`. `AUDIT_SV` includes UI_FLAGS and
UI_KPIS, so the agent reads the same numbers as the screens. In chat, a question about everything ("status report",
"all files") is answered from `V_UI_KPIS_BY_CONTRACT`, grouped by contract; a question naming no contract uses the
only contract with invoices and says so in one line; MERRY asks only when the question could mean two contracts.
**Reason:** The chat, the screens and the letters must never disagree, and every change must leave one history.

## D33 Who decided, "you", and suggestion clicks
**Decision:** (1) Answers about findings say who made the current decision: MERRY's own in the first person ("I cleared
this: ..."), an analyst override as theirs ("You dismissed this on <date>; your reason: <reason>"), from
`V_UI_FLAGS.DECIDED_BY / DECIDED_AT / OVERRIDE_REASON` (in AUDIT_SV). An override is never presented as MERRY's
judgment. (2) The person asking is "you" in every chat answer, as in welcome back: the agent is told who is asking and
`review.as_you` replaces any remaining raw user name; other people are named as the records name them. (3) A suggestion
click is recorded by the button's on_click and acted on at the top of the next run (`review.run_clicked`), so a long
action never runs under a "What I can do next" header whose buttons were not drawn.
**Reason:** An analyst's call must stay visibly the analyst's, the chat should speak to its user like the welcome back
does, and the header must never stand alone.

## D34 Delete a chat; Sources under every answer
**Decision:** (1) Every chat except a contract's main thread has a 🗑 with a confirm step; deleting removes its THREADS
row, all its CHAT_MESSAGES and its THREAD_FOCUS (`review.delete_chat`) and nothing else (decisions, approvals,
letters, uploads and activity stay). The main thread (thread_id = contract id) is pinned on top as "📌 Audit history"
with no delete, because it holds the audit runs and briefings the suggestions read.
**Post-demo:** make the suggestions table-derived only (no reads of the main thread's messages), so every chat can be
deleted, then drop the pin.
(2) Every chat answer ends with "Sources", built from the agent's tool results only, never from its text: contract
clauses (number and title; popover with the clause text), site records (table, equipment or worker, dates; popover
with the rows), documents (invoice and ticket numbers; the PDF opens from a presigned URL, or downloads from the stage
if that fails), and decisions or activity entries. Every item is looked up in the database and dropped if not found.
The stored EVIDENCE_REFS and clauses of a decision found in the results are listed separately as "Records behind this
decision". GET_PRESIGNED_URL takes no bind for its path, so the stored path is checked against a strict pattern and
inlined (`review.presigned_url`).
Status reports cite the Dashboard view.
**Reason:** Analysts need to tidy their chat list without risking audit records, and to check every answer against the
records it rests on.

## D35 Document sources are served by the app, not by a URL
**Decision:** Clicking a document source opens a popover in which the app reads the PDF's bytes from DOCS.DOC_STAGE
itself (`review.doc_bytes`) and offers them as a download button, with an inline preview (`st.pdf`) where the runtime
has it. No presigned URL is shown: a URL generated in the container runtime goes through its internal access point
and fails from a browser (S3 AccessDenied, explicit deny), although it worked when fetched server-side. The D34 gate
now checks that the app delivers the bytes and the download button, not a URL fetch.
**Rule:** anything browser-facing (links, downloads, previews, layout) must be verified in a browser; a server-side
check does not prove it works for the user.
**Reason:** The D34 gate passed on a server-side fetch while the link failed for the analyst.

## D36 Migration-ready repository: one app, one settings file, a clean slate
**Decision:** The repository is a clean starting point for another account.
- **One app.** The root Streamlit app (`<DB>.APP.MERRY`, container runtime: Dashboard, Issues, Ask MERRY, Data) is
  the only app, owned by `<DB>_APP`. The standalone warehouse app and the teammate's retired RAW_DB pipeline modules
  are removed (a zip of the workspace before the cleanup is on the original account's user stage).
- **One settings file.** `settings.py` holds the connection, database, role, warehouse, compute pool and the two
  models; code and SQL use `{{DB}}`, `{{ROLE}}`, `{{WAREHOUSE}}`, `{{COMPUTE_POOL}}`, `{{MODEL}}`,
  `{{CONTRACT_MODEL}}`. The letter's letterhead and sign-off and the agent's system prompt use the operator read from
  the contract (CONTRACTS.OPERATOR), not a company name.
- **One command.** `merry_agent/setup.py`: database objects, app, smoke test. `merry_chat/` and `snowflake.yml` are
  generated by it.
- **Clean slate.** Every table starts empty; only reference data is seeded. Site-record CSVs are uploaded like any
  file. Rows uploaded before any contract exists load with an empty site; when the first contract is confirmed and
  it is the only site, `APP.SP_CLAIM_SITE_RECORDS` (called by `SP_CONFIRM_CONTRACT`) gives them its site.
- **Starting over.** `tools/reset.py` (admin, with a confirm) empties all user data and the document stage, keeps the
  reference data and objects; `--contract` limits it to one contract (site records are kept).
- **Sample.** `tools/try_sample.py` uploads the sample pack through the app's own upload path in first-use order and
  runs the audit; expected 9 findings, PHP 287,000.00 upheld.
**Reason:** A new owner should get a working, empty MERRY from one settings file and one command, with nothing tied
to the account it was built in.

## D37 MERRY never waits: the chat is history, the Issues tab is where you act
**Decision:** Supersedes D27.7 (suggestions), D27.11/D27.14 (run suggestions and auto-start conditions), the D27 intake
rule "MERRY asks when…" and the D27.4 confirm/review offers.
- **Intake never asks.** Ambiguous d/m dates read day-first unless the contractor's order is known. A ticket spanning
  two weeks is filed to its first week. A ticket with no usable dates takes its contractor's invoice week when exactly
  one is known. With only one contract on file, an unmatched document is filed under it. Anything still unplaceable
  (unknown type, no contract, no dates) is UNREADABLE: excluded from the audit and listed in the Issues tab.
- **Same name, different content** replaces the filed copy (old row REPLACED, nothing deleted) and re-audits its week;
  the chat states it. A readable copy with the name of an UNREADABLE upload replaces it, which closes that issue.
- **Contracts are always confirmed.** With WARN rate warnings the worker confirms with AS_EXTRACTED_ACK as MERRY; the
  warnings are noted in the chat only. Invoice rates above the contract remain RATE findings.
- **Missing tickets are findings.** TICKET_BACKING also flags line days no uploaded ticket covers (not uploaded, or no
  ticket cited), under the same TICKET_COMPANY_SIGNATURE_REQUIRED gate, for the part of the line no other rule flagged.
- **The audit always starts** for every filed week that needs one, whatever state other files are in.
- **Clean invoices are approved automatically.** After a worker run, every DRAFT APPROVE invoice with no EXCEPTIONS row
  is approved through SP_APPROVE_RESOLUTION by "MERRY (auto)" (the PDF is saved) and the chat records it. Invoices with
  findings are approved when their findings are settled in Issues (SP_REVIEW_FINDING, as in D32).
- **Unreadable uploads are ordinary issues.** V_UI_FLAGS adds one row per UNREADABLE upload: flag_id `UPLOAD|<path>`,
  rule UNREADABLE, HIGH, ₱0, status from FINDING_REVIEWS. Confirmed / Dismissed close it; Recovered is refused. The
  Dashboard and KPI views are unchanged, so these rows count as findings with no peso amount.
- **Ask MERRY is an activity history.** No suggestion buttons, decision forms or editable letters. Under the latest
  message it states the open issues with SQL amounts and points to the Issues tab. Typed questions, file drops, PDF
  downloads and chat navigation stay.
- **Uploads go to the Audit history.** Every drop, from any chat's box or the sidebar, goes to the contract's main
  thread (thread_id = contract id; `_INTAKE` before any contract exists). With several contracts: the contract of the
  current chat, else the one with the latest activity. A side chat only gets "Sent N files to the 📌 Audit history"
  (an empty new chat is removed instead). New chats stay, for questions; uploads no longer bind or title them.
**Reason:** One place to act. The same decisions were offered in two places (chat buttons and Issues statuses), and
MERRY stopped for questions it could settle itself or report as an issue.

## D38 Correcting a misread date in Ask MERRY
**Decision:** The analyst corrects a document's date by saying it in Ask MERRY ("LCR-T-3301 is dated 1 September
2026"). The agent tool CorrectDocumentDates (AGENT.CORRECT_DOCUMENT_DATES) calls APP.SP_CORRECT_DOC_DATES, which:
- finds the one filed (or UNREADABLE) invoice or field ticket of that number or file name in the contract; refuses
  unknown or ambiguous documents, unreal or reversed dates, spans over 31 days, and weeks with an audit running;
- records the change in APP.DATE_CORRECTIONS (who, when, old and new dates and weeks, the analyst's words), shown in
  V_ACTIVITY so history questions can answer "who changed this date";
- stores the correction on the upload (INTAKE:date_correction) and refiles it under the week of the first date;
- marks the old and new weeks stale and re-audits both (PROCESS_PERIOD, force), posting a line in the Audit history.
The extract step uses a stored correction instead of the printed header or ticket date, and reads the document's
other dates in the day/month order that gives the corrected date, so a re-audit never brings the wrong date back.
The agent calls the tool only when the analyst states the correct date; it never guesses one. Dates only: amounts
are recomputed by SQL in the re-audit (rule 4).
- **Fewer misreads at intake.** A d/m-ambiguous date with no known contractor order takes the reading inside the
  contractor's term, else in a week where the contractor already has a filed invoice; day-first only when neither
  decides (D37's blanket day-first filed LCR-T-3301, printed 09/01/2026, in January).
**Reason:** Extraction can misread a date. The analyst knows the right one and should be able to fix it in one
sentence, with the change recorded and the audit redone from the corrected data.
