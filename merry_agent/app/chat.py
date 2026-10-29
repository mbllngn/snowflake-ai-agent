# MERRY chat text helpers, shared by the Review tab (review.py) and the gates (12_review/validate_chat_rewrite.py,
# 12_review/validate_12c.py). No Streamlit imports: callers pass their own q(sql, params) -> rows.
# Rows must be plain dicts (review.qd converts Snowpark Rows at the boundary); _q asserts it so a Row fails clearly.
# Step 12c (D27): date-range wording, the batch description (batch_summary) and the "What I can do next" suggestions
# (next_steps) are pure functions of table state: no LLM, no Snowflake calls.
import json
import re
from datetime import date, timedelta

# Plain phrases for each check (step 12c, D27.10): read from APP.RULE_LABELS, the table SP_BRIEFING reads too, so a
# rule is worded the same in the briefing facts, the pattern line and the suggestions. RULE_LABEL: "Repeat pattern:
# PumpCo, <label>"; RULE_ASK: "Has PumpCo <ask> in earlier periods?". Callers run load_labels(q) once per script run.
RULE_LABEL = {}
RULE_ASK = {}
# Step 12c polish (D27.13): MERRY's plain voice in chat, from APP.STATUS_WORDS and APP.PLAIN_PHRASES (no copy here).
STATUS_WORD = {}
PLAIN = {}


def load_labels(q):
    rows = _q(q, "SELECT rule_type, label, ask_phrase FROM {{DB}}.APP.RULE_LABELS")
    RULE_LABEL.clear()
    RULE_ASK.clear()
    RULE_LABEL.update({r["RULE_TYPE"]: r["LABEL"] for r in rows})
    RULE_ASK.update({r["RULE_TYPE"]: r["ASK_PHRASE"] for r in rows})
    STATUS_WORD.clear()
    PLAIN.clear()
    STATUS_WORD.update({r["DECISION"]: r["WORD"] for r in _q(q, "SELECT decision, word FROM {{DB}}.APP.STATUS_WORDS")})
    PLAIN.update({(r["RULE_TYPE"], r["DECISION"]): r["TEMPLATE"]
                  for r in _q(q, "SELECT rule_type, decision, template FROM {{DB}}.APP.PLAIN_PHRASES")})


def plain(x, what="row"):
    """Fail clearly if a caller passes anything but a plain dict (e.g. a Snowpark Row)."""
    assert isinstance(x, dict), f"chat.py takes plain dicts; got {type(x).__module__}.{type(x).__name__} for {what}. " \
                                f"Convert at the boundary (review.qd / Row.as_dict)."
    return x


def _q(q, sql, params=None):
    return [plain(r, sql.split()[0:6] and " ".join(sql.split()[:6])) for r in (q(sql, params) if params else q(sql))]


REWRITE_MODEL = "{{MODEL}}"

# A message depends on earlier turns if it uses a reference word or opens elliptically. "this contract/period/week"
# names the chat's own scope and is not a reference.
SCOPE_PHRASE = re.compile(r"(?i)\b(this|that|the same)\s+(contract|service period|period|week|month)\b")
REFERENCE = re.compile(r"(?i)\b(that|this|these|those|it|its|they|them|their|again|same|also|too|either|instead)\b")
ELLIPTICAL = re.compile(r"(?i)^\s*(what|how)\s+about\b|^\s*(and|or|same for|also|then)\b")
CLAIM_WORDS = re.compile(r"(?i)\b(say|says|said|claim\w*|state[sd]?|stating|alleg\w*|assert\w*|insist\w*|"
                         r"report(s|ed)? that|misreport\w*|accus\w*|admit\w*|dispute[sd]? that)\b")
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"])}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
DATE_MD = re.compile(rf"(?i)\b{MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b")
DATE_DM = re.compile(rf"(?i)\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{MON}")
AMOUNT = re.compile(r"(?i)(?:₱|php\s?)\s?(\d[\d,]*(?:\.\d+)?)|\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)\b")


def short_name(name):
    return (name or "").split()[0].rstrip(",") if name else ""


def pattern_prompt(p):
    """Suggested prompt for one briefing repeat pattern, built only from the pattern's own fields."""
    who = short_name(p.get("contractor_name")) or p.get("contractor_code")
    ask = RULE_ASK.get(p.get("rule_type"))
    if ask:
        return f"Has {who} {ask} in earlier periods?"
    return f"Has {who} had {(p.get('rule_type') or '').lower().replace('_', ' ')} findings in earlier periods?"


def suggestions(brief):
    out = []
    for p in (brief or {}).get("patterns") or []:
        s = pattern_prompt(p)
        if s not in out:
            out.append(s)
    for v in (brief or {}).get("verify_flags") or []:
        out.append(f"What should I verify on {v.get('DOC_NO') or v.get('doc_no')}?")
    return out[:4]


def depends_on_context(question):
    t = SCOPE_PHRASE.sub(" ", question or "")
    return bool(REFERENCE.search(t) or ELLIPTICAL.search(t))


def parties(q, cid):
    """Contractor name words and codes for the contract, from CONTRACT.CONTRACTORS (lowercase)."""
    out = {}
    for r in _q(q, "SELECT contractor_code c, contractor_name n FROM {{DB}}.CONTRACT.CONTRACTORS WHERE contract_id = ?", [cid]):
        disp = short_name(r["N"]) or r["C"]
        for w in {r["C"], short_name(r["N"])} - {"", None}:
            out[w.lower()] = disp
    return out


