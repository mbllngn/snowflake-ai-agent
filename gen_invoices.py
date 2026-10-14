"""Generate contractor invoices and field tickets from story_bible.yaml.
Builds CLEAN invoices from actual activity, then applies the planted errors.
Also writes ground-truth CSVs, the answer key, and blank forms for the
tickets to be filled in by hand."""
import csv, os, random, subprocess, glob
import yaml
import numpy as np
from datetime import date, timedelta
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfgen import canvas

random.seed(7)
B = yaml.safe_load(open("story_bible.yaml"))
K, ACT, WEEKS = B["contractors"], B["activity"], B["meta"]["weeks"]
OP, REP = B["operator"], B["operator"]["company_rep"]
ERR = {e["id"]: e for e in B["errors"] if e["enabled"]}
OUT = "out"
for sub in ["invoices", "tickets", "handwritten_forms", "_tmp"]:
    os.makedirs(f"{OUT}/{sub}", exist_ok=True)

D = lambda x: x if isinstance(x, date) else date.fromisoformat(str(x))
def rng(a, b):
    a, b = D(a), D(b)
    return [a + timedelta(i) for i in range((b - a).days + 1)]
RATE = {r["code"]: r["rate"] for k in K.values() for r in k["rates"]}
VAT = 0.12

# Per-contractor document style, so extraction sees varied layouts
STYLE = {
    "PCS": dict(color="#1F3A5F", font="Helvetica", datefmt="%d-%b-%Y", qty="Qty", amt="Amount", scan=False, inv_start=1041),
    "LCR": dict(color="#8B1E1E", font="Helvetica", datefmt="%m/%d/%Y", qty="Quantity", amt="Line Total", scan=False, inv_start=2207),
    "AQH": dict(color="#2E6B3A", font="Courier", datefmt="%d/%m/%Y", qty="Loads", amt="Amount (PHP)", scan=True, inv_start=318),
    "KCS": dict(color="#B35C00", font="Helvetica", datefmt="%B %d, %Y", qty="Head-days", amt="Amount", scan=True, inv_start=77),
    "TLW": dict(color="#4B2C6F", font="Times-Roman", datefmt="%d %b %Y", qty="Qty", amt="Ext. Amount", scan=False, inv_start=5512),
}
HANDWRITTEN = {("LCR", D("2026-09-15")), ("AQH", D("2026-09-14")), ("PCS", D("2026-09-15"))}

# =========================================================== FIELD TICKETS
# ticket = dict(no, code, date, people[(name, role, hrs)], equip[(id, desc, qty, unit)], qty_lines, remarks, signed)
tickets, tseq = [], {c: s for c, s in zip(K, [7101, 3301, 409, 51, 880])}
def new_ticket(code, d, **kw):
    t = dict(no=f"{code}-T-{tseq[code]:04d}", code=code, date=d, people=[], equip=[], qty_lines=[],
             remarks="", signed=True, handwritten=(code, d) in HANDWRITTEN)
    t.update(kw); tseq[code] += 1; tickets.append(t); return t

def week_of(d):
    for w, r in WEEKS.items():
        if D(r["start"]) <= d <= D(r["end"]): return w

# =========================================================== CLEAN INVOICES
invoices = []  # dict(code, week, lines[...]); line = dict(date, ticket, desc, rcode, qty, unit, rate, amount, tag)
def L(desc, rcode, qty, unit, rate=None, d=None, ticket="", tag=""):
    rate = RATE[rcode] if rate is None else rate
    return dict(date=d, ticket=ticket, desc=desc, rcode=rcode, qty=qty, unit=unit, rate=rate, amount=qty * rate, tag=tag)

