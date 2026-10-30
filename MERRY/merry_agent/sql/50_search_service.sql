-- MERRY database objects (rendered from settings.py by deploy.py). Cortex Search: created once, then kept (its index is rebuilt by TARGET_LAG, not by redeploys).
-- @owner ADMIN runs as ACCOUNTADMIN, @owner APP as {{ROLE}} (no secondary roles).

-- @owner APP
CREATE CORTEX SEARCH SERVICE IF NOT EXISTS {{DB}}.AGENT.CONTRACT_SEARCH
  ON SEARCH_TEXT
  ATTRIBUTES CONTRACT_ID,CLAUSE_NO
  WAREHOUSE = {{WAREHOUSE}}
  TARGET_LAG = '1 day'
  EMBEDDING_MODEL = 'snowflake-arctic-embed-m-v1.5'
  COMMENT = 'MERRY contract clauses. Filter by contract_id and clause_no. referenced_text holds clauses this one cites.'
AS
WITH refs AS (
  SELECT c.contract_id, c.clause_no,
         LISTAGG(DISTINCT '[' || r.clause_no || '] ' || r.clause_text, ' ') AS referenced_text
  FROM {{DB}}.CONTRACT.CLAUSES c
  JOIN LATERAL FLATTEN(input => REGEXP_SUBSTR_ALL(c.clause_text, '(Section|Clause|clause|section)s? ([0-9]+\\.[0-9]+)', 1, 1, 'e', 2)) f
  JOIN {{DB}}.CONTRACT.CLAUSES r
    ON r.contract_id = c.contract_id AND r.clause_no = f.value::STRING AND r.clause_no <> c.clause_no
  GROUP BY 1, 2)
SELECT c.contract_id,
       c.clause_no,
       c.title,
       c.article,
       c.page,
       c.clause_text,
       refs.referenced_text,
       'Clause ' || c.clause_no || ' ' || COALESCE(c.title, '') || ' (' || COALESCE(c.article, '') || '): ' || c.clause_text
         || COALESCE(' Referenced: ' || refs.referenced_text, '') AS search_text
FROM {{DB}}.CONTRACT.CLAUSES c
LEFT JOIN refs ON refs.contract_id = c.contract_id AND refs.clause_no = c.clause_no;