def mentioned(text, names):
    return {names[w] for w in names if re.search(rf"(?i)\b{re.escape(w)}\b", text or "")}


def claim_words(text):
    return {m.group(0).lower() for m in CLAIM_WORDS.finditer(text or "")}


def adds_claim(rewrite, typed):
    """True if the rewrite introduces claim or attribution wording the analyst did not type."""
    return bool(claim_words(rewrite) - claim_words(typed))


def dates(text):
    """Dates as (month, day): 2026-09-10, Sep 10, September 10th, 10 Sep."""
    t = text or ""
    out = {(int(m), int(d)) for _, m, d in DATE_ISO.findall(t)}
    out |= {(MONTHS[mo[:3].lower()], int(d)) for mo, d in DATE_MD.findall(t)}
    out |= {(MONTHS[mo[:3].lower()], int(d)) for d, mo in DATE_DM.findall(t)}
    return out


def amounts(text):
    """Peso amounts (PHP/₱ prefixed, or bare numbers of 4+ digits); dates, years and document numbers are not amounts."""
    t = DATE_ISO.sub(" ", text or "")
    t = re.sub(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)*-\d+\b|\b(?:19|20)\d{2}\b", " ", t)
    return {float((a or b).replace(",", "")) for a, b in AMOUNT.findall(t) if (a or b)}


def adds_facts(rewrite, allowed_text):
    """True if the rewrite contains a date or amount that is not in the analyst's own words."""
    return bool(dates(rewrite) - dates(allowed_text)) or bool(amounts(rewrite) - amounts(allowed_text))


def standalone(q, msgs, question, cid, suggested=False, focus=None):
    """Step 12b: decide in code whether a message depends on earlier turns; only those are rewritten (AI_COMPLETE over
    the analyst's own last 3 questions; MERRY's answers are never passed). Suggested prompts and self-contained
    messages are sent unchanged. Guards: a rewrite with a date or amount the analyst never typed falls back to the
    typed text plus the latest earlier question; a follow-up that switches contractor must be a neutral check, and a
    rewrite that carries a claim onto the new party (or that the model cannot write) becomes a question to the analyst.
    Returns (text, mode), mode in VERBATIM / REWRITTEN / FALLBACK / CLARIFY; for CLARIFY text is shown to the analyst.
    D29.5: focus = the thread's current subject (APP.THREAD_FOCUS, built by review.thread_focus): {"invoice_no",
    "contractor", "fact"}. References resolve to it first, then to the newest earlier message that names something; a
    rewrite naming another invoice than the focus (and than the analyst typed) is corrected to the focus in code; the
    focus's last action is appended as a plain fact from the records (never from MERRY's answers)."""
    if suggested:
        return question, "VERBATIM"
    earlier = [m["p"].get("text") for m in msgs if m["role"] == "USER" and m["type"] == "TEXT" and m["p"].get("text")]
    earlier = earlier[-3:]
    if not earlier or not depends_on_context(question):
        return question, "VERBATIM"
    try:
        names = parties(q, cid)
    except Exception:
        names = {}
    new = mentioned(question, names) - mentioned("\n".join(earlier), names)
    who = " and ".join(sorted(new))
    clarify = (f"Which issue should I check for {who}? Name the check or the invoice and I will look at {who}'s "
               f"records for this period.", "CLARIFY")
    if new:
        rule = (f"The new message switches to {who}, a different party from the earlier questions. Rewrite it as a "
                f"neutral check of the same issue type for {who}: describe the issue type in general terms, never as "
                f"something {who} said, claimed, reported or did. Carry over no claim, attribution, accusation, date or "
                f"amount from the earlier questions. If the issue type is unclear, set clarify to true.")
    else:
        rule = ("Keep any claim attributed to its original source (e.g. 'PumpCo says ...'); never state it as fact and "
                "never attribute it to a different party.")
    subject = (focus or {}).get("invoice_no") or (focus or {}).get("contractor")
    if subject and not new:
        rule += (f" The current subject of the conversation is {subject}: resolve 'it', 'that', 'this decision' and "
                 f"similar references to {subject} first.")
    rule += " Otherwise resolve references to the newest earlier question that names something, not an older one."
    prompt = ("An auditing assistant that cannot see the conversation will receive the analyst's new message. Rewrite "
              "it into one standalone question, naming the contractor, invoice, date or topic its references point to "
              "in the analyst's earlier questions. Use only words and facts from the analyst's own messages; add no "
              "facts, findings, dates or amounts. " + rule + "\n"
              'Return only JSON: {"question": "<standalone question>", "clarify": false}'
              "\n\nAnalyst's earlier questions (oldest first):\n" + "\n".join(f"- {e}" for e in earlier)
              + "\n\nNew message: " + question)
    try:
        r = q(f"SELECT TRIM(AI_COMPLETE('{REWRITE_MODEL}', ?, {{'temperature': 0}})::STRING) t", [prompt])[0]["T"]
        m = re.search(r"\{.*\}", r or "", re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    out = (d.get("question") or "").strip().strip('"')
    allowed = "\n".join(earlier + [question])
    if new:
        if d.get("clarify") is True or not out or adds_claim(out, question) or adds_facts(out, question):
            return clarify
        return out, "REWRITTEN"
    if out == question and not subject:
        return question, "VERBATIM"
    if not out or adds_facts(out, allowed):
        if subject:
            return with_focus(f"{question} (about {subject})", question, focus), "FALLBACK"
        return f'{question} (This follows my earlier question: "{earlier[-1]}")', "FALLBACK"
    return with_focus(out, question, focus), "REWRITTEN"


INVOICE = re.compile(r"\b[A-Z]{2,5}-INV-\d+\b")


def with_focus(out, typed, focus):
    """D29.5 code check on a rewrite: any invoice it names that is neither the focus nor typed by the analyst is
    replaced by the focus invoice; if it names none, the focus invoice is added; then the focus fact is appended."""
    inv = (focus or {}).get("invoice_no")
    if inv:
        keep = {inv} | set(INVOICE.findall(typed or ""))
        out = INVOICE.sub(lambda m: m.group(0) if m.group(0) in keep else inv, out)
        if inv not in out:
            out = f"{out.rstrip()} (about {inv})"
    fact = (focus or {}).get("fact")
    return f"{out.rstrip()} Context from the records: {fact}" if fact else out


# ---------------------------------------------------------------- step 12c: wording (D27)
def pattern_line(p):
    """Briefing banner for one repeat pattern: same words as SP_BRIEFING's fact ("Repeat pattern: <name>, <label>, ...")."""
    lbl = RULE_LABEL.get(p.get("rule_type")) or (p.get("rule_type") or "").lower().replace("_", " ")
    n = len(p.get("periods") or [])
    return f"Repeat pattern: {p.get('contractor_name') or p.get('contractor_code')}, {lbl}, upheld in {n} service periods"


def php(x):
    """Peso amount for chat text and suggestions: whole pesos without decimals (₱40,000), else 2 decimals."""
    v = float(x or 0)
    return f"₱{v:,.0f}" if abs(v - round(v)) < 0.005 else f"₱{v:,.2f}"


def as_date(d):
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def fmt_span(a, b):
    """'Aug 31 – Sep 6', 'Sep 7 – 13'."""
    a, b = as_date(a), as_date(b)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a:%b} {a.day} – {b.day}"
    return f"{a:%b} {a.day} – {b:%b} {b.day}"


