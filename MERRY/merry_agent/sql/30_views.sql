-- MERRY database objects (rendered from settings.py by deploy.py). Views in dependency order (replaceable).
-- @owner ADMIN runs as ACCOUNTADMIN, @owner APP as {{ROLE}} (no secondary roles).

-- @owner ADMIN
create or replace view {{DB}}.APP.V_ACTIVITY(
	CONTRACT_ID,
	AT,
	WHO,
	KIND,
	SUBJECT,
	SUMMARY,
	AMOUNT_EFFECT
) COMMENT='MERRY: who did what, when, per contract (uploads, confirmations, decisions, overrides, approvals, edits, chat actions). Read-only.'
 as
SELECT contract_id, at::TIMESTAMP_NTZ AS at, who, kind, subject, summary, amount_effect::NUMBER(14,2) AS amount_effect
FROM (
WITH dec AS (
  SELECT e.contract_id, d.created_at AS at, d.decided_by, d.decision, d.rationale, d.override_reason, d.version,
         e.invoice_no, e.line_no, e.rule_type, e.flagged_php,
         COALESCE(d.overbilled_php, 0) + COALESCE(d.withheld_php, 0) AS not_payable,
         LAG(d.decision) OVER (PARTITION BY d.exception_id ORDER BY d.version) AS prev_decision,
         l.label AS rule_label,
         COALESCE(w.word, LOWER(d.decision)) AS word,                       -- D29.5: status words, never codes
         LAG(COALESCE(w.word, LOWER(d.decision))) OVER (PARTITION BY d.exception_id ORDER BY d.version) AS prev_word
  FROM {{DB}}.AUDIT.DECISIONS d
  JOIN {{DB}}.AUDIT.EXCEPTIONS e ON e.exception_id = d.exception_id
  LEFT JOIN {{DB}}.APP.RULE_LABELS l ON l.rule_type = e.rule_type
  LEFT JOIN {{DB}}.APP.STATUS_WORDS w ON w.decision = d.decision
)
SELECT contract_id, MIN(uploaded_at) AS at, ANY_VALUE(uploaded_by) AS who, 'UPLOAD' AS kind,
       COALESCE(batch_id, MIN(file_name)) AS subject,
       'Uploaded ' || COUNT(*) || IFF(COUNT(*) = 1, ' file', ' files')
         || IFF(COUNT_IF(doc_kind = 'INVOICE') > 0, ' (' || COUNT_IF(doc_kind = 'INVOICE') || ' invoices, '
                || COUNT_IF(doc_kind = 'TICKET') || ' field tickets)', '') AS summary,
       NULL::NUMBER(14,2) AS amount_effect
FROM {{DB}}.APP.UPLOADS
WHERE contract_id IS NOT NULL AND COALESCE(intake_status, '') NOT IN ('NOT_RECEIVED', 'DUPLICATE', 'REPLACED')
GROUP BY contract_id, batch_id, IFF(batch_id IS NULL, file_path, NULL)

UNION ALL
SELECT contract_id, confirmed_at, confirmed_by, 'CONTRACT_CONFIRMED', contract_id,
       'Confirmed the contract values for ' || contract_id, NULL
FROM {{DB}}.CONTRACT.CONTRACTS WHERE confirmed_at IS NOT NULL

UNION ALL
SELECT contract_id, at,
       IFF(decided_by = 'AGENT', 'MERRY',
           COALESCE(REGEXP_SUBSTR(rationale, '(override|Accepted) by ([^:]+?)( from|:)', 1, 1, 'e', 2), 'analyst')),
       IFF(decided_by = 'AGENT', 'AGENT_DECISION', 'OVERRIDE'),
       invoice_no,
       IFF(decided_by = 'AGENT', 'MERRY found "' || word || '" on',
           'Changed "' || COALESCE(prev_word, '?') || '" to "' || word || '" on')
         || ' the ' || REGEXP_REPLACE(COALESCE(rule_label, rule_type), '^the ', '') || ' flag on line ' || line_no
         || ' (flagged ₱' || REGEXP_REPLACE(TO_VARCHAR(flagged_php, 'FM9,999,999,990.00'), '\\.00$', '') || ', not payable ₱'
         || REGEXP_REPLACE(TO_VARCHAR(not_payable, 'FM9,999,999,990.00'), '\\.00$', '') || ')'
         || IFF(override_reason IS NOT NULL, '. Reason: ' || override_reason, ''),
       not_payable::NUMBER(14,2)
FROM dec

UNION ALL
SELECT contract_id, approved_at, approved_by, 'APPROVAL', invoice_no,
       'Approved ' || invoice_no || ' v' || version || ': pay ₱' || REGEXP_REPLACE(TO_VARCHAR(pay_php, 'FM9,999,999,990.00'), '\\.00$', '')
         || IFF(disputed_php > 0, ', dispute ₱' || REGEXP_REPLACE(TO_VARCHAR(disputed_php, 'FM9,999,999,990.00'), '\\.00$', ''), '')
         || IFF(held_php > 0, ', hold ₱' || REGEXP_REPLACE(TO_VARCHAR(held_php, 'FM9,999,999,990.00'), '\\.00$', ''), ''),
       pay_php
FROM {{DB}}.AUDIT.RESOLUTIONS WHERE approved_at IS NOT NULL

UNION ALL
SELECT contract_id, edited_at, edited_by, 'CONTRACT_EDIT', table_name || '.' || field || ' ' || row_key,
       'Changed ' || LOWER(table_name) || ' ' || field || ' for ' || row_key || ' from '
         || COALESCE(old_value::STRING, '(blank)') || ' to ' || COALESCE(new_value::STRING, '(blank)'),
       NULL
FROM {{DB}}.CONTRACT.EDIT_LOG

UNION ALL
SELECT contract_id, resolved_at, resolved_by, 'CHAT_ACTION', action,
       'Confirmed a chat action: ' || LOWER(REPLACE(action, '_', ' ')) || ' for ' || ARRAY_SIZE(items) || ' invoice(s)',
       NULL
FROM {{DB}}.APP.PENDING_ACTIONS WHERE status = 'DONE'

UNION ALL
SELECT e.contract_id, r.reviewed_at, r.reviewed_by, 'REVIEW', e.invoice_no,
       IFF(r.status = 'OPEN', 'Reopened', 'Marked ' || LOWER(r.outcome)) || ' the '
         || REGEXP_REPLACE(COALESCE(l.label, LOWER(e.rule_type)), '^the ', '') || ' flag on ' || e.invoice_no
         || ' line ' || e.line_no || IFF(r.note IS NOT NULL AND r.note <> '', '. Note: ' || r.note, ''),
       NULL
FROM {{DB}}.AUDIT.FINDING_REVIEWS r
JOIN {{DB}}.AUDIT.EXCEPTIONS e ON e.exception_id = r.exception_id
LEFT JOIN {{DB}}.APP.RULE_LABELS l ON l.rule_type = e.rule_type

UNION ALL
SELECT contract_id, recorded_at, recorded_by, 'CREDIT_NOTE', invoice_no,
       'Recorded credit note ' || COALESCE(credit_ref, '') || ' on ' || invoice_no || ' for ₱'
         || REGEXP_REPLACE(TO_VARCHAR(amount_php, 'FM9,999,999,990.00'), '\\.00$', ''),
       amount_php
FROM {{DB}}.AUDIT.CREDIT_NOTES

UNION ALL
SELECT c.contract_id, MIN(s.loaded_at), ANY_VALUE(s.loaded_by), 'SITE_RECORDS', s.source_file,
       'Loaded ' || COUNT(*) || ' ' || LOWER(REPLACE(s.table_name, '_', ' ')) || ' row(s) from ' || s.source_file,
       NULL
FROM {{DB}}.RAW.SITE_RECORD_SOURCES s
JOIN {{DB}}.CONTRACT.CONTRACTS c ON c.site_id = s.site_id
GROUP BY c.contract_id, s.batch_id, s.source_file, s.table_name
);

