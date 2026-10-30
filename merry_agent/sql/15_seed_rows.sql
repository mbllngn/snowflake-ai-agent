-- MERRY database objects (rendered from settings.py by deploy.py). App word tables: inserts missing rows, never updates or deletes.
-- @owner ADMIN runs as ACCOUNTADMIN, @owner APP as {{ROLE}} (no secondary roles).

-- @owner APP
MERGE INTO {{DB}}.APP.RULE_LABELS t USING (SELECT * FROM VALUES
  ('RATE', 'billing above the contract rate', 'billed above the contract rate'),
  ('MATH', 'arithmetic errors on the invoice', 'made arithmetic errors on an invoice'),
  ('DUPLICATE', 'the same item billed twice', 'billed the same item twice'),
  ('TICKET_BACKING', 'work billed without a signed field ticket', 'billed work without a signed field ticket'),
  ('ELIGIBILITY_TERM', 'work billed outside the contract term', 'billed work outside the contract term'),
  ('ELIGIBILITY_EXCLUSION', 'charges the contract excludes', 'billed charges the contract excludes'),
  ('SITE_EQUIPMENT_DAYS', 'idle equipment billed at the full rate', 'billed idle equipment at the full rate'),
  ('SITE_HOURS', 'more hours billed than the gate log shows', 'billed more hours than the gate log shows'),
  ('UNREADABLE', 'document could not be read', 'sent a document that could not be read')
) s (RULE_TYPE, LABEL, ASK_PHRASE)
ON t.RULE_TYPE = s.RULE_TYPE
WHEN NOT MATCHED THEN INSERT (RULE_TYPE, LABEL, ASK_PHRASE) VALUES (s.RULE_TYPE, s.LABEL, s.ASK_PHRASE);

-- @owner APP
MERGE INTO {{DB}}.APP.STATUS_WORDS t USING (SELECT * FROM VALUES
  ('CORRECT', 'overcharged in part'),
  ('CONFIRM', 'overcharged'),
  ('WITHHOLD', 'on hold until signed'),
  ('DISMISS', 'checked, it''s fine')
) s (DECISION, WORD)
ON t.DECISION = s.DECISION
WHEN NOT MATCHED THEN INSERT (DECISION, WORD) VALUES (s.DECISION, s.WORD);

-- @owner APP
MERGE INTO {{DB}}.APP.PLAIN_PHRASES t USING (SELECT * FROM VALUES
  ('SITE_EQUIPMENT_DAYS', 'CORRECT', '{who} charged full price for {equipment} that sat idle[ during the {weather}][ on {dates}]. Fair price is [{standby}, so they overcharged ]{upheld}.'),
  ('SITE_EQUIPMENT_DAYS', '*', '{who} charged for {equipment}[ on {dates}], when the equipment log shows it wasn''t working. That''s {upheld}.'),
  ('SITE_HOURS', 'DISMISS', '{who} billed more hours than the gate log shows[ on {dates}], but the extra is travel time the contract allows.'),
  ('SITE_HOURS', '*', '{who} billed[ {extra_hours}] more hours than the gate log shows. That''s {upheld}.'),
  ('DUPLICATE', '*', '{who} billed[ field ticket {tickets}] again; it was already on[ {earlier}]. That''s {upheld}.'),
  ('ELIGIBILITY_TERM', '*', '{who} billed work[ on {dates}][, after their contract ended on {term_end}]. That''s {upheld}.'),
  ('ELIGIBILITY_EXCLUSION', '*', '{who} added a charge the contract doesn''t allow[ ({charge})]. That''s {upheld}.'),
  ('RATE', '*', '{who} billed[ a {role}] above the contract rate[ ({billed_rate} instead of {contract_rate})]. That''s {upheld} too much.'),
  ('MATH', '*', '{who} added up a line wrong[: it says {printed}, but {qty} × {unit_rate} is {expected}]. That''s {upheld} too much.'),
  ('TICKET_BACKING', '*', '{who} billed work[ on field ticket {tickets}] without a field ticket signed by the company rep. I''d hold {upheld} until a signed ticket is provided.'),
  ('SIGNATURE_UNCLEAR', 'VERIFY', 'Please look at {doc} yourself: I couldn''t tell whether the company rep signed it.'),
  ('*', 'VERIFY', 'Please look at {doc} yourself: I couldn''t read part of it clearly.')
) s (RULE_TYPE, DECISION, TEMPLATE)
ON t.RULE_TYPE = s.RULE_TYPE AND t.DECISION = s.DECISION
WHEN NOT MATCHED THEN INSERT (RULE_TYPE, DECISION, TEMPLATE) VALUES (s.RULE_TYPE, s.DECISION, s.TEMPLATE);