def fmt_range(periods):
    """Dates covered by a set of weekly periods (Monday starts): first Monday to the last period's Sunday."""
    ps = sorted(as_date(p) for p in periods)
    return fmt_span(ps[0], ps[-1] + timedelta(days=6)) if ps else ""


def blocks(periods):
    """Contiguous runs of weekly periods."""
    out = []
    for p in sorted(as_date(x) for x in set(map(str, periods))):
        if out and (p - out[-1][-1]).days == 7:
            out[-1].append(p)
        else:
            out.append([p])
    return out


NUM = {1: "One", 2: "Two", 3: "Three"}
KIND = {"INVOICE": ("invoice", "invoices"), "TICKET": ("field ticket", "field tickets")}


def count_words(inv, tix):
    parts = []
    if inv:
        parts.append(f"{inv} {'invoice' if inv == 1 else 'invoices'}")
    if tix:
        parts.append(f"{tix} {'field ticket' if tix == 1 else 'field tickets'}")
    return " and ".join(parts) or "no invoices or tickets"


def doc_label(r):
    return r.get("doc_no") if r.get("doc_no") and re.fullmatch(r"[A-Z]{2,5}-[A-Z]*-?\d+", r.get("doc_no") or "") \
        else re.sub(r"\.pdf$", "", r.get("file_name") or "", flags=re.I)