-- @owner ADMIN
create or replace view {{DB}}.APP.V_DASH_ACTIVITY(
	CONTRACT_ID,
	AT,
	WHO,
	KIND,
	SUBJECT,
	SUMMARY,
	WHY,
	AMOUNT_EFFECT
) COMMENT='MERRY dashboard: V_ACTIVITY in plain voice (status words, reasons), latest first. Read-only.'
 as
WITH dec AS (
  SELECT e.contract_id, d.created_at AS at, d.decided_by, d.decision, d.rationale, d.override_reason,
         e.invoice_no, e.line_no, COALESCE(l.label, LOWER(e.rule_type)) AS rule_label, w.word,
         COALESCE(d.overbilled_php, 0) + COALESCE(d.withheld_php, 0) AS not_payable,
         LAG(w.word) OVER (PARTITION BY d.exception_id ORDER BY d.version) AS prev_word
  FROM {{DB}}.AUDIT.DECISIONS d
  JOIN {{DB}}.AUDIT.EXCEPTIONS e ON e.exception_id = d.exception_id
  LEFT JOIN {{DB}}.APP.RULE_LABELS l ON l.rule_type = e.rule_type
  LEFT JOIN {{DB}}.APP.STATUS_WORDS w ON w.decision = d.decision
)
SELECT contract_id, at::TIMESTAMP_NTZ AS at,
       IFF(decided_by = 'AGENT', 'MERRY',
           COALESCE(REGEXP_SUBSTR(rationale, '(override|Accepted) by ([^:]+?)( from|:)', 1, 1, 'e', 2), 'analyst')) AS who,
       IFF(decided_by = 'AGENT', 'AGENT_DECISION', 'OVERRIDE') AS kind,
       invoice_no AS subject,
       IFF(decided_by = 'AGENT', 'Found ', 'Changed ' || COALESCE('"' || prev_word || '" to ', ''))
         || IFF(decided_by = 'AGENT', '', '"') || COALESCE(word, LOWER(decision)) || IFF(decided_by = 'AGENT', '', '"')
         || ' on ' || invoice_no || ' line ' || line_no || ' (₱'
         || REGEXP_REPLACE(TO_VARCHAR(not_payable, 'FM9,999,999,990.00'), '\\.00$', '') || ' not payable)' AS summary,
       IFF(decided_by = 'AGENT', rule_label, override_reason) AS why,
       not_payable::NUMBER(14,2) AS amount_effect
