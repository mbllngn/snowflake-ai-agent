"""Generate the MSA contract PDF and rate card CSV from story_bible.yaml."""
import csv
import yaml
from datetime import date
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, KeepTogether)

B = yaml.safe_load(open("story_bible.yaml"))
C = B["contract"]
OP = B["operator"]
OUT = "out"

def d(x):  # 2026-09-15 -> 15 September 2026
    x = x if isinstance(x, date) else date.fromisoformat(str(x))
    return x.strftime("%d %B %Y").lstrip("0")

def php(n):
    return f"PHP {n:,.2f}"

# ------------------------------------------------------------------ styles
ss = getSampleStyleSheet()
body = ParagraphStyle("body", parent=ss["Normal"], fontName="Times-Roman",
                      fontSize=10.5, leading=14, spaceAfter=6, alignment=4)
h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Times-Bold", fontSize=15, spaceAfter=4)
sub = ParagraphStyle("sub", parent=body, alignment=1, fontSize=10.5)
art = ParagraphStyle("art", parent=body, fontName="Times-Bold", fontSize=11,
                     spaceBefore=10, spaceAfter=4, alignment=0)
cl = ParagraphStyle("cl", parent=body, leftIndent=0)
small = ParagraphStyle("small", parent=body, fontSize=9, leading=11, alignment=0)

def clause(num, title, text):
    return Paragraph(f"<b>{num} {title}.</b> {text}", cl)

def key(num):  # clause text straight from the bible, so citations stay exact
    c = C["clauses"][num]
    return clause(num, c["title"], c["text"])

# ------------------------------------------------------------- the document
S = []
S += [Paragraph("MASTER SERVICE AGREEMENT", h1),
      Paragraph(f"Agreement No. {C['title'].split('No. ')[1]}", sub),
      Spacer(1, 8)]

S.append(Paragraph(
    f"This Master Service Agreement (the <b>Agreement</b>) is entered into as of "
    f"{d(C['effective'])} (the <b>Effective Date</b>) by and between "
    f"<b>{OP['name']}</b>, a corporation organized under the laws of the Republic of the "
    f"Philippines (the <b>Company</b>), and each contractor that executes a Contractor "
    f"Schedule under this Agreement (each, a <b>Contractor</b>). The Company and each "
    f"Contractor are referred to individually as a <b>Party</b>.", body))

S.append(Paragraph("ARTICLE 1. DEFINITIONS", art))
S += [
    clause("1.1", "Site", f"means {OP['site']}, including its access roads, gates, and the Company camp."),
    clause("1.2", "Company Representative", "means the individual designated by the Company to direct operations at the Site, approve field tickets, and record daily reports."),
    clause("1.3", "Field Ticket", "means the Contractor's daily record of personnel, equipment, quantities, and hours supplied at the Site."),
    clause("1.4", "Contractor Schedule", "means the schedule attached as Exhibit B that identifies a Contractor, its term, and its applicable rates."),
    clause("1.5", "Day", "means a calendar day. A day rate covers up to twelve (12) hours of work unless the Rate Schedule states otherwise."),
]

S.append(Paragraph("ARTICLE 2. TERM", art))
S += [key("2.1"),
      clause("2.2", "Expiry Date", f"Unless terminated earlier, this Agreement expires on {d(C['expiry'])}."),
      clause("2.3", "Extension", "A Contractor Schedule may be extended only by a written amendment signed by both Parties before the end of its term.")]

S.append(Paragraph("ARTICLE 3. SCOPE OF WORK", art))
S += [clause("3.1", "Work Orders", "Contractor shall perform the services described in its Contractor Schedule as directed by the Company Representative."),
      clause("3.2", "Site Access", "All Contractor personnel and equipment shall enter and exit the Site through a Company-controlled gate and shall be recorded in the gate log."),
      clause("3.3", "Weather Suspension", "The Company Representative may suspend operations when weather conditions make work unsafe. Contractor shall comply immediately and shall not resume until directed.")]

S.append(Paragraph("ARTICLE 4. RATES AND CHARGES", art))
S += [key("4.1"), key("4.2"), key("4.3"),
      clause("4.4", "Rate Adjustments", "Rates are fixed for the term of each Contractor Schedule and may change only by written amendment.")]

S.append(Paragraph("ARTICLE 5. FIELD TICKETS AND TIME", art))
S += [key("5.1"),
      clause("5.2", "Content", "Each Field Ticket shall state the date, ticket number, personnel by name and role, equipment by identifier, hours or quantities, and the Rate Schedule code for each item."),
      clause("5.3", "Hours", "Personnel hours are measured from gate entry to gate exit, as recorded in the gate log, except as provided in Section 5.4."),
      key("5.4")]