def batch_summary(rows, cid):
    """Describe one dropped batch for contract cid from its APP.UPLOADS rows (dicts with lower-case keys).
    Returns {"text": [sentences], "runs": [(label, [periods])], "periods": [...], "others": {cid: n}}.
    D27.5: one contiguous range in which every period has field tickets -> one run. Otherwise the main part is the
    largest contiguous block of periods that have tickets (by files; by files alone if no period has tickets), the rest
    is stated by file (3 files or fewer) or by count, and both runs are offered. No file is judged stray."""
    mine = [r for r in rows if r.get("intake_status") == "FILED" and r.get("contract_id") == cid]
    per = {}
    for r in mine:
        d = per.setdefault(str(as_date(r["target_period"])), {"inv": 0, "tix": 0, "files": []})
        d["inv" if r.get("doc_kind") == "INVOICE" else "tix"] += 1
        d["files"].append(r)
    others = {}
    for r in rows:
        if r.get("intake_status") == "FILED" and r.get("contract_id") != cid:
            others[r["contract_id"]] = others.get(r["contract_id"], 0) + 1
    out = {"text": [], "runs": [], "periods": sorted(per), "others": others}
    if per:
        n_inv = sum(d["inv"] for d in per.values())
        n_tix = sum(d["tix"] for d in per.values())
        bl = blocks(per)
        if len(bl) == 1 and all(per[str(p)]["tix"] for p in bl[0]):
            rng = fmt_range(bl[0])
            out["text"].append(f"I sorted {len(mine)} files: **{count_words(n_inv, n_tix)}** for {cid}, covering **{rng}**.")
            out["runs"].append((f"Run the audit for {rng}", [str(p) for p in bl[0]]))
        else:
            with_tix = [b for b in blocks([p for p in per if per[p]["tix"]])] or bl
            main = max(with_tix, key=lambda b: (sum(len(per[str(p)]["files"]) for p in b), -b[0].toordinal()))
            main_s = [str(p) for p in main]
            out["text"].append(f"I sorted {len(mine)} files for {cid}. Most files cover **{fmt_range(main)}**: "
                               f"{count_words(sum(per[p]['inv'] for p in main_s), sum(per[p]['tix'] for p in main_s))}.")
            for p in sorted(set(per) - set(main_s)):
                fs = per[p]["files"]
                kinds = {f.get("doc_kind") for f in fs}
                if len(fs) <= 3:
                    noun = KIND.get(kinds.pop(), ("file", "files")) if len(kinds) == 1 else ("file", "files")
                    names = ", ".join(doc_label(f) for f in fs) if len(fs) < 3 else \
                        ", ".join(doc_label(f) for f in fs[:-1]) + " and " + doc_label(fs[-1])
                    if len(fs) == 2:
                        names = " and ".join(doc_label(f) for f in fs)
                    out["text"].append(f"{NUM[len(fs)]} {noun[0] if len(fs) == 1 else noun[1]}, {names}, "
                                       f"{'covers' if len(fs) == 1 else 'cover'} **{fmt_range([p])}**.")
                else:
                    out["text"].append(f"{count_words(per[p]['inv'], per[p]['tix']).capitalize()} cover **{fmt_range([p])}**.")
            out["runs"].append((f"Audit {fmt_range(main)} only", main_s))
            out["runs"].append((f"Audit {fmt_range(per)}", sorted(per)))
    for c, n in sorted(others.items()):
        out["text"].append(f"{n} {'file is' if n == 1 else 'files are'} for {c}; switch the Contract dropdown to audit them.")
    for r in rows:
        st_ = r.get("intake_status")
        name = r.get("file_name")
        it = r.get("intake") or {}
        if st_ == "DUPLICATE":
            where = f"under **{fmt_range([it['filed_period']])}**" if it.get("filed_period") else "already"
            out["text"].append(f"{name} has the same content as {it.get('duplicate_name')}, filed {where}. "
                               f"I ignored the new copy.")
        elif st_ == "ASK_TYPE":
            out["text"].append(f"I couldn't tell what **{name}** is.")
        elif st_ == "ASK_CONTRACT":
            m = (it.get("options") or {}).get("matched", 0)
            out["text"].append(f"**{name}** matches {'no contract' if not m else 'more than one contract'}. "
                               f"Which contract is it for?")
        elif st_ == "ASK_DATES":
            amb = f" (printed \"{it['ambiguous']}\", which could be read two ways)" if it.get("ambiguous") else ""
            out["text"].append(f"I couldn't read the dates on **{name}**{amb}. Which dates does it cover?")
        elif st_ == "ASK_SPAN":
            out["text"].append(f"**{name}** covers {fmt_span(r['printed_start'], r['printed_end'])}, across two "
                               f"service periods. Which one should it be audited in?")
        elif st_ == "ASK_REPLACE":
            rp = it.get("replaces") or {}
            out["text"].append(f"**{name}** has the same name as a file already filed"
                               + (f" under {fmt_range([rp['period']])}" if rp.get("period") else "")
                               + ", but different content. Should the new copy replace it?")
    return out


# ---------------------------------------------------------------- step 12c.4: run suggestions and timing (D27.11-12)
STAGE_WORD = {"EXTRACT": "while reading the documents", "CHECKS": "at the checks", "REVIEW": "at the review",
              "DRAFT": "while drafting the letters", "RUN": "before it started"}


def run_estimate(n):
    """Expected seconds for a staged range run of n periods (D27.12): extract all at once, checks and drafts one
    period at a time, review all at once, then the briefing. From the step 10/12 cold per-period times."""
    n = max(1, int(n or 1))
    return 45 + 18 * n + (52 if n == 1 else 70) + 15 * n + 5 * n


def fmt_periods(periods):
    """'Aug 31 – Sep 13' for one contiguous block; 'Aug 31 – Sep 6 and Sep 21 – 27' otherwise."""
    bl = blocks(periods)
    return " and ".join(fmt_range(b) for b in bl) if len(bl) <= 2 else \
        ", ".join(fmt_range(b) for b in bl[:-1]) + " and " + fmt_range(bl[-1])


def run_suggestions(filed, audited, stale=(), retry=(), cap=4):
    """D27.11: run suggestions from table state, never from the latest drop alone.
    filed: {period_start: {"inv": n, "tix": n}} over every FILED upload of the contract; audited: periods with a current
    (not stale) audit; stale: audited periods marked stale; retry: the periods the latest range run did not audit.
    Returns [(label, [periods])], at most `cap`, no two with the same period set."""
    stale = {str(p) for p in stale}
    need = sorted(({str(p) for p in filed} - {str(p) for p in audited}) | stale)
    out, seen = [], set()

    def add(label, ps):
        k = frozenset(ps)
        if ps and k not in seen and len(out) < cap:
            seen.add(k)
            out.append((label, sorted(ps)))
    retry = sorted(str(p) for p in retry if str(p) in need)
    if retry:
        add(f"Retry {fmt_periods(retry)}", retry)
    bl = [[str(p) for p in b] for b in blocks(need)]
    for b in bl:
        verb = "Re-run the audit" if all(p in stale for p in b) else "Run the audit"
        add(f"{verb} for {fmt_range(b)}", b)
    for b in bl:
        tix = [p for p in b if (filed.get(p) or {"tix": 1}).get("tix")]
        if tix and len(tix) < len(b):
            for tb in blocks(tix):
                add(f"Audit {fmt_range(tb)} only (the dates with field tickets)", [str(p) for p in tb])
    if len(bl) > 1:
        add(f"Run the audit for every period that needs it ({fmt_periods(need)})", need)
    return out