FROM dec
UNION ALL
SELECT contract_id, at, who, kind, subject,
       REGEXP_REPLACE(summary, ' v[0-9]+:', ':') AS summary,
       NULL AS why, amount_effect
FROM {{DB}}.APP.V_ACTIVITY
WHERE kind NOT IN ('AGENT_DECISION', 'OVERRIDE');

-- @owner APP
create or replace view {{DB}}.AUDIT.V_DECISIONS(
	EXCEPTION_ID,
	VERSION,
	DECISION,
	UPHELD_PHP,
	OVERBILLED_PHP,
	WITHHELD_PHP,
	ALLOWED_PHP,
	CLAUSES,
	EVIDENCE_REFS,
	RATIONALE,
	DECIDED_BY,
	CREATED_AT,
	IS_STALE,
	REVIEW_TOKEN,
	BASIS,
	COMPUTATION,
	OVERRIDE_REASON,
	IS_CURRENT,
	IS_UPHELD
) as
SELECT d.*,
       version = MAX(version) OVER (PARTITION BY exception_id) AS is_current,
       upheld_php > 0 AS is_upheld
FROM {{DB}}.AUDIT.DECISIONS d;

-- @owner APP
create or replace view {{DB}}.AUDIT.V_RESOLUTIONS(
	CONTRACT_ID,
	INVOICE_NO,
	PERIOD_START,
	VERSION,
	RESOLUTION_TYPE,
	BILLED_PHP,
	DISPUTED_PHP,
	HELD_PHP,
	PAY_PHP,
	LETTER_TEXT,
	STATUS,
	PDF_PATH,
	APPROVED_BY,
	APPROVED_AT,
	PAY_VAT_PHP,
	PAY_TOTAL_PHP,
	DECISION_FINGERPRINT,
	IS_STALE,
	CREATED_AT,
	VAT_RATE,
	FINDINGS,
	REVISION_NOTE
) as
SELECT r.*
FROM {{DB}}.AUDIT.RESOLUTIONS r
QUALIFY r.version = MAX(r.version) OVER (PARTITION BY r.contract_id, r.invoice_no);

-- @owner ADMIN
create or replace view {{DB}}.APP.V_DASH_GRID(
	CONTRACT_ID,
	CONTRACTOR_CODE,
	CONTRACTOR_NAME,
	PERIOD_START,
	PERIOD_END,
	INVOICE_NO,
	RESOLUTION_STATUS,
	RESOLUTION_TYPE,
	WORDS
) COMMENT='MERRY dashboard: invoice status per contractor and period, in plain status words, from current versions. Read-only.'
 as
WITH f AS (
  SELECT e.invoice_no, ARRAY_SORT(ARRAY_UNIQUE_AGG(w.word)) AS words
  FROM {{DB}}.AUDIT.EXCEPTIONS e
  JOIN {{DB}}.AUDIT.V_DECISIONS d ON d.exception_id = e.exception_id AND d.is_current
  JOIN {{DB}}.APP.STATUS_WORDS w ON w.decision = d.decision
  GROUP BY e.invoice_no
)
SELECT h.contract_id, h.contractor_code, c.contractor_name, h.period_start, h.period_end, h.invoice_no,
       r.status AS resolution_status, r.resolution_type,
       COALESCE(f.words, ARRAY_CONSTRUCT()) AS words
FROM {{DB}}.CURATED.INVOICE_HEADERS h
LEFT JOIN {{DB}}.CONTRACT.CONTRACTORS c ON c.contract_id = h.contract_id AND c.contractor_code = h.contractor_code
LEFT JOIN {{DB}}.AUDIT.V_RESOLUTIONS r ON r.contract_id = h.contract_id AND r.invoice_no = h.invoice_no
LEFT JOIN f ON f.invoice_no = h.invoice_no;

-- @owner APP
create or replace view {{DB}}.APP.V_ISSUE_FACTS(
	EXCEPTION_ID,
	CONTRACT_ID,
	EQUIPMENT,
	DATES,
	TICKETS,
	EARLIER,
	TERM_END,
	CHARGE,
	ROLE,
	BILLED_RATE,
	CONTRACT_RATE,
	PRINTED,
	EXPECTED,
	QTY,
	UNIT_RATE,
	EXTRA_HOURS,
	STANDBY_PCT,
	WEATHER
) COMMENT='MERRY: plain-sentence facts per exception (step 12c polish, D27.13), from stored evidence and site records.'
 as
WITH it AS (
  SELECT e.exception_id, e.contract_id, i.value v
  FROM {{DB}}.AUDIT.EXCEPTIONS e, LATERAL FLATTEN(e.evidence:lines) l, LATERAL FLATTEN(l.value:items) i),
