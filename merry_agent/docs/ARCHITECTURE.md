# MERRY: architecture

## Flow

```
contract PDF ──> SP_ONBOARD_CONTRACT ──> CONTRACT.* (contractors, rates, clauses, rule params)
                                              │  edited in app (EDIT_LOG), then confirmed
invoices + tickets ──> SP_EXTRACT_PERIOD ──> CURATED.*
site records (RAW, preloaded) ───────────────────┐
                                                 ▼
                       SP_RUN_CHECKS ──> AUDIT.EXCEPTIONS
                                                 ▼
                 agent review (MERRY_AGENT) ──> AUDIT.DECISIONS
                                                 ▼
                 SP_DRAFT_RESOLUTIONS ──> AUDIT.RESOLUTIONS (+ PDF on approve)
                                                 ▼
                 Streamlit app: Contracts | Review (chat) | Dashboard
```

`SP_PROCESS_PERIOD(contract_id, period_start)` runs extraction, checks, review, and drafting in order. It writes each step's status to `AUDIT.PERIOD_RUNS`, so the app can show progress. The app may also call the steps one by one.

## Schemas and table contracts

Column names are binding. Add columns if needed, but don't rename these.

### RAW: site records, preloaded from `out/*.csv`
`GATE_LOG`, `EQUIPMENT_LOG`, `TIMESHEETS`, `DAILY_REPORTS`, with columns as in README.md, using proper types (DATE, TIMESTAMP_NTZ, NUMBER). Each row carries the `site_id` of its site (from the contract, see D36).

### DOCS
- `DOC_STAGE`: see AGENTS.md.
- `PARSED_DOCS(file_path, doc_type CONTRACT/INVOICE/TICKET, contract_id, period_start, parsed VARIANT, parsed_at)`

### CONTRACT: filled by extraction, editable in the app
- `CONTRACTS(contract_id PK, title, operator, site_id, effective_date, source_file, status EXTRACTED|CONFIRMED|STALE, confirmed_by, confirmed_at)`
- `CONTRACTORS(contract_id, contractor_code, contractor_name, scope, term_start, term_end)`
- `RATES(contract_id, contractor_code, rate_code, item, unit, rate_php, standby_rate_php, is_equipment)`. `is_equipment` is true when rate_code contains `-EQ-`.
- `CLAUSES(contract_id, clause_no, title, article, clause_text, page)`: one row per clause; exhibits as separate rows.
- `RULE_PARAMS(contract_id, param, value VARIANT, source_clause)`. Extracted from the clause text by AI_COMPLETE with structured output. At minimum:
  - `STANDBY_EQUIPMENT_PCT` (0.5, clause 4.2)
  - `STANDBY_CREW_BILLABLE` (false, 4.2)
  - `HOURS_BASIS` ("GATE", 5.3)
  - `TRAVEL_MAX_HOURS_EACH_WAY` (2, 5.4)
  - `TICKET_COMPANY_SIGNATURE_REQUIRED` (true, 5.1)
  - `TICKET_BILL_ONCE` (true, 6.1)
  - `EXCLUDED_CHARGES` (["fuel surcharge", "unapproved mobilization"], 7.3)
  - `RATES_INCLUDE_FUEL` (true, 4.1)
  - `PAYMENT_OUTSIDE_TERM` (false, 2.1)

  A contract missing a clause gets no param, and the checks that need it are skipped for that contract.
- `EDIT_LOG(edit_id, contract_id, table_name, row_key, field, old_value, new_value, edited_by, edited_at)`

### CURATED: extracted documents
- `INVOICE_HEADERS(contract_id, invoice_no, contractor_code, invoice_date, period_start, period_end, subtotal, vat, total, source_file)`
- `INVOICE_LINES(invoice_no, line_no, description, rate_code, service_date, qty, unit, unit_rate, amount, ticket_refs ARRAY)`. If the invoice has no rate code, `rate_code` is matched to `CONTRACT.RATES` by description.
- `TICKETS(ticket_no, contract_id, contractor_code, ticket_date, signed_by_contractor, signed_by_company_rep, company_rep_name, handwritten, source_file)`
- `TICKET_ITEMS(ticket_no, item_type PERSON|EQUIPMENT|QUANTITY, item_id, description, qty, unit, hours)`. Kusina's weekly headcount sheet is expanded to one row per day.