# ---------------------------------------------------------------- step 12c: suggestions (D27.7)
def S(label, act, arg=None, amt=None, money=False):
    return {"label": label, "act": act, "arg": arg, "amt": amt, "money": money}


def ask_suggestions(a, periods_hint):
    """Choices for one intake question (a: UPLOADS row with lower-case keys)."""
    name, it, st_ = a["file_name"], a.get("intake") or {}, a["intake_status"]
    out = []
    if st_ == "ASK_TYPE":
        out += [S(f"{name} is an invoice", "set_type", [a["file_path"], "INVOICE"]),
                S(f"{name} is a field ticket", "set_type", [a["file_path"], "TICKET"])]
    elif st_ == "ASK_CONTRACT":
        out += [S(f"File {name} under {c}", "set_contract", [a["file_path"], c])
                for c in (it.get("options") or {}).get("contracts") or []][:3]
    elif st_ in ("ASK_SPAN", "ASK_DATES"):
        ps = (it.get("options") or {}).get("periods") or periods_hint
        out += [S(f"File {name} under {fmt_range([p])}", "file_under", [a["file_path"], str(p)]) for p in ps][:3]
    elif st_ == "ASK_REPLACE":
        out += [S(f"Replace the filed {name} with the new copy", "replace", a["file_path"]),
                S(f"Keep the {name} already filed", "keep_existing", a["file_path"])]
    if st_ != "ASK_REPLACE":
        out.append(S(f"Leave {name} out", "leave_out", a["file_path"]))
    return out


def next_steps(st):
    """'What I can do next', from table state only (dict built by review.py; see review.next_state). Money suggestions
    carry the invoice version they were computed for; review.py re-checks it before acting."""
    out = []
    done = st.get("done") or set()
    if st.get("busy"):
        return [S("Stop", "stop", st["stoppable"])] if st.get("stoppable") else []
    for d in st.get("stopped") or []:                  # D27.15: a drop cut off by a closed tab
        out.append(S(f"Audit what arrived ({d['got']} file{'s' if d['got'] != 1 else ''})", "audit_partial", d["batch_id"]))
    for a in st.get("asks") or []:
        out += ask_suggestions(a, st.get("ask_periods") or [])
    if out:
        return out[:8]
    for c in st.get("confirm") or []:
        out += [S(f"Review {c['contract_id']}'s values", "review_values", c["contract_id"]),
                S(f"Confirm {c['contract_id']} as extracted (I've seen the {c['warnings']} rate warning"
                  f"{'s' if c['warnings'] != 1 else ''})", "confirm_contract", c["contract_id"])]
    # contract offers (D27.4) lead only until a briefing exists; after it, the bulk action leads (D27.14)
    later = [S(f"Review {c}'s values", "review_values", c) for c in st.get("review_offer") or []
             if ("review_values", c) not in done]
    later += [S(f"Open {c}", "open_contract", c) for c in st.get("open_contract") or []]
    if not st.get("invoices"):
        out += later
        later = []
    for aid in st.get("actions") or []:
        out += [S(aid["label"], "action_yes", aid["action_id"], amt=f"pay {php(aid['pay'])} + VAT", money=True),
                S("Don't do that", "action_no", aid["action_id"])]
    for rv in st.get("revisions") or []:
        out += [S(f"Accept the revised decision on {rv['invoice_no']} ({rv['decision']})", "revision_yes",
                  rv["revision_id"], amt=f"upheld {php(rv['upheld'])}", money=True),
                S(f"Keep the current decision on {rv['invoice_no']}", "revision_no", rv["revision_id"])]
    if st.get("briefing_missing"):
        out.append(S("Write the briefing", "write_briefing", st["briefing_missing"]))
    if st.get("runs"):
        out += [S(lbl, "run", ps) for lbl, ps in st["runs"]]
        if st.get("sorted_batch") and ("sorted", st["sorted_batch"]) not in done:
            out.append(S("Show how you sorted them", "sorted", st["sorted_batch"]))
    invs = st.get("invoices") or []
    if not invs:
        return (out + later)[:8]
    last = st.get("last") or {}

    def approve_letter(r):
        bits = []
        if r["disputed"]:
            bits.append(f"dispute {php(r['disputed'])}")
        if r["held"]:
            bits.append(f"hold {php(r['held'])}")
        return S(f"Approve {r['invoice_no']} and save the PDF", "approve", [r["invoice_no"], r["version"]],
                 amt=", ".join(bits + [f"pay {php(r['pay'])} + VAT"]), money=True)

    def notice(r):
        return "hold notice" if r["kind"] == "hold" else "dispute letter"

    by_no = {r["invoice_no"]: r for r in invs}
    ctx = by_no.get(last.get("invoice_no"))
    drafts = [r for r in invs if r["status"] == "DRAFT"]
    issues = [r for r in drafts if r["findings"]]
    # D27.14: one bulk action first, spelled out in full; then one by one
    if drafts and last.get("kind") != "ONE":
        every = len(drafts) == len(invs)
        out.append(S(f"Approve {'all' if every else 'the remaining ' + str(len(drafts))} as recommended: "
                     + bulk_label(drafts), "approve_all", [[r["invoice_no"], r["version"]] for r in drafts], money=True))
        if issues:
            out.append(S(f"Go through the {len(issues)} invoice{'s' if len(issues) != 1 else ''} with issues one by one",
                         "one_by_one"))
    if ctx and last.get("kind") == "ONE" and ctx["status"] == "DRAFT":
        out.append(S("Approve this one", "approve_next", [ctx["invoice_no"], ctx["version"]],
                     amt=approve_letter(ctx)["amt"], money=True))
        out.append(S("Change it…", "change_one", ctx["invoice_no"]))
        if ("explain", ctx["invoice_no"]) not in done:
            out.append(S("Explain", "explain", ctx["invoice_no"]))
        rest = [r for r in drafts if r is not ctx]
        if rest:
            out.append(S("Approve all the rest as recommended: " + bulk_label(rest), "approve_all",
                         [[r["invoice_no"], r["version"]] for r in rest], money=True))
        return out + later
    if ctx and ctx["status"] == "DRAFT" and last.get("kind") == "LETTER" and ctx["kind"] not in ("clean", "cleared"):
        if not ctx.get("firmer"):
            out.append(S("Make it firmer", "firmer", ctx["invoice_no"]))
        out.append(approve_letter(ctx))
    for p in st.get("patterns") or []:
        qn = pattern_prompt(p)
        if ("ask", qn) not in done:
            out.append(S(qn, "ask", qn))
    for v in st.get("verify") or []:
        qn = f"What should I verify on {v['doc_no']}?"
        if ("ask", qn) not in done:
            out.append(S(qn, "ask", qn))
    if any(r["findings"] for r in invs):
        out.append(S("Change a decision…", "change", st.get("range_periods")))
    if all(r["status"] == "APPROVED" for r in invs):
        out.append(S("Open the dashboard", "tab", "Dashboard"))
    out += later
    # keep "Change a decision…" even when the list is long
    if len(out) > 8:
        keep = [o for o in out if o["act"] == "change"]
        out = [o for o in out if o["act"] != "change"][:8 - len(keep)] + keep
    return out