f AS (
  SELECT it.exception_id, ANY_VALUE(it.contract_id) contract_id,
         ARRAY_UNIQUE_AGG(v:equipment_id::STRING) equipment,
         ARRAY_UNIQUE_AGG(COALESCE(v:log_date, v:service_date, v:work_date)::STRING) dates,
         ARRAY_UNIQUE_AGG(COALESCE(v:ticket_no, ea.value:ticket_no)::STRING) tickets,
         ARRAY_UNIQUE_AGG(ea.value:invoice_no::STRING) earlier,
         MAX(v:term_end::STRING) term_end,
         ARRAY_UNIQUE_AGG(IFF(m.value:param::STRING = 'EXCLUDED_CHARGES', m.value:term::STRING, NULL)) charge,
         ARRAY_UNIQUE_AGG(v:role::STRING) role,
         MAX(v:billed_rate::NUMBER(14,2)) billed_rate, MAX(v:contract_rate::NUMBER(14,2)) contract_rate,
         MAX(v:printed::NUMBER(14,2)) printed, MAX(v:expected::NUMBER(14,2)) expected,
         MAX(v:qty::NUMBER) qty, MAX(v:unit_rate::NUMBER(14,2)) unit_rate, MAX(v:extra_hours::NUMBER) extra_hours
  FROM it, LATERAL FLATTEN(it.v:earlier, OUTER => TRUE) ea, LATERAL FLATTEN(it.v:matched, OUTER => TRUE) m
  GROUP BY it.exception_id)
SELECT f.*,
       (SELECT MAX(TRY_TO_DOUBLE(p.value::STRING)) FROM {{DB}}.CONTRACT.RULE_PARAMS p
         WHERE p.contract_id = f.contract_id AND p.param = 'STANDBY_EQUIPMENT_PCT') standby_pct,
       (SELECT MIN(d.weather) FROM {{DB}}.RAW.DAILY_REPORTS d
         WHERE d.ops_status <> 'NORMAL' AND ARRAY_CONTAINS(d.report_date::STRING::VARIANT, f.dates)) weather
FROM f;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_DASH_REPEATS(
	CONTRACT_ID,
	CONTRACTOR_CODE,
	CONTRACTOR_NAME,
	RULE_TYPE,
	RULE_LABEL,
	ASK_PHRASE,
	PERIODS,
	PERIOD_STARTS,
	INVOICES,
	DATES,
	NOT_PAYABLE_PHP
) COMMENT='MERRY dashboard: repeat patterns (same contractor and check upheld in 2+ periods), from current decisions. Read-only.'
 as
SELECT e.contract_id, e.contractor_code, ANY_VALUE(c.contractor_name) AS contractor_name, e.rule_type,
       ANY_VALUE(l.label) AS rule_label, ANY_VALUE(l.ask_phrase) AS ask_phrase,
       COUNT(DISTINCT e.period_start) AS periods,
       ARRAY_SORT(ARRAY_UNIQUE_AGG(e.period_start::STRING)) AS period_starts,
       ARRAY_SORT(ARRAY_UNIQUE_AGG(e.invoice_no)) AS invoices,
       ARRAY_SORT(ARRAY_DISTINCT(ARRAY_FLATTEN(ARRAY_AGG(COALESCE(f.dates, ARRAY_CONSTRUCT()))))) AS dates,
       SUM(COALESCE(d.overbilled_php, 0) + COALESCE(d.withheld_php, 0))::NUMBER(14,2) AS not_payable_php
FROM {{DB}}.AUDIT.EXCEPTIONS e
JOIN {{DB}}.AUDIT.V_DECISIONS d ON d.exception_id = e.exception_id AND d.is_current AND d.is_upheld
LEFT JOIN {{DB}}.CONTRACT.CONTRACTORS c ON c.contract_id = e.contract_id AND c.contractor_code = e.contractor_code
LEFT JOIN {{DB}}.APP.RULE_LABELS l ON l.rule_type = e.rule_type
LEFT JOIN {{DB}}.APP.V_ISSUE_FACTS f ON f.exception_id = e.exception_id
GROUP BY e.contract_id, e.contractor_code, e.rule_type
HAVING COUNT(DISTINCT e.period_start) >= 2;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_DASH_TOTALS(
	CONTRACT_ID,
	INVOICES,
	APPROVED,
	WAITING,
	NOT_REVIEWED,
	BILLED_PHP,
	PAY_PHP,
	DISPUTED_PHP,
	HELD_PHP
) COMMENT='MERRY dashboard: per contract, billed / to pay / disputed / on hold from current resolution versions, and invoices still waiting. Read-only.'
 as