for w in WEEKS:
    # ---- PumpCo: aggregated by role, ticket per day
    a = ACT["PCS"].get(w)
    if a:
        ops = [D(x) for x in a["operating"]]; sb = [D(x) for x in a.get("standby_on_location", [])]
        tks = []
        for d in sorted(ops + sb):
            if d in ops:
                t = new_ticket("PCS", d, people=[(c["name"], c["role"], 12) for c in K["PCS"]["crew"]],
                               equip=[("PU-101", "Pump unit", 1, "day")],
                               qty_lines=[("Proppant handled", round(a["proppant_tonnes"] / len(ops)), "t")])
            else:  # standby day: pump on location, no crew
                t = new_ticket("PCS", d, equip=[("PU-101", "Pump unit on location", 1, "day")],
                               remarks="Weather. Crew released.")
            tks.append(t["no"])
        rng_t = f"{tks[0]} to {tks[-1]}"
        lines = [L("Pump unit PU-101", "PCS-EQ-PU", len(ops), "day", ticket=rng_t, tag="pump")]
        if sb:
            lines.append(L("Pump unit PU-101 standby (Sec. 4.2)", "PCS-EQ-PU", len(sb), "day", rate=RATE["PCS-EQ-PU"] // 2, ticket=rng_t, tag="pump_sb"))
        n = len(ops)
        lines += [L("Supervisor", "PCS-SUP", n, "day", ticket=rng_t),
                  L("Pump Operator (x2)", "PCS-OPR", 2 * n, "day", ticket=rng_t),
                  L("Helper (x2)", "PCS-HLP", 2 * n, "day", ticket=rng_t),
                  L("Proppant handling", "PCS-PROP", a["proppant_tonnes"], "tonne", ticket=rng_t)]
        invoices.append(dict(code="PCS", week=w, lines=lines))

    # ---- Liftmax: per worker, per day
    a = ACT["LCR"].get(w)
    if a:
        lines = []
        for d in map(D, a["operating"]):
            t = new_ticket("LCR", d, people=[(c["name"], c["role"], 12) for c in K["LCR"]["crew"]],
                           equip=[("CR-301", "50-ton crane", 1, "day")])
            lines += [L("50-ton crane CR-301", "LCR-EQ-CR", 1, "day", d=d, ticket=t["no"]),
                      L("Crane Operator - N. Villanueva", "LCR-COP", 1, "day", d=d, ticket=t["no"]),
                      L("Rigger - P. Cruz", "LCR-RIG", 1, "day", d=d, ticket=t["no"], tag="rigger")]
        invoices.append(dict(code="LCR", week=w, lines=lines))

    # ---- AquaHaul: one ticket per day, one line per ticket
    a = ACT["AQH"].get(w)
    if a:
        lines = []
        for d, n in a["loads"].items():
            d = D(d)
            drivers = K["AQH"]["crew"] if n > 3 else K["AQH"]["crew"][:1]
            t = new_ticket("AQH", d, people=[(c["name"], c["role"], 10) for c in drivers],
                           qty_lines=[("Water hauled, 20 m3 loads", n, "loads")])
            lines.append(L("Water haul, 20 m3", "AQH-LOAD", n, "load", d=d, ticket=t["no"]))
        invoices.append(dict(code="AQH", week=w, lines=lines))

    # ---- Kusina: one weekly headcount sheet, one line per day
    a = ACT["KCS"].get(w)
    if a:
        days = rng(WEEKS[w]["start"], WEEKS[w]["end"])
        if "E6" not in ERR:
            days = [d for d in days if d <= D(K["KCS"]["term"]["end"])]
        t = new_ticket("KCS", days[0], qty_lines=[(f"Headcount {d.strftime('%a %d %b')}", a["headcount"], "heads") for d in days],
                       remarks=f"Weekly camp headcount sheet, {days[0]:%d %b} to {days[-1]:%d %b}")
        lines = [L("Meals & accommodation", "KCS-HD", a["headcount"], "head-day", d=d, ticket=t["no"]) for d in days]
        invoices.append(dict(code="KCS", week=w, lines=lines))

    # ---- TruLine: per job day: runs, engineer hrs, operator hrs
    a = ACT["TLW"].get(w)
    if a:
        lines = []
        for d, runs in a["runs"].items():
            d = D(d); hrs = a["hours_on_site"] + 2 * a["travel_hours_each_way"]
            t = new_ticket("TLW", d, people=[(c["name"], c["role"], hrs) for c in K["TLW"]["crew"]],
                           equip=[("WL-501", "Wireline unit", runs, "runs")],
                           remarks="Incl. travel from Sto. Tomas Yard" if a["travel_hours_each_way"] else "")
            lines += [L("Wireline run", "TLW-RUN", runs, "run", d=d, ticket=t["no"], tag="runs"),
                      L("Wireline Engineer - C. Domingo", "TLW-ENG", hrs, "hour", d=d, ticket=t["no"]),
                      L("Wireline Operators - A. Gomez, J. Navarro", "TLW-OPR", 2 * hrs, "hour", d=d, ticket=t["no"])]
        invoices.append(dict(code="TLW", week=w, lines=lines))

# =========================================================== APPLY ERRORS
def inv(code, week):
    return next(i for i in invoices if i["code"] == code and i["week"] == week)
def tag_line(i, tag):
    return next(l for l in i["lines"] if l["tag"] == tag)

for eid in ("E1a", "E1b"):           # standby billed at full rate
    if eid in ERR:
        e = ERR[eid]; i = inv("PCS", e["week"])
        sb = tag_line(i, "pump_sb"); i["lines"].remove(sb)
        p = tag_line(i, "pump"); p["qty"] = e["mutation"]["billed_qty"]; p["amount"] = p["qty"] * p["rate"]
        p["err"] = eid
if "E2" in ERR:                      # rigger billed as crane operator
    for l in inv("LCR", "W2")["lines"]:
        if l["tag"] == "rigger":
            l.update(desc="Crane Operator - P. Cruz", rcode="LCR-COP", rate=9500, amount=9500, err="E2")
if "E3" in ERR:                      # W2 ticket resubmitted in W3
    src = next(l for l in inv("AQH", "W2")["lines"] if l["date"] == D("2026-09-07"))
    dup = dict(src); dup["err"] = "E3"
    inv("AQH", "W3")["lines"].insert(0, dup)
if "E4" in ERR:                      # wrong line extension
    l = tag_line(inv("TLW", "W3"), "runs"); l["amount"] = ERR["E4"]["mutation"]["line_total"]; l["err"] = "E4"
if "E5" in ERR:                      # unsigned ticket
    next(t for t in tickets if t["code"] == "LCR" and t["date"] == D("2026-09-15"))["signed"] = False
if "E6" in ERR:
    for l in inv("KCS", "W3")["lines"]:
        if l["date"] > D(K["KCS"]["term"]["end"]): l["err"] = "E6"
if "E7" in ERR:                      # excluded fuel surcharge
    i = inv("TLW", "W1"); m = ERR["E7"]["mutation"]["add_line"]
    i["lines"].append(dict(date=None, ticket="", desc=m["item"], rcode="", qty=m["qty"], unit=m["unit"],
                           rate=m["rate"], amount=m["qty"] * m["rate"], tag="", err="E7"))
if "E8" in ERR:
    for l in inv("TLW", "W2")["lines"]:
        if l["rcode"] in ("TLW-ENG", "TLW-OPR"): l["err"] = "E8"

# invoice header data
seq = {c: STYLE[c]["inv_start"] for c in K}
for i in sorted(invoices, key=lambda x: (x["code"], x["week"])):
    c = i["code"]
    i["no"] = f"{c}-INV-{seq[c]}"; seq[c] += 1
    i["date"] = D(WEEKS[i["week"]]["end"]) + timedelta(1)
    i["period"] = (D(WEEKS[i["week"]]["start"]), D(WEEKS[i["week"]]["end"]))
    i["subtotal"] = sum(l["amount"] for l in i["lines"])
    i["vat"] = round(i["subtotal"] * VAT, 2)
    i["total"] = i["subtotal"] + i["vat"]

# =========================================================== RENDER INVOICES
def money(x): return f"{x:,.2f}"

def render_invoice(i, path):
    k, s = K[i["code"]], STYLE[i["code"]]
    f, fb = s["font"], {"Helvetica": "Helvetica-Bold", "Courier": "Courier-Bold", "Times-Roman": "Times-Bold"}[s["font"]]
    col = colors.HexColor(s["color"])
    P = lambda t, sz=9, b=False, al=0: Paragraph(t, ParagraphStyle("x", fontName=fb if b else f, fontSize=sz, leading=sz + 3, alignment=al))
    S = []
    head = Table([[P(f"<font color='{s['color']}'>{k['name'].upper()}</font>", 15, True),
                   P("INVOICE", 18, True, 2)],
                  [P(f"{k['base']}, Batangas<br/>TIN 0{random.randint(10,99)}-{random.randint(100,999)}-{random.randint(100,999)}-000"),
                   P(f"<b>Invoice No.:</b> {i['no']}<br/><b>Invoice Date:</b> {i['date'].strftime(s['datefmt'])}<br/>"
                     f"<b>Service Period:</b> {i['period'][0].strftime(s['datefmt'])} to {i['period'][1].strftime(s['datefmt'])}<br/>"
                     f"<b>Contract Ref.:</b> {B['contract']['title'].split('No. ')[1]}", 9, False, 2)]],
                 colWidths=[100*mm, 70*mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.5, col)]))
    S += [head, Spacer(1, 8),
          P(f"<b>BILL TO:</b><br/>{OP['name']}<br/>{OP['site']}<br/>Attn: {REP['name']}, {REP['title']}"), Spacer(1, 10)]
    rows = [["Date", "Ticket No.", "Description", "Code", s["qty"], "Unit", "Rate", s["amt"]]]
    for l in i["lines"]:
        rows.append([l["date"].strftime("%d %b") if l["date"] else "", P(l["ticket"], 7), P(l["desc"], 8), l["rcode"],
                     f"{l['qty']:g}", l["unit"], money(l["rate"]), money(l["amount"])])
    t = Table(rows, colWidths=[13*mm, 27*mm, 45*mm, 21*mm, 13*mm, 15*mm, 17*mm, 22*mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), fb), ("FONTNAME", (0, 1), (-1, -1), f),
                           ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("BACKGROUND", (0, 0), (-1, 0), col), ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.grey)]))
    S += [t, Spacer(1, 6)]
    tot = Table([["Subtotal", money(i["subtotal"])], ["VAT (12%)", money(i["vat"])], ["TOTAL DUE (PHP)", money(i["total"])]],
                colWidths=[40*mm, 30*mm], hAlign="RIGHT")
    tot.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), f), ("FONTNAME", (0, 2), (-1, 2), fb),
                             ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                             ("LINEABOVE", (0, 2), (-1, 2), 1, col)]))
    S += [tot, Spacer(1, 14), P("Payment terms: 30 days from receipt (MSA Sec. 6.3). Field tickets attached per Sec. 6.2.", 8),
          P(f"Please remit to {k['name'].rstrip('.')}. Thank you for your business.", 8)]
    SimpleDocTemplate(path, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm,
                      title=i["no"]).build(S)

