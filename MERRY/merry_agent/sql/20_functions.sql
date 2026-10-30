-- MERRY database objects (rendered from settings.py by deploy.py). Functions (replaceable).
-- @owner ADMIN runs as ACCOUNTADMIN, @owner APP as {{ROLE}} (no secondary roles).

-- @owner APP
CREATE OR REPLACE FUNCTION {{DB}}.AUDIT.FN_AMOUNTS("TXT" VARCHAR)
RETURNS ARRAY
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'amounts'
COMMENT='MERRY: money amounts in a text (PHP/₱ prefixed, comma-grouped, or 4+ digits with 2 decimals), as 2-decimal strings.'
AS '
import re
from decimal import Decimal
PAT = re.compile(
    r''(?:PHP|Php|php|₱)\\s?(\\d[\\d,]*(?:\\.\\d+)?)''                      # currency-prefixed
    r''|(?<![\\w.,/-])(\\d{1,3}(?:,\\d{3})+(?:\\.\\d+)?)(?![\\w-])''          # comma-grouped
    r''|(?<![\\w.,/-])(\\d{4,}\\.\\d{2})(?![\\d])'')                         # 1234.56
def amounts(txt):
    if not txt:
        return []
    out = []
    for m in PAT.finditer(txt):
        s = next(g for g in m.groups() if g)
        try:
            out.append(str(Decimal(s.replace('','', '''')).quantize(Decimal(''0.01''))))
        except Exception:
            pass
    return out
';

-- @owner APP
CREATE OR REPLACE FUNCTION {{DB}}.AUDIT.FN_CLAUSE_REFS("TXT" VARCHAR)
RETURNS ARRAY
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'refs'
COMMENT='MERRY: clause numbers (n.m) and Exhibit letters cited in a text, distinct and sorted.'
AS '
import re
CLAUSE = re.compile(r''(?<![\\w.,/-])(\\d{1,2}\\.\\d{1,2})(?![\\d.,%]?\\d)(?![%\\w-])'')
EXHIBIT = re.compile(r''\\bExhibits?\\s+([A-Z])\\b'', re.I)
def refs(txt):
    if not txt:
        return []
    out = {m.group(1) for m in CLAUSE.finditer(txt)}
    out |= {''Exhibit '' + m.group(1).upper() for m in EXHIBIT.finditer(txt)}
    return sorted(out)
';