WITH inv AS (
  SELECT h.contract_id, h.invoice_no, r.billed_php, r.pay_php, r.disputed_php, r.held_php, r.status
  FROM {{DB}}.CURATED.INVOICE_HEADERS h
  LEFT JOIN {{DB}}.AUDIT.V_RESOLUTIONS r ON r.contract_id = h.contract_id AND r.invoice_no = h.invoice_no
)
SELECT c.contract_id,
       COUNT(i.invoice_no)                                         AS invoices,
       COUNT_IF(i.status = 'APPROVED')                             AS approved,
       COUNT_IF(i.invoice_no IS NOT NULL AND COALESCE(i.status, '') <> 'APPROVED') AS waiting,
       COUNT_IF(i.invoice_no IS NOT NULL AND i.status IS NULL)     AS not_reviewed,
       COALESCE(SUM(i.billed_php), 0)::NUMBER(14,2)                AS billed_php,
       COALESCE(SUM(i.pay_php), 0)::NUMBER(14,2)                   AS pay_php,
       COALESCE(SUM(i.disputed_php), 0)::NUMBER(14,2)              AS disputed_php,
       COALESCE(SUM(i.held_php), 0)::NUMBER(14,2)                  AS held_php
FROM {{DB}}.CONTRACT.CONTRACTS c
LEFT JOIN inv i ON i.contract_id = c.contract_id
GROUP BY c.contract_id;

-- @owner APP
create or replace view {{DB}}.AUDIT.V_EXCEPTIONS(
	EXCEPTION_ID,
	RUN_ID,
	CONTRACT_ID,
	PERIOD_START,
	INVOICE_NO,
	LINE_NO,
	CONTRACTOR_CODE,
	RULE_TYPE,
	SUMMARY,
	FLAGGED_PHP,
	CREATED_AT,
	CLAUSE_REFS,
	EVIDENCE_SUMMARY
) as
WITH it AS (
  SELECT e.exception_id,
         ARRAY_TO_STRING(ARRAY_COMPACT(ARRAY_CONSTRUCT(
           i.value:equipment_id::STRING, i.value:log_date::STRING, i.value:status::STRING,
           i.value:reason::STRING, i.value:worker_id::STRING, i.value:ticket_no::STRING)), ' ') AS item_text
  FROM {{DB}}.AUDIT.EXCEPTIONS e,
       LATERAL FLATTEN(input => e.evidence:lines) l,
       LATERAL FLATTEN(input => l.value:items, OUTER => TRUE) i),
pr AS (
  SELECT e.exception_id,
         LISTAGG(DISTINCT p.value:param::STRING || ' (clause ' || p.value:source_clause::STRING || ')', '; ') AS params_text,
         ARRAY_AGG(DISTINCT p.value:source_clause::STRING) AS clauses
  FROM {{DB}}.AUDIT.EXCEPTIONS e, LATERAL FLATTEN(input => e.evidence:params) p
  GROUP BY 1)
SELECT e.exception_id, e.run_id, e.contract_id, e.period_start, e.invoice_no, e.line_no, e.contractor_code,
       e.rule_type, e.summary, e.flagged_php, e.created_at,
       ARRAY_TO_STRING(pr.clauses, ', ') AS clause_refs,
       e.summary
         || COALESCE(' | clauses: ' || pr.params_text, '')
         || COALESCE(' | evidence: ' || (SELECT LISTAGG(item_text, '; ') FROM it WHERE it.exception_id = e.exception_id AND item_text <> ''), '')
         AS evidence_summary
FROM {{DB}}.AUDIT.EXCEPTIONS e LEFT JOIN pr ON pr.exception_id = e.exception_id;

-- @owner APP
create or replace view {{DB}}.CURATED.INVOICE_LINE_DAYS(
	INVOICE_NO,
	LINE_NO,
	SERVICE_DATE,
	SOURCE
) as
SELECT l.invoice_no, l.line_no, f.value::DATE AS service_date, 'LINE' AS source
FROM {{DB}}.CURATED.INVOICE_LINES l, LATERAL FLATTEN(input => l.line_dates) f
WHERE ARRAY_SIZE(l.line_dates) > 0
UNION ALL
SELECT DISTINCT l.invoice_no, l.line_no, t.ticket_date, 'TICKET'
FROM {{DB}}.CURATED.INVOICE_LINES l
JOIN LATERAL FLATTEN(input => l.ticket_refs) r
JOIN {{DB}}.CURATED.TICKETS t ON t.ticket_no = r.value::STRING AND t.contract_id = l.contract_id
WHERE COALESCE(ARRAY_SIZE(l.line_dates), 0) = 0 AND t.ticket_date IS NOT NULL;

-- @owner APP
create or replace view {{DB}}.CURATED.V_EQUIPMENT(
	EQUIPMENT_ID,
	EQUIPMENT_NAME,
	CONTRACTOR_CODE
) as
SELECT equipment_id, MAX(equipment_name) AS equipment_name, MAX(contractor_code) AS contractor_code
FROM (
  SELECT equipment_id, NULL AS equipment_name, contractor_code FROM {{DB}}.RAW.EQUIPMENT_LOG
  UNION ALL
  SELECT subject_id, subject_name, contractor_code FROM {{DB}}.RAW.GATE_LOG WHERE subject_type = 'EQUIPMENT'
)
GROUP BY equipment_id;