# =========================================================== RENDER TICKETS
def signature(c, x, y):
    c.saveState(); c.setStrokeColor(colors.HexColor("#1A2A6C")); c.setLineWidth(1.1)
    p = c.beginPath(); p.moveTo(x, y)
    for _ in range(5):
        x2 = x + random.uniform(8, 16)
        p.curveTo(x + 3, y + random.uniform(6, 14), x2 - 3, y - random.uniform(4, 10), x2, y + random.uniform(-2, 3)); x = x2
    c.drawPath(p); c.line(x - 60, y - 6, x + 5, y - 3); c.restoreState()

def render_ticket(t, path, blank=False):
    k, s = K[t["code"]], STYLE[t["code"]]
    W, H = A4; c = canvas.Canvas(path, pagesize=A4)
    f = s["font"]; fb = {"Helvetica": "Helvetica-Bold", "Courier": "Courier-Bold", "Times-Roman": "Times-Bold"}[f]
    c.setFillColor(colors.HexColor(s["color"])); c.rect(15*mm, H - 32*mm, W - 30*mm, 17*mm, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont(fb, 14); c.drawString(20*mm, H - 22*mm, k["name"].upper())
    c.setFont(f, 10); c.drawString(20*mm, H - 28*mm, "FIELD TICKET")
    c.setFont(fb, 12); c.drawRightString(W - 20*mm, H - 24*mm, f"No. {t['no']}")
    c.setFillColor(colors.black)
    y = H - 44*mm; c.setFont(f, 10)
    val = lambda v: "" if blank else v
    for lab, v in [("Date", t["date"].strftime(s["datefmt"])), ("Site", OP["site"]),
                   ("Contract", B["contract"]["title"].split("No. ")[1])]:
        c.setFont(fb, 10); c.drawString(20*mm, y, f"{lab}:"); c.setFont(f, 10)
        c.drawString(45*mm, y, val(v)); c.line(44*mm, y - 2, 120*mm, y - 2); y -= 7*mm

    def table(title, hdr, rows, widths, nrows):
        nonlocal y
        y -= 3*mm; c.setFont(fb, 10); c.drawString(20*mm, y, title); y -= 6*mm
        x = 20*mm; c.setFont(fb, 9)
        for h, w in zip(hdr, widths): c.drawString(x + 1.5*mm, y, h); x += w
        c.rect(20*mm, y - 2, sum(widths), 6*mm, stroke=1, fill=0)
        for r in range(nrows):
            y -= 6.5*mm; x = 20*mm; c.rect(20*mm, y - 2, sum(widths), 6.5*mm)
            if not blank and r < len(rows):
                c.setFont(f, 9)
                for v, w in zip(rows[r], widths): c.drawString(x + 1.5*mm, y, str(v)); x += w
        y -= 7*mm

    table("PERSONNEL", ["Name", "Role", "Hours"], [(n, r, h) for n, r, h in t["people"]], [70*mm, 60*mm, 40*mm],
          max(len(t["people"]), 2 if not blank else 4))
    table("EQUIPMENT", ["Equipment ID", "Description", "Qty", "Unit"], t["equip"], [40*mm, 70*mm, 30*mm, 30*mm],
          max(len(t["equip"]), 1 if not blank else 2))
    if t["qty_lines"] or blank:
        table("QUANTITIES", ["Item", "Qty", "Unit"], t["qty_lines"], [110*mm, 30*mm, 30*mm],
              max(len(t["qty_lines"]), 1 if not blank else 3))
    c.setFont(fb, 10); c.drawString(20*mm, y, "Remarks:"); c.setFont(f, 9)
    c.drawString(40*mm, y, val(t["remarks"])); c.line(39*mm, y - 2, W - 20*mm, y - 2)
    y -= 22*mm
    c.line(20*mm, y, 85*mm, y); c.line(115*mm, y, W - 20*mm, y)
    c.setFont(f, 8); c.drawString(20*mm, y - 4*mm, "Contractor Supervisor")
    c.drawString(115*mm, y - 4*mm, f"Approved: Company Representative ({REP['name']})")
    if not blank:
        signature(c, 25*mm, y + 4)
        if t["signed"]: signature(c, 125*mm, y + 4)
    c.setFont(f, 7); c.drawString(20*mm, 15*mm, "Charges not supported by a signed field ticket are not payable (MSA Sec. 5.1).")
    c.save()

def scanify(pdf_path):
    """Rasterize, grayscale, skew, add noise: looks like a photocopy scan."""
    base = f"{OUT}/_tmp/{os.path.basename(pdf_path)[:-4]}"
    subprocess.run(["pdftoppm", "-r", "130", "-gray", "-png", pdf_path, base], check=True)
    pages = []
    for p in sorted(glob.glob(base + "*.png")):
        im = Image.open(p).convert("L").rotate(random.uniform(-1.4, 1.4), expand=True, fillcolor=245)
        a = np.array(im, dtype=np.int16) + np.random.normal(0, 9, im.size[::-1]).astype(np.int16)
        im = Image.fromarray(np.clip(a * 0.93 + 12, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
        pages.append(im); os.remove(p)
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:], resolution=130)

# =========================================================== WRITE EVERYTHING
for i in invoices:
    p = f"{OUT}/invoices/{i['no']}.pdf"; render_invoice(i, p)
    if STYLE[i["code"]]["scan"]: scanify(p)
for t in tickets:
    p = f"{OUT}/tickets/{t['no']}.pdf"; render_ticket(t, p)
    if STYLE[t["code"]]["scan"] or t["handwritten"]: scanify(p)
    if t["handwritten"]:
        render_ticket(t, f"{OUT}/handwritten_forms/BLANK_{t['no']}.pdf", blank=True)

with open(f"{OUT}/handwritten_forms/FILL_IN_INSTRUCTIONS.txt", "w") as f:
    f.write("Print each BLANK form, fill it in by hand with the values below, photograph or scan it,\n"
            "and save it as tickets/<ticket no>.pdf (or .jpg), replacing the typed placeholder.\n\n")
    for t in [t for t in tickets if t["handwritten"]]:
        f.write(f"=== {t['no']} ({K[t['code']]['name']}) ===\nDate: {t['date']:%d %b %Y}\n")
        for n, r, h in t["people"]: f.write(f"  Person: {n} | {r} | {h} hrs\n")
        for e in t["equip"]: f.write(f"  Equipment: {e[0]} | {e[1]} | {e[2]} {e[3]}\n")
        for q in t["qty_lines"]: f.write(f"  Quantity: {q[0]} | {q[1]} {q[2]}\n")
        if t["remarks"]: f.write(f"  Remarks: {t['remarks']}\n")
        f.write("  Contractor supervisor: SIGN\n")
        f.write("  Company Representative: " + ("SIGN" if t["signed"] else "LEAVE BLANK (planted error E5)") + "\n\n")

# ---- ground truth for extraction checks
with open(f"{OUT}/truth_invoice_lines.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["invoice_no", "contractor_code", "week", "invoice_date", "period_start", "period_end", "line_no",
                "line_date", "ticket_ref", "description", "rate_code", "qty", "unit", "rate_php", "amount_php",
                "subtotal_php", "vat_php", "total_php", "planted_error"])
    for i in invoices:
        for n, l in enumerate(i["lines"], 1):
            w.writerow([i["no"], i["code"], i["week"], i["date"], *i["period"], n, l["date"] or "", l["ticket"],
                        l["desc"], l["rcode"], l["qty"], l["unit"], l["rate"], l["amount"],
                        i["subtotal"], i["vat"], i["total"], l.get("err", "")])