# ---------------------------------------------------------------- step 12c polish: plain voice (D27.13)
# Every amount below is a V_RESOLUTIONS / V_DECISIONS column (or a sum of them); every other slot is a stored fact
# from APP.V_ISSUE_FACTS. Technical detail (rule labels, decision codes, timings) goes to details(), never the text.
def range_data(q, cid, periods):
    """(resolutions by invoice, current decisions by invoice, facts by exception_id) for the range's periods."""
    ps = json.dumps(sorted(str(p) for p in periods))
    res = {r["INVOICE_NO"]: r for r in _q(q, """
        SELECT r.*, h.contractor_code, k.contractor_name
        FROM {{DB}}.AUDIT.V_RESOLUTIONS r
        LEFT JOIN {{DB}}.CURATED.INVOICE_HEADERS h ON h.contract_id = r.contract_id AND h.invoice_no = r.invoice_no
        LEFT JOIN {{DB}}.CONTRACT.CONTRACTORS k ON k.contract_id = r.contract_id AND k.contractor_code = h.contractor_code
        WHERE r.contract_id = ? AND ARRAY_CONTAINS(r.period_start::STRING::VARIANT, PARSE_JSON(?))
        ORDER BY r.period_start, r.invoice_no""", [cid, ps])}
    decs = {}
    for r in _q(q, """SELECT e.exception_id, e.invoice_no, e.rule_type, e.summary, e.flagged_php, e.period_start, d.version,
                         d.decision, d.upheld_php, d.clauses::STRING clauses, d.decided_by, d.override_reason
                  FROM {{DB}}.AUDIT.EXCEPTIONS e
                  JOIN {{DB}}.AUDIT.V_DECISIONS d ON d.exception_id = e.exception_id AND d.is_current
                  WHERE e.contract_id = ? AND ARRAY_CONTAINS(e.period_start::STRING::VARIANT, PARSE_JSON(?))
                  ORDER BY e.period_start, e.invoice_no, e.line_no""", [cid, ps]):
        decs.setdefault(r["INVOICE_NO"], []).append(r)
    facts = {r["EXCEPTION_ID"]: r for r in _q(q, """SELECT f.* FROM {{DB}}.APP.V_ISSUE_FACTS f
        JOIN {{DB}}.AUDIT.EXCEPTIONS e ON e.exception_id = f.exception_id
        WHERE e.contract_id = ? AND ARRAY_CONTAINS(e.period_start::STRING::VARIANT, PARSE_JSON(?))""", [cid, ps])}
    return res, decs, facts


def _arr(v):
    v = json.loads(v) if isinstance(v, str) else v
    return [x for x in (v or []) if x not in (None, "")]


def and_list(xs):
    xs = list(dict.fromkeys(xs))
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def fmt_days(ds):
    """'Sep 10 and 11', 'Sep 3, 10 and 11', 'Sep 16 – 20' (a run of 3 or more days), 'Aug 31 and Sep 1'."""
    ds = sorted({as_date(d) for d in ds})
    if not ds:
        return ""
    if len(ds) >= 3 and (ds[-1] - ds[0]).days == len(ds) - 1:
        return fmt_span(ds[0], ds[-1])
    out, mon = [], None
    for d in ds:
        out.append(f"{d:%b} {d.day}" if d.month != mon else str(d.day))
        mon = d.month
    return and_list(out)


def share_words(pct):
    """0.5 -> 'half the rate' (the contract's standby share, from CONTRACT.RULE_PARAMS)."""
    if pct is None:
        return None
    p = float(pct)
    named = {0.5: "half the rate", 0.25: "a quarter of the rate", 0.75: "three quarters of the rate"}
    return named.get(round(p, 4), f"{p * 100:g}% of the rate")