-- @owner APP
create or replace view {{DB}}.CURATED.V_WORKERS(
	WORKER_ID,
	WORKER_NAME,
	CONTRACTOR_CODE,
	ROLE
) as
SELECT worker_id, MAX(worker_name) AS worker_name, MAX(contractor_code) AS contractor_code, MAX(role) AS role
FROM (
  SELECT worker_id, worker_name, contractor_code, role FROM {{DB}}.RAW.TIMESHEETS
  UNION ALL
  SELECT subject_id, subject_name, contractor_code, NULL FROM {{DB}}.RAW.GATE_LOG WHERE subject_type = 'PERSON'
)
GROUP BY worker_id;

-- @owner APP
create or replace view {{DB}}.CURATED.V_WORKER_DAY_HOURS(
	WORKER_ID,
	WORK_DATE,
	CONTRACTOR_CODE,
	ROLE,
	GATES,
	GATE_HOURS,
	TIMESHEET_HOURS,
	HOURS_OVER_GATE
) as
WITH g AS (
  SELECT subject_id AS worker_id, entry_date AS work_date, contractor_code,
         ROUND(SUM(DATEDIFF('minute', time_in, time_out)) / 60, 2) AS gate_hours,
         LISTAGG(DISTINCT gate, ', ') AS gates
  FROM {{DB}}.RAW.GATE_LOG WHERE subject_type = 'PERSON'
  GROUP BY 1, 2, 3),
t AS (
  SELECT worker_id, work_date, contractor_code, SUM(hours) AS timesheet_hours, MAX(role) AS role
  FROM {{DB}}.RAW.TIMESHEETS GROUP BY 1, 2, 3)
SELECT COALESCE(t.worker_id, g.worker_id) AS worker_id,
       COALESCE(t.work_date, g.work_date) AS work_date,
       COALESCE(t.contractor_code, g.contractor_code) AS contractor_code,
       t.role, g.gates,
       COALESCE(g.gate_hours, 0) AS gate_hours,
       COALESCE(t.timesheet_hours, 0) AS timesheet_hours,
       COALESCE(t.timesheet_hours, 0) - COALESCE(g.gate_hours, 0) AS hours_over_gate
FROM t FULL OUTER JOIN g ON g.worker_id = t.worker_id AND g.work_date = t.work_date;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_FLAGS(
  FLAG_ID,
  CONTRACT_ID,
  PERIOD_START,
  RULE_TYPE,
  RULE,
  SEVERITY,
  CONTRACTOR_CODE,
  SOURCE_DOC,
  LINE_NO,
  TICKET_NO,
  SUBJECT,
  WORK_DATE,
  BILLED_VALUE,
  ACTUAL_VALUE,
  IMPACT_PHP,
  DECISION,
  DECISION_WORD,
  DECISION_VERSION,
  CLAUSES,
  RATIONALE,
  STATUS,
  INVOICE_STATUS,
  REVIEW_NOTE,
  CREDIT_PHP,
  REASON,
  DECIDED_BY,
  DECIDED_AT,
  OVERRIDE_REASON
) COMMENT='MERRY (D32, D33): one row per audit finding in the shape of the root app''s Issues tab, from current decisions. STATUS: OPEN = drafted, not settled; CONFIRMED = upheld and settled or approved; DISMISSED = cleared and settled or approved; RECOVERED = credit note recorded. IMPACT_PHP is the upheld (not payable) amount. DECIDED_BY is MERRY for MERRY''s own decision, else the analyst who overrode it (OVERRIDE_REASON is their reason). Read-only.'
 as
WITH d AS (SELECT * FROM {{DB}}.AUDIT.V_DECISIONS WHERE is_current),
rv AS (SELECT * FROM {{DB}}.AUDIT.FINDING_REVIEWS
       QUALIFY ROW_NUMBER() OVER (PARTITION BY exception_id ORDER BY reviewed_at DESC) = 1),
cn AS (SELECT exception_id, SUM(amount_php) AS amt FROM {{DB}}.AUDIT.CREDIT_NOTES GROUP BY exception_id)
SELECT e.exception_id, e.contract_id, e.period_start, e.rule_type,
       UPPER(LEFT(COALESCE(l.label, LOWER(e.rule_type)), 1)) || SUBSTR(COALESCE(l.label, LOWER(e.rule_type)), 2),
       CASE WHEN d.decision IN ('CONFIRM', 'WITHHOLD') THEN 'HIGH' WHEN d.decision = 'CORRECT' THEN 'MEDIUM' ELSE 'LOW' END,
       e.contractor_code, e.invoice_no, e.line_no,
       f.tickets[0]::STRING,
       COALESCE(f.equipment[0]::STRING, f.role[0]::STRING),
       TRY_TO_DATE(ARRAY_MIN(f.dates)::STRING),
       e.flagged_php, d.allowed_php, d.upheld_php,
       d.decision, w.word, d.version, ARRAY_TO_STRING(d.clauses, ', '), d.rationale,
       CASE WHEN cn.exception_id IS NOT NULL THEN 'RECOVERED'
            WHEN rv.status = 'OPEN' AND COALESCE(r.status, '') <> 'APPROVED' THEN 'OPEN'
            WHEN r.status = 'APPROVED' OR (rv.status = 'SETTLED' AND rv.decision_version = d.version)
              THEN IFF(d.upheld_php > 0, 'CONFIRMED', 'DISMISSED')
            ELSE 'OPEN' END,
       r.status,
       COALESCE(rv.note, d.override_reason),
       cn.amt,
       REGEXP_REPLACE(REGEXP_REPLACE(e.summary, '^[A-Z_]+: ', ''), 'PHP +', '₱'),
       IFF(d.decided_by = 'AGENT', 'MERRY',
           COALESCE(REGEXP_SUBSTR(d.rationale, '(override|Accepted) by ([^:]+?)( from|:)', 1, 1, 'e', 2), 'analyst')),
       d.created_at,
       IFF(d.decided_by = 'AGENT', NULL, d.override_reason)