with open(f"{OUT}/truth_tickets.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ticket_no", "contractor_code", "ticket_date", "signed_by_company_rep", "handwritten", "scanned",
                "personnel", "equipment", "quantities", "remarks"])
    for t in tickets:
        w.writerow([t["no"], t["code"], t["date"], t["signed"], t["handwritten"], STYLE[t["code"]]["scan"] or t["handwritten"],
                    "; ".join(f"{n} ({r}, {h}h)" for n, r, h in t["people"]),
                    "; ".join(f"{e[0]} {e[2]} {e[3]}" for e in t["equip"]),
                    "; ".join(f"{q[0]}: {q[1]} {q[2]}" for q in t["qty_lines"]), t["remarks"]])

# ---- answer key
inv_no = {(i["code"], i["week"]): i["no"] for i in invoices}
with open(f"{OUT}/answer_key.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["error_id", "contractor_code", "week", "invoice_no", "description", "rule", "flagged_php",
                "decision", "correct_php", "overbilled_php", "withheld_php", "clauses", "evidence"])
    for e in ERR.values():
        held = e["correct_php"] if e["id"] == "E5" else 0
        w.writerow([e["id"], e["contractor"], e["week"], inv_no[(e["contractor"], e["week"])], e["description"],
                    e["rule"], e["flagged_php"], e["decision"], e["correct_php"], e["overbilled_php"], held,
                    "; ".join(e["evidence"]["clauses"]), "; ".join(e["evidence"]["records"])])