S.append(Paragraph("ARTICLE 6. INVOICING AND PAYMENT", art))
S += [key("6.1"),
      clause("6.2", "Supporting Documents", "Each invoice shall reference the Field Tickets it bills and attach copies of them."),
      clause("6.3", "Payment", "The Company shall pay undisputed amounts within thirty (30) days after receipt of a correct invoice."),
      clause("6.4", "Disputes", "The Company may withhold any disputed amount by written notice identifying the invoice line, the amount, and the reason. The Parties shall attempt in good faith to resolve the dispute within fifteen (15) days.")]

S.append(Paragraph("ARTICLE 7. GENERAL", art))
S += [clause("7.1", "Audit", "The Company may audit any invoice, Field Ticket, or supporting record for two (2) years after payment and may recover any amount found to have been overbilled."),
      clause("7.2", "Health, Safety and Environment", "Contractor shall comply with the Company's HSE policies and all applicable laws while at the Site."),
      key("7.3"),
      clause("7.4", "Governing Law", "This Agreement is governed by the laws of the Republic of the Philippines.")]

# signature block
S.append(Spacer(1, 14))
sig = Table([
    [Paragraph(f"<b>{OP['name']}</b>", small), Paragraph("<b>CONTRACTOR</b> (per Exhibit B)", small)],
    ["\n\n_____________________________", "\n\n_____________________________"],
    [Paragraph("Authorized Signatory", small), Paragraph("Authorized Signatory", small)],
], colWidths=[85*mm, 85*mm])
sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("FONTNAME", (0, 0), (-1, -1), "Times-Roman")]))
S.append(KeepTogether(sig))

# ---------------------------------------------------------- Exhibit A rates
S += [PageBreak(), Paragraph("EXHIBIT A: RATE SCHEDULE", art),
      Paragraph("All rates are in Philippine Pesos (PHP), exclusive of VAT, and subject to Sections 4.1 to 4.3.", body)]

hdr = ["Code", "Item", "Unit", "Rate (PHP)"]
for code, k in B["contractors"].items():
    rows = [hdr] + [[r["code"], r["item"], r["unit"], f"{r['rate']:,.2f}"] for r in k["rates"]]
    t = Table(rows, colWidths=[30*mm, 75*mm, 25*mm, 35*mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6E6E6")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (3, 1), (3, -1), "RIGHT"),
    ]))
    S.append(KeepTogether([Paragraph(f"<b>A.{list(B['contractors']).index(code)+1} {k['name']} ({code})</b>", small),
                           Spacer(1, 3), t, Spacer(1, 10)]))

# ---------------------------------------------------- Exhibit B schedules
S += [Paragraph("EXHIBIT B: CONTRACTOR SCHEDULES", art),
      Paragraph("Each Contractor below has executed this Agreement for the term stated. Section 2.1 applies to any work outside that term.", body)]
rows = [["Contractor", "Code", "Base", "Term Start", "Term End"]]
for code, k in B["contractors"].items():
    rows.append([k["name"], code, k["base"], d(k["term"]["start"]), d(k["term"]["end"])])
t = Table(rows, colWidths=[52*mm, 15*mm, 35*mm, 33*mm, 33*mm])
t.setStyle(TableStyle([
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6E6E6")),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
]))
S.append(t)

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawString(20*mm, 10*mm, f"{C['title']}  |  Confidential")
    canvas.drawRightString(190*mm, 10*mm, f"Page {doc.page}")
    canvas.restoreState()

doc = SimpleDocTemplate(f"{OUT}/contract_msa.pdf", pagesize=A4,
                        leftMargin=20*mm, rightMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm,
                        title=C["title"], author=OP["name"])
doc.build(S, onFirstPage=footer, onLaterPages=footer)

# ---------------------------------------------------------- rate card CSV
with open(f"{OUT}/rate_card.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["contractor_code", "contractor_name", "rate_code", "item", "unit",
                "rate_php", "standby_rate_php", "term_start", "term_end"])
    for code, k in B["contractors"].items():
        for r in k["rates"]:
            is_equip = "-EQ-" in r["code"]
            w.writerow([code, k["name"], r["code"], r["item"], r["unit"], r["rate"],
                        int(r["rate"] * 0.5) if is_equip else "", k["term"]["start"], k["term"]["end"]])

print("done")