def weather_words(w):
    """'Typhoon signal no. 2' -> 'typhoon'; 'Heavy rain' -> 'heavy rain'."""
    return re.sub(r"\s+signal\b.*$", "", w.strip(), flags=re.I).lower() if w else None


def fill(template, slots):
    """Fill {slot}s; drop any [ ... ] group whose slots have no fact; a bare {slot} with no fact drops the sentence's
    clause but never invents a value."""
    def opt(m):
        names = re.findall(r"{(\w+)}", m.group(1))
        return m.group(1).format(**slots) if all(slots.get(n) for n in names) else ""
    t = re.sub(r"\[([^\]]*)\]", opt, template)
    t = re.sub(r"{(\w+)}", lambda m: str(slots.get(m.group(1)) or ""), t)
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s{2,}", " ", t)).strip()


def issue_groups(res, decs):
    """Findings grouped by contractor, then by (check, decision), in order of the contractor's upheld amount."""
    by = {}
    for inv, ds in decs.items():
        r = res.get(inv)
        if r is None:
            continue
        who = short_name(r["CONTRACTOR_NAME"]) or r["CONTRACTOR_CODE"] or inv
        for d in ds:
            by.setdefault(who, {}).setdefault((d["RULE_TYPE"], d["DECISION"]), []).append(d)
    return sorted(by.items(), key=lambda kv: -sum(float(d["UPHELD_PHP"] or 0) for g in kv[1].values() for d in g))


def plain_sentence(who, rule, decision, ds, facts):
    fs = [plain(facts.get(plain(d, "decision")["EXCEPTION_ID"]) or {}, "fact") for d in ds]
    cat = lambda k: [x for f in fs for x in _arr(f.get(k))]
    one = lambda k: next((f.get(k) for f in fs if f.get(k) is not None), None)
    upheld = sum(float(d["UPHELD_PHP"] or 0) for d in ds)
    slots = {"who": who, "upheld": php(upheld), "flagged": php(sum(float(d["FLAGGED_PHP"] or 0) for d in ds)),
             "equipment": and_list(cat("EQUIPMENT")), "dates": fmt_days(cat("DATES")),
             "weather": and_list([w for w in (weather_words(f.get("WEATHER")) for f in fs) if w]),
             "standby": share_words(one("STANDBY_PCT")) if rule == "SITE_EQUIPMENT_DAYS" else None,
             "tickets": and_list(cat("TICKETS")), "earlier": and_list(cat("EARLIER")),
             "term_end": fmt_days([one("TERM_END")]) if one("TERM_END") else None,
             "charge": and_list(cat("CHARGE")), "role": and_list([x.lower() for x in cat("ROLE")]),
             "extra_hours": f"{one('EXTRA_HOURS')} hours" if one("EXTRA_HOURS") else None}
    for k in ("billed_rate", "contract_rate", "printed", "expected", "unit_rate"):
        slots[k] = php(one(k.upper())) if one(k.upper()) is not None else None
    slots["qty"] = one("QTY")
    if len(ds) > 1:                                    # per-item facts only hold for one finding
        for k in ("billed_rate", "contract_rate", "printed", "expected", "unit_rate", "qty"):
            slots[k] = None
    t = PLAIN.get((rule, decision)) or PLAIN.get((rule, "*")) or "{who}: " + (RULE_LABEL.get(rule) or rule) + ". That's {upheld}."
    return fill(t, slots)


def briefing(res, decs, facts, periods, patterns=(), verify=()):
    """D27.13 briefing: {"opening", "lines": [(text, invoices, status words)], "notes"}. One line per contractor
    (contractor first, largest amount first), so the text stays about 6 lines; the detail is in Details."""
    for r in res.values():
        plain(r, "resolution")
    n = len(res)
    pay = sum(float(r["PAY_PHP"] or 0) for r in res.values())
    push = sum(float(r["DISPUTED_PHP"] or 0) + float(r["HELD_PHP"] or 0) for r in res.values())
    n_push = sum(1 for r in res.values() if float(r["DISPUTED_PHP"] or 0) + float(r["HELD_PHP"] or 0) > 0)
    opening = f"I checked {n} invoice{'s' if n != 1 else ''} for {fmt_range(periods)}. "
    opening += (f"I'd pay {php(pay)} before VAT and push back on {php(push)} across {n_push} "
                f"invoice{'s' if n_push != 1 else ''}." if push else f"They all look right: I'd pay {php(pay)} before VAT.")
    rep = {(p.get("contractor_code"), p.get("rule_type")): len(p.get("periods") or []) for p in patterns or []}
    lines = []
    for who, groups in issue_groups(res, decs):
        texts, invs, words = [], [], []
        for (rule, decision), ds in sorted(groups.items(), key=lambda kv: kv[0][1] == "DISMISS"):
            t = plain_sentence(who if not texts else "They", rule, decision, ds, facts)
            code = res[ds[0]["INVOICE_NO"]]["CONTRACTOR_CODE"]
            if rep.get((code, rule), 0) > 1:
                t += f" It happened in {rep[(code, rule)]} different weeks."
            texts.append(t + (f" {STATUS_WORD.get('DISMISS', '').capitalize()}." if decision == "DISMISS" else ""))
            invs += [d["INVOICE_NO"] for d in ds]
            words.append(STATUS_WORD.get(decision, decision.lower()))
        lines.append((" ".join(texts), sorted(set(invs)), and_list(words)))
    notes = []
    for v in verify or []:
        t = PLAIN.get((v.get("issue_type"), "VERIFY")) or PLAIN.get(("*", "VERIFY")) or "Please look at {doc} yourself."
        notes.append(fill(t, {"doc": v.get("doc_no")}))
    return {"opening": opening, "lines": lines, "notes": list(dict.fromkeys(notes))}


