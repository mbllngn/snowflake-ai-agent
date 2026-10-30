"""MERRY audit rules: what each check means for the reviewer, and the review statuses.

The checks themselves run in SQL (AUDIT.SP_RUN_CHECKS, merry_agent/sql/40_procedures.sql) and their results are read
from APP.V_UI_FLAGS. This file only explains them on screen: dashboard.py and streamlit_app.py import from here.
"""

import pandas as pd

STATUSES = ["OPEN", "CONFIRMED", "RECOVERED", "DISMISSED"]

# Plain-language explanation for every rule name MERRY can show:
# rule -> (why it is a potential issue, contract basis, what the reviewer should check)
RULE_INFO = {
    "Ticket vs Timesheet Hours": ("Hours on the field ticket differ from the hours on the timesheet",
                                  "MSA 5.2-5.3", "Compare ticket, timesheet and gate log; ask contractor which is correct"),
    "Missing Timesheet": ("Person billed on a ticket has no timesheet for that day",
                          "MSA 5.2", "Request the timesheet or remove the billed hours"),
    "Timesheet vs Gate Hours": ("Timesheet hours exceed time on site (gate in to gate out) plus allowed travel",
                                "MSA 5.3-5.4", "Check gate log times and travel claims"),
    "No Gate Record": ("Timesheet shows hours but the person never passed the gate that day",
                       "MSA 3.2, 5.3", "Confirm the person was on site"),
    "Hours on Suspended Day": ("Hours logged on a day the daily report marks operations SUSPENDED",
                               "MSA 4.2", "Personnel stood down are not billable - remove unless on site by instruction"),
    "Rate Above Contract": ("Invoice rate is higher than the Exhibit A contract rate",
                            "MSA 4.1, Exhibit A", "Short-pay the difference"),
    "Rate Above Rate Card": ("Invoice rate is higher than the rate card CSV",
                             "Rate card vs MSA Exhibit A", "Confirm which rate is agreed (rate card may be an amendment)"),
    "Line Amount Error": ("Line amount is not qty x rate", "MSA 6.3 (correct invoice)", "Recalculate the line"),
    "Invoice Total Error": ("Subtotal, VAT or total does not add up", "MSA 6.3 (correct invoice)",
                            "Ask for a corrected invoice"),
    "Invoice Line Without Ticket": ("Invoice line has no matching signed field ticket",
                                    "MSA 5.1, 6.2", "Not payable until the ticket is provided"),
    "Qty vs Ticket": ("Invoiced quantity differs from the field ticket", "MSA 5.1", "Pay the ticket quantity"),
    "Date vs Ticket": ("Invoice line date differs from the field ticket date", "MSA 6.2",
                       "Check for mis-dated or re-used tickets"),
    "Loads vs Gate Entries": ("More loads invoiced than trucks recorded at the gate", "MSA 3.2",
                              "Pay only loads supported by gate entries"),
    "Duplicate Ticket Billed": ("Same field ticket billed more than once", "MSA 6.1", "Reject the repeat line"),
    "Billed Outside Contract Term": ("Work billed outside the contractor's term", "MSA 2.1, Exhibit B",
                                     "Not payable unless a written extension exists (MSA 2.3)"),
    "Work Outside Contract Term": ("Timesheet hours outside the contractor's term", "MSA 2.1, Exhibit B",
                                   "Check for an extension before any billing"),
    "Billed on Suspended Day": ("Work billed on a day operations were SUSPENDED", "MSA 4.2",
                                "Idle equipment at 50% standby only; personnel not billable"),
    "Non-Billable Item": ("Fuel surcharge, mob/demob or camp meals billed", "MSA 7.3",
                          "Remove unless pre-approved in writing"),
    "Ticket Not Approved": ("Field ticket has no Company Representative approval", "MSA 1.2, 5.1",
                            "Get the ticket signed before paying"),
    "Ticket vs Site Records": ("Ticket or invoice tonnes/loads differ from the equipment log or daily report "
                               "(flagged even if not billed; peso impact when the invoice bills more)",
                               "MSA 5.1-5.2, 7.1",
                               "Correct the ticket; pay only the quantity in the site records"),
    "Role vs Ticket": ("Person billed in a higher-rate role than the ticket shows (e.g. Rigger billed as Crane "
                       "Operator)", "MSA 4.1, 5.2", "Pay the contract rate for the role on the ticket"),
    "Person Not on Ticket": ("Person named on the invoice is not on the field ticket", "MSA 5.1-5.2",
                             "Not payable unless a signed ticket shows them"),
}


# MERRY's own checks, by rule type: (why it is a potential issue, what the reviewer should check). The contract basis
# is not written here: it is the clauses MERRY cited for the finding (V_UI_FLAGS.CLAUSES), from the contract itself.
MERRY_RULE_INFO = {
    "RATE": ("The invoice rate is higher than the contract rate for that role or item",
             "Compare the billed rate with the contract rate table; pay the contract rate"),
    "MATH": ("A line amount is not quantity x rate", "Recalculate the line; ask for a corrected invoice"),
    "DUPLICATE": ("The same field ticket was already billed on an earlier invoice", "Reject the repeat charge"),
    "TICKET_BACKING": ("Charges are not backed by a field ticket signed by the company representative: the ticket "
                       "is unsigned, was never uploaded, or the line cites none",
                       "Ask for the signed field ticket; upload it and MERRY re-audits the week"),
    "ELIGIBILITY_TERM": ("Work is billed on dates outside the contractor's term",
                         "Check for a written extension; otherwise not payable"),
    "ELIGIBILITY_EXCLUSION": ("A charge the contract lists as not billable was billed",
                              "Remove the charge unless it was approved in writing"),
    "SITE_EQUIPMENT_DAYS": ("Equipment billed at the full rate on days the site records show it idle or standing by",
                            "Check the equipment log and daily report; pay the standby rate for idle days"),
    "SITE_HOURS": ("More hours billed than the gate log shows on site, beyond the travel the contract allows",
                   "Check the gate log and timesheets; pay the hours on site"),
    "UNREADABLE": ("MERRY couldn't read or place this document, so it was not audited",
                   "Upload a readable copy with the same file name, or dismiss it if it isn't an invoice or ticket"),
}


def rule_info_for(flags: pd.DataFrame) -> None:
    """MERRY's checks have their own names: give RULE_INFO a line for each (why, contract basis, what to check)."""
    for rule, rows in flags.groupby("RULE"):
        if rule not in RULE_INFO:
            clauses = sorted({c.strip() for cs in rows["CLAUSES"].dropna() for c in str(cs).split(",") if c.strip()})
            basis = ", ".join(("Exhibit " if c.isalpha() else "Clause ") + c for c in clauses)
            why, check = MERRY_RULE_INFO.get(rows["RULE_TYPE"].iloc[0] if "RULE_TYPE" in rows else None,
                                             (rule, "Open the invoice in Ask MERRY to see the evidence and the letter"))
            RULE_INFO[rule] = (why, basis or "—", check)