FROM {{DB}}.AUDIT.EXCEPTIONS e
JOIN d ON d.exception_id = e.exception_id
LEFT JOIN {{DB}}.APP.RULE_LABELS l ON l.rule_type = e.rule_type
LEFT JOIN {{DB}}.APP.STATUS_WORDS w ON w.decision = d.decision
LEFT JOIN {{DB}}.APP.V_ISSUE_FACTS f ON f.exception_id = e.exception_id
LEFT JOIN {{DB}}.AUDIT.V_RESOLUTIONS r ON r.contract_id = e.contract_id AND r.invoice_no = e.invoice_no
LEFT JOIN rv ON rv.exception_id = e.exception_id
LEFT JOIN cn ON cn.exception_id = e.exception_id;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_KPIS_BY_CONTRACT(
  CONTRACT_ID,
  INVOICES,
  INVOICES_APPROVED,
  INVOICES_WAITING,
  INVOICES_WITH_FINDINGS,
  TICKETS,
  DOCS_REVIEWED,
  SPEND_REVIEWED,
  FINDINGS,
  OPEN_FLAGS,
  CONFIRMED_FLAGS,
  DISMISSED_FLAGS,
  RECOVERED_FLAGS,
  UPHELD_PHP,
  POTENTIAL_ISSUES,
  CONFIRMED_ISSUES,
  RECOVERED
) COMMENT='MERRY (D32): the Dashboard numbers per contract, the same figures as the chat status report. SPEND_REVIEWED is invoice total due incl. VAT; UPHELD_PHP is the not-payable amount of all current decisions; POTENTIAL_ISSUES that of OPEN findings; CONFIRMED_ISSUES that of CONFIRMED and RECOVERED findings; RECOVERED the credit notes recorded. Read-only.'
 as
WITH inv AS (
  SELECT h.contract_id, COUNT(*) AS invoices, SUM(h.total) AS spend, COUNT_IF(r.status = 'APPROVED') AS approved
  FROM {{DB}}.CURATED.INVOICE_HEADERS h
  LEFT JOIN {{DB}}.AUDIT.V_RESOLUTIONS r ON r.contract_id = h.contract_id AND r.invoice_no = h.invoice_no
  GROUP BY h.contract_id),
tk AS (SELECT contract_id, COUNT(*) AS tickets FROM {{DB}}.CURATED.TICKETS GROUP BY contract_id),
fl AS (
  SELECT contract_id, COUNT(*) AS findings, COUNT(DISTINCT source_doc) AS inv_with,
         COUNT_IF(status = 'OPEN') AS n_open, COUNT_IF(status = 'CONFIRMED') AS n_conf,
         COUNT_IF(status = 'DISMISSED') AS n_dis, COUNT_IF(status = 'RECOVERED') AS n_rec,
         SUM(impact_php) AS upheld,
         SUM(IFF(status = 'OPEN', impact_php, 0)) AS potential,
         SUM(IFF(status IN ('CONFIRMED', 'RECOVERED'), impact_php, 0)) AS confirmed
  FROM {{DB}}.APP.V_UI_FLAGS GROUP BY contract_id),
cn AS (SELECT contract_id, SUM(amount_php) AS amt FROM {{DB}}.AUDIT.CREDIT_NOTES GROUP BY contract_id)
SELECT c.contract_id,
       COALESCE(inv.invoices, 0), COALESCE(inv.approved, 0), COALESCE(inv.invoices, 0) - COALESCE(inv.approved, 0),
       COALESCE(fl.inv_with, 0), COALESCE(tk.tickets, 0), COALESCE(inv.invoices, 0) + COALESCE(tk.tickets, 0),
       COALESCE(inv.spend, 0)::NUMBER(14,2),
       COALESCE(fl.findings, 0), COALESCE(fl.n_open, 0), COALESCE(fl.n_conf, 0), COALESCE(fl.n_dis, 0), COALESCE(fl.n_rec, 0),
       COALESCE(fl.upheld, 0)::NUMBER(14,2), COALESCE(fl.potential, 0)::NUMBER(14,2),
       COALESCE(fl.confirmed, 0)::NUMBER(14,2), COALESCE(cn.amt, 0)::NUMBER(14,2)