def briefing_markdown(b):
    out = [b["opening"], ""]
    out += [f"- {t} *({', '.join(i)}: {w})*" for t, i, w in b["lines"]]
    out += [f"- {x}" for x in b["notes"]]
    return "\n".join(out)


def bulk_label(drafts):
    """'Approve all as recommended: pay ₱X, dispute ₱Y (N letters), hold ₱Z (M notices)' from V_RESOLUTIONS rows."""
    pay = sum(r["pay"] for r in drafts)
    disp, held = sum(r["disputed"] for r in drafts), sum(r["held"] for r in drafts)
    n_l = sum(1 for r in drafts if r["kind"] in ("dispute", "dispute and hold"))
    n_n = sum(1 for r in drafts if r["kind"] == "hold")
    bits = [f"pay {php(pay)}"]
    if disp:
        bits.append(f"dispute {php(disp)} ({n_l} letter{'s' if n_l != 1 else ''})")
    if held:
        bits.append(f"hold {php(held)} ({n_n} notice{'s' if n_n != 1 else ''})")
    return ", ".join(bits)


def one_by_one_text(r, ds, facts):
    """One problem invoice in plain words (the one-by-one view)."""
    who = r["ctr"]
    groups = {}
    for d in ds:
        groups.setdefault((d["RULE_TYPE"], d["DECISION"]), []).append(d)
    parts = [plain_sentence(who, rule, dec, g, facts) for (rule, dec), g in groups.items()]
    words = and_list([STATUS_WORD.get(dec, dec.lower()) for (_, dec) in groups])
    return f"**{r['invoice_no']}** ({who}, {words}). " + " ".join(parts) + f" I'd pay {php(r['pay'])} before VAT."


# ---------------------------------------------------------------- threads and welcome back (step 12d, D28)
def thread_title(m):
    """Title from a thread's first meaningful message: a run's dates, an upload, or the first question. None if m isn't one."""
    p = plain(m.get("p") or {}, "message payload")
    if m.get("type") == "RUN" and p.get("periods"):
        return f"Audit {fmt_range(p['periods'])}"
    if m.get("role") == "USER" and not p.get("act"):
        fs = p.get("files") or []
        if fs:
            return f"Uploaded {fs[0]}" if len(fs) == 1 else f"Uploaded {len(fs)} files"
        t = " ".join((p.get("text") or "").split())
        if t:
            return t if len(t) <= 48 else t[:47].rsplit(" ", 1)[0] + "…"
    return None


def _n(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def welcome_text(groups, waiting, since, viewer, contract_id):
    """D28 welcome back, in plain voice. groups: V_ACTIVITY grouped by (who, kind) with N, AMT (SQL sum) and SUBJECTS;
    waiting: one V_RESOLUTIONS row (N, PAY, DISPUTED, HELD) for DRAFT invoices. Amounts are only those SQL values."""
    me = (viewer or "").lower()
    by = {}
    for g in groups:
        plain(g, "activity group")
        who = (g["WHO"] or "someone")
        key = "you" if who.lower() == me else ("I" if who == "MERRY" else who)
        by.setdefault(key, []).append(g)
    phrases = []
    for who, gs in by.items():
        acts = []
        for g in gs:
            k, n = g["KIND"], int(g["N"] or 0)
            if k == "UPLOAD":
                acts.append("uploaded files" + (f" ({n} drops)" if n > 1 else ""))
            elif k == "CONTRACT_CONFIRMED":
                acts.append("confirmed the contract")
            elif k == "AGENT_DECISION":
                acts.append(f"reviewed {_n(n, 'flag')} on {_n(int(g['SUBJ'] or 0), 'invoice')}")
            elif k == "OVERRIDE":
                acts.append(f"changed {'one' if n == 1 else n} of my decisions ({g['SUBJECTS']})")
            elif k == "APPROVAL":
                acts.append(f"approved {_n(int(g['SUBJ'] or 0), 'invoice')} ({php(g['AMT'])} to pay before VAT)")
            elif k == "CONTRACT_EDIT":
                acts.append(f"changed {_n(n, 'contract value')}")
            elif k == "CHAT_ACTION":
                acts.append(f"confirmed {_n(n, 'chat action')}")
        if acts:
            phrases.append(f"{who} {and_list(acts)}")
    when = f"Since your last visit on {as_date(since):%b} {as_date(since).day}, " if since else "So far, "
    out = [when + "; ".join(phrases) + "." if phrases else f"Nothing has changed on {contract_id} since your last visit."]
    w = plain(waiting or {}, "waiting")
    n = int(w.get("N") or 0)
    if n:
        bits = [f"{php(w['PAY'])} to pay before VAT"]
        if float(w.get("DISPUTED") or 0):
            bits.append(f"{php(w['DISPUTED'])} to push back on")
        if float(w.get("HELD") or 0):
            bits.append(f"{php(w['HELD'])} on hold")
        out.append(f"Waiting for you: {_n(n, 'invoice')} to approve, {and_list(bits)}.")
    else:
        out.append("Nothing is waiting for your approval.")
    return " ".join(out)
