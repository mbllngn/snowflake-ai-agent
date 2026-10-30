-- MERRY database objects (rendered from settings.py by deploy.py). Job runner task (replaceable; left suspended, the app starts it with EXECUTE TASK).
-- @owner ADMIN runs as ACCOUNTADMIN, @owner APP as {{ROLE}} (no secondary roles).

-- @owner APP
create or replace task {{DB}}.APP.TK_RUN_JOBS
	warehouse={{WAREHOUSE}}
	USER_TASK_TIMEOUT_MS=3600000
	COMMENT='Runs queued {{DB}}.APP.JOBS (D25). Started by the app with EXECUTE TASK.'
	as CALL {{DB}}.APP.SP_RUN_JOBS();