FROM {{DB}}.CONTRACT.CONTRACTS c
LEFT JOIN inv ON inv.contract_id = c.contract_id
LEFT JOIN tk ON tk.contract_id = c.contract_id
LEFT JOIN fl ON fl.contract_id = c.contract_id
LEFT JOIN cn ON cn.contract_id = c.contract_id;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_KPIS(
  CONTRACTS,
  INVOICES,
  INVOICES_APPROVED,
  INVOICES_WAITING,
  INVOICES_WITH_FINDINGS,
  DOCS_REVIEWED,
  SPEND_REVIEWED,
  FINDINGS,
  OPEN_FLAGS,
  CONFIRMED_FLAGS,
  DISMISSED_FLAGS,
  RECOVERED_FLAGS,
  UPHELD_PHP,
  POTENTIAL_ISSUES,
  CONFIRMED_ISSUES,
  RECOVERED
) COMMENT='MERRY (D32): the root app Dashboard metric cards, one row: totals of V_UI_KPIS_BY_CONTRACT. Read-only.'
 as
SELECT COALESCE(COUNT_IF(invoices > 0), 0), COALESCE(SUM(invoices), 0), COALESCE(SUM(invoices_approved), 0),
       COALESCE(SUM(invoices_waiting), 0), COALESCE(SUM(invoices_with_findings), 0), COALESCE(SUM(docs_reviewed), 0),
       COALESCE(SUM(spend_reviewed), 0)::NUMBER(14,2), COALESCE(SUM(findings), 0), COALESCE(SUM(open_flags), 0),
       COALESCE(SUM(confirmed_flags), 0), COALESCE(SUM(dismissed_flags), 0), COALESCE(SUM(recovered_flags), 0),
       COALESCE(SUM(upheld_php), 0)::NUMBER(14,2), COALESCE(SUM(potential_issues), 0)::NUMBER(14,2),
       COALESCE(SUM(confirmed_issues), 0)::NUMBER(14,2), COALESCE(SUM(recovered), 0)::NUMBER(14,2)
FROM {{DB}}.APP.V_UI_KPIS_BY_CONTRACT;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_TIMESHEETS COMMENT='MERRY (D32): Data tab - timesheets, with the source file of uploaded rows. Read-only.' as
SELECT t.*, COALESCE(s.source_file, 'data pack') AS source FROM {{DB}}.RAW.TIMESHEETS t
LEFT JOIN {{DB}}.RAW.SITE_RECORD_SOURCES s ON s.table_name = 'TIMESHEETS' AND s.row_key = t.work_date::STRING || '|' || t.worker_id;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_GATE_LOG COMMENT='MERRY (D32): Data tab - gate log, with the source file of uploaded rows. Read-only.' as
SELECT t.*, COALESCE(s.source_file, 'data pack') AS source FROM {{DB}}.RAW.GATE_LOG t
LEFT JOIN {{DB}}.RAW.SITE_RECORD_SOURCES s ON s.table_name = 'GATE_LOG' AND s.row_key = t.gate_entry_id;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_DAILY_REPORTS COMMENT='MERRY (D32): Data tab - daily reports, with the source file of uploaded rows. Read-only.' as
SELECT t.*, COALESCE(s.source_file, 'data pack') AS source FROM {{DB}}.RAW.DAILY_REPORTS t
LEFT JOIN {{DB}}.RAW.SITE_RECORD_SOURCES s ON s.table_name = 'DAILY_REPORTS' AND s.row_key = t.report_date::STRING || '|' || t.site_id;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_EQUIPMENT_LOG COMMENT='MERRY (D32): Data tab - equipment log, with the source file of uploaded rows. Read-only.' as
SELECT t.*, COALESCE(s.source_file, 'data pack') AS source FROM {{DB}}.RAW.EQUIPMENT_LOG t
LEFT JOIN {{DB}}.RAW.SITE_RECORD_SOURCES s ON s.table_name = 'EQUIPMENT_LOG' AND s.row_key = t.log_date::STRING || '|' || t.equipment_id;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_TICKETS COMMENT='MERRY (D32): Data tab - field tickets read by MERRY. Read-only.' as
SELECT * FROM {{DB}}.CURATED.TICKETS;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_TICKET_ITEMS COMMENT='MERRY (D32): Data tab - field ticket items. Read-only.' as
SELECT * FROM {{DB}}.CURATED.TICKET_ITEMS;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_INVOICES COMMENT='MERRY (D32): Data tab - invoice headers read by MERRY. Read-only.' as
SELECT * FROM {{DB}}.CURATED.INVOICE_HEADERS;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_INVOICE_LINES COMMENT='MERRY (D32): Data tab - invoice lines. Read-only.' as
SELECT * FROM {{DB}}.CURATED.INVOICE_LINES;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_CONTRACT_RATES COMMENT='MERRY (D32): Data tab - contract rates (Exhibit A, from contract extraction). Read-only.' as
SELECT * FROM {{DB}}.CONTRACT.RATES;

-- @owner ADMIN
create or replace view {{DB}}.APP.V_UI_DATA_CONTRACT_TERMS COMMENT='MERRY (D32): Data tab - contractors and their terms (Exhibit B). Read-only.' as
SELECT * FROM {{DB}}.CONTRACT.CONTRACTORS;