### AUDIT
- `PERIOD_RUNS(run_id, contract_id, period_start, period_end, status, current_step, started_at, finished_at, is_stale)`
- `EXCEPTIONS(exception_id, run_id, contract_id, period_start, invoice_no, line_no, contractor_code, rule_type, summary, flagged_php, evidence VARIANT)`. `rule_type` is one of `RATE`, `MATH`, `DUPLICATE`, `TICKET_BACKING`, `ELIGIBILITY_TERM`, `ELIGIBILITY_EXCLUSION`, `SITE_EQUIPMENT_DAYS`, `SITE_HOURS`. `evidence` holds keys to the source rows (table + primary-key values), not copied text.
- `DECISIONS(exception_id, version, decision CONFIRM|CORRECT|WITHHOLD|DISMISS, upheld_php, overbilled_php, withheld_php, allowed_php, clauses ARRAY, evidence_refs ARRAY, rationale, decided_by AGENT|USER, created_at)`. The latest version is the current one. Definitions:
  - `upheld_php = overbilled_php + withheld_php`
  - `allowed_php = flagged_php − upheld_php`
- `RESOLUTIONS(contract_id, invoice_no, period_start, version, resolution_type DISPUTE|HOLD|DISPUTE_AND_HOLD|APPROVE, billed_php, disputed_php, held_php, pay_php, letter_text, status DRAFT|APPROVED, pdf_path, approved_by, approved_at)`. Every audited invoice gets exactly one current resolution, clean invoices included (type APPROVE, no letter). `pay_php = billed_php − disputed_php − held_php`, where billed is the subtotal before VAT.

### AGENT
- `CONTRACT_SEARCH`: a Cortex Search service on `CONTRACT.CLAUSES`, filterable by `contract_id`.
- `AUDIT_SV`: a semantic view over the RAW site records, `CONTRACT.CONTRACTORS`, `CONTRACT.RATES`, `CURATED` invoices and tickets, and `AUDIT.EXCEPTIONS` + `DECISIONS`, so the agent can answer history and pattern questions. Include synonyms such as standby/idle, typhoon/weather suspension, gate hours/on-site hours, crew/personnel.
- Tool procedures:
  - `GET_EXCEPTION_CONTEXT(exception_id)`: the exception, invoice line, linked tickets, evidence rows, the contractor's rates and term, and the contract's rule params.
  - `RECORD_DECISION(...)`: inserts a new `DECISIONS` version. It validates the amounts: they must be ≥ 0, the upheld amount can't exceed flagged, and CORRECT requires `allowed_php > 0`.
  - `GET_PAST_DECISIONS(contract_id, contractor_code, rule_type)`
  - `PROPOSE_REVISION(exception_id, ...)`: used during a chat challenge. It writes a pending revision that the app shows for the analyst to confirm. It never applies one directly.
- `MERRY_AGENT`: tools are CONTRACT_SEARCH, AUDIT_SV (Cortex Analyst), and the procedures above.
  - **Review mode** (one exception per call): it must call GET_EXCEPTION_CONTEXT, find the relevant clauses, check the site records, and then call RECORD_DECISION. Every decision cites at least one clause, except MATH, and at least one site-record or document row.
  - **Chat mode**: answers the analyst, scoped by the contract_id and period passed in the first message, with citations.

### APP
- `CHAT_MESSAGES(thread_id, seq, role USER|AGENT, msg_type TEXT|STEPS|BRIEFING|INVOICE_CARDS|LETTER|TOOL_TRACE, payload VARIANT, created_at)`. `thread_id = contract_id` (one thread per contract; `_INTAKE` before any contract exists; period messages carry `payload.periods`, D27).
- Streamlit app `APP.MERRY`.

## Resolution drafting

`SP_DRAFT_RESOLUTIONS(contract_id, period_start)`:

1. For each invoice, add up the current decisions into disputed, held, and pay. This is pure SQL.
2. Build the letter from a fixed template: subject, opening (invoice, site, period, contract ID), one numbered finding per non-dismissed decision (amount, a one-sentence reason, clause numbers), the payment line, the credit-note request if disputed, the release condition if held, and the sign-off "<operator>, Contracts and Audit." (the operator is the Company party read from the contract)
3. Only the one-sentence reasons may come from AI_COMPLETE, generated from the decision rationale.
4. After drafting, check that every peso amount in the letter equals a value in DECISIONS or RESOLUTIONS. If not, fail.

"Make it firmer" in the app calls AI_COMPLETE with the letter and the instruction, then runs the same amount check and rejects the edit if it changed any amount. **Approve** renders the PDF (reportlab or fpdf2 from the Snowflake Anaconda channel), puts it on the stage under `resolutions/`, and sets status APPROVED.

## The app (Streamlit in Snowflake)

**Header**, always visible: Contract dropdown (shows its status), Service period dropdown (periods with no documents are labelled), Contractor dropdown (All by default). Tabs: Review, Dashboard, Contracts.

**Contracts tab:**
- File uploader (PDF, multiple) → `PUT` to `contracts/` → `SP_ONBOARD_CONTRACT` per file, with `st.status` steps.
- Per contract: a contractors table, the rate card and rule params as `st.data_editor` (each change writes to `EDIT_LOG` and the target table, and marks audited periods stale), clause chips, and a Confirm button.

**Review tab** (chat-first):
- If the period isn't audited: the two-lane upload (contractor documents uploader; site records shown as up to date, with each table's max date), then Run audit with `st.status` showing the PERIOD_RUNS steps.
- If audited: the thread from `CHAT_MESSAGES`, then suggested prompts, then `st.chat_input`.
- **State pattern (required):** messages are stored as types, and invoice cards and letters are **rendered fresh from `AUDIT` tables on every rerun**. Buttons only write to tables, append messages, and call `st.rerun()`. Button keys are `<action>:<invoice_no>:<version>`. A card's state is never kept only in session state.
- Agent calls go through the Cortex Agents REST API. On the warehouse runtime use `_snowflake.send_snow_api_request`; on the container runtime use `requests` with the session token. The choice comes from the step 2 spike. Show tool steps in an expander under each answer.

**Dashboard tab:** (D29) reads only the APP views V_DASH_TOTALS, V_DASH_REPEATS, V_DASH_GRID and V_DASH_ACTIVITY (current decision and resolution versions, across all chats) for the selected contract: the plain-words total line with invoices waiting, the repeat-pattern callout (same contractor and rule_type upheld in ≥ 2 periods), the contractor × period status grid labelled by date range, and recent activity filtered by kind. Empty states for no contract and for a contract with no invoices.

## Gates

| Step | Gate |
|---|---|
| Contract | Extracted rates equal `EVAL.TRUTH_RATE_CARD` (13 rows, exact amounts, terms). Clauses 2.1, 4.1, 4.2, 5.1, 5.3, 5.4, 6.1, and 7.3 match story_bible.yaml text. Rule params as listed above. MSA-019 has 2 contractors and 5 rates. |
| Extraction | Field accuracy vs `EVAL.TRUTH_INVOICE_LINES` and `EVAL.TRUTH_TICKETS`, split clean / scanned / handwritten. Every line amount exact. AQH-T-0412 on AQH-INV-319 and AQH-INV-320. LCR-T-3306 unsigned. |
| Checks | 9 exceptions, ₱360,200, matching `EVAL.ANSWER_KEY` one-to-one. Changing `STANDBY_EQUIPMENT_PCT` or a term date in CONTRACT changes results; revert afterwards. |
| Search / Analyst | All `EVAL.QA_SAMPLES` pass. |
| Agent | 3 clean runs. Each: decisions and amounts match ANSWER_KEY; totals ₱287,000 upheld, ₱236,500 overbilled, ₱50,500 withheld, ₱13,200 dismissed. E1b cites 4.2 and the Sep 10–11 records; E8 cites 5.4. Report run-to-run differences and latency per exception. |
| Resolutions | One per audited invoice (19). pay = billed − disputed − held. PCS-INV-1042: billed ₱500,000, disputed ₱40,000, pay ₱460,000. The amount check passes for every letter. |
| App | The demo plan in PRODUCT.md runs end to end without errors. W2 audit time is recorded. |