# ---- sample questions for Cortex Search and Cortex Analyst
QA = [
    ("search", "What can a contractor bill for equipment idle on location due to weather?", "50% of the day rate; stood-down personnel not billable", "4.2"),
    ("search", "Is travel time billable for contractor personnel?", "Yes, at standard hourly rates, capped at 2 hours each way per day", "5.4"),
    ("search", "Can a contractor charge a fuel surcharge?", "No; fuel surcharges are excluded and rates include fuel", "7.3; 4.1"),
    ("search", "What happens if a field ticket is not signed?", "Charges are not payable until the ticket is signed by the Company Representative", "5.1"),
    ("search", "When does Kusina Camp Services' contract term end?", "15 September 2026; work outside the term is not payable", "Exhibit B; 2.1"),
    ("search", "Can the same field ticket be billed twice?", "No; resubmitted tickets are not payable", "6.1"),
    ("analyst", "What was PU-101's status and hours run on 2026-09-10?", "IDLE, 0 hours", "equipment_log"),
    ("analyst", "On which days in the week of 2026-09-07 did PU-101 operate?", "Sep 7, 8, 9", "equipment_log"),
    ("analyst", "Were any PumpCo personnel on site on 2026-09-10 or 2026-09-11?", "No", "gate_log"),
    ("analyst", "What role does Paolo Cruz have on Liftmax timesheets?", "Rigger", "timesheets"),
    ("analyst", "How many hours were TruLine personnel on site on 2026-09-08, and through which gate?", "10 hours, SECONDARY gate", "gate_log"),
    ("analyst", "What was the camp headcount from 2026-09-16 to 2026-09-20?", "24 per day", "daily_reports"),
    ("analyst", "How many AquaHaul water truck trips entered the site on 2026-09-07?", "6", "gate_log"),
]
with open(f"{OUT}/qa_samples.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["tool", "question", "expected_answer", "source"]); w.writerows(QA)

os.system(f"rm -rf {OUT}/_tmp")
print(f"invoices: {len(invoices)}  tickets: {len(tickets)}  handwritten: {sum(t['handwritten'] for t in tickets)}")
for i in invoices:
    print(f"  {i['no']:<14} {i['week']}  subtotal {i['subtotal']:>12,.2f}  " +
          ",".join(sorted({l.get('err', '') for l in i['lines']} - {''})))
