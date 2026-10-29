> The company, people, contracts and invoice numbers on this page are from the synthetic sample pack in `merry_agent/data/`.

# MERRY: product spec

## User

The **contracts analyst** (cost controller) at Pinnacle Upstream Philippines Inc. Their job is to check contractor invoices before accounts payable pays them. They know the contracts, but they don't have time to cross-check every line against the gate log by hand. The company representative on site, Ramon Dizon, signs field tickets. He is not the user.

The deliverable the analyst cares about, per invoice: **"Pay this much. Dispute this much, and here's why. Hold this much until X."**

## The user journey

The app is one conversation per contract (concept v2, `docs/ui_concept.html`). The header has a single **Contract** dropdown, empty and disabled until a contract exists, and three tabs: **Review** (the conversation), **Dashboard**, **Documents**. Every file, whether contract, invoice or field ticket, is dropped in the same place: the chat's message box.

### Setup, once per contract

1. Open the app. MERRY greets the analyst and asks for a service contract.
2. Drop one or more contract PDFs in the chat (demo: PUP-MSA-2026-019 on top of the already onboarded PUP-MSA-2026-014).
3. MERRY works out that each file is a contract and reads it, showing its progress: parse, split clauses, read the contractors and terms (Exhibit B), the rates (Exhibit A) and the rule settings, map roles to rates, index the clauses for search.
4. MERRY reports what it read: contractors, rates, rule settings and warnings.
   - **No rate warnings:** the contract is confirmed automatically ("as extracted, automatically"). MERRY says so and suggests "Review its values".
   - **Any rate warning:** the contract stays unconfirmed and MERRY asks the analyst to review it or confirm it anyway.
5. The contract appears in the Contract dropdown. The analyst can still **correct any extracted value** in Documents. Every edit is logged with old value, new value, user and time, and audited periods are marked stale, with a "Re-run the audit" suggestion.

### Each batch of documents, per contract

6. Drop any mix of invoices and field tickets in the chat: one week, two weeks, or a whole month.
7. MERRY sorts them. It reads each file's type, contractor and printed dates, and files it:
   - to the contract whose contractors include that contractor;
   - to the internal Monday–Sunday period(s) given by the invoice's printed billing dates or the ticket's date.

   Filing is logical: nothing is moved on the stage. MERRY asks instead of guessing when:
   - a file matches no contract or more than one;
   - its type is unknown;
   - its dates can't be read;
   - an invoice's billing dates span two periods.

   A file whose content was already filed is ignored, and MERRY says where it is filed. A new file with an existing file name but different content is replaced only if the analyst agrees.
8. MERRY describes the batch by the dates it covers, never by week numbers. For example, "I sorted 72 files covering Aug 31 – Sep 27".
   - If the files fall into one contiguous range, MERRY offers one suggestion: "Run the audit for Aug 31 – Sep 27".
   - If they split, MERRY states the split and offers both options. For example: "Most files cover Sep 7 – 13. One invoice, AQH-INV-320, covers Sep 14 – 20." The options are "Audit Sep 7 – 13 only" and "Audit Sep 7 – 20".
9. Clicking a run suggestion queues the audit of every affected period. A progress message shows the real steps per period: extract invoices and tickets → match to rates, tickets and site records → run billing checks → review each flag → draft resolutions. The audit keeps going if the page is closed or reloaded.
10. When the last period finishes, MERRY posts one **briefing** for the whole range, under a divider that shows the range. The briefing gives:
    - the invoices and the amount billed;
    - what was flagged;
    - what is disputed, held or cleared;
    - the recommended payment;
    - repeat patterns from earlier periods and documents to verify.

    A **findings table** inside the message lists each finding with its invoice, decision and amount. A corrected or dismissed amount is shown struck through. The table is rendered fresh from the audit tables.
11. Every MERRY message ends with **What I can do next**: suggestions generated from the current table state, never by the LLM. Clicking a suggestion runs it. A suggestion that changes money shows its exact effect, e.g. "Approve PCS-INV-1042 and save the PDF: dispute ₱40,000, pay ₱460,000 + VAT", and clicking it is the confirmation. Typical suggestions:
    - **Explain** a finding: the reasoning, the cited clause text and site records, and the tool steps, from the stored audit.
    - **Show the dispute letter**: an editable letter in the thread, one per invoice, covering every finding on it. "Make it firmer" revises the wording. The amounts, payment line and clause numbers are locked.
    - **Approve … and save the PDF** for a dispute or hold, and **Approve … for payment** for clean invoices and invoices whose flags were cleared, including "Approve the n clean invoices".
    - **Why was … cleared?**, **Has PumpCo billed idle equipment at the full rate in earlier periods?** (one per repeat pattern) and **What should I verify on …?** (one per verify flag).
    - **Change a decision…**, always offered when the range has findings. It opens a short form (finding, new decision, required reason). The amounts are recalculated, the letter is redrafted, and the reason is shown on the finding.
12. The analyst can also type. The agent answers in chat mode, scoped to the contract and the range. Vague follow-ups are rewritten into a standalone question. Demo challenge: "PumpCo says the typhoon hold started Sep 9. Check that." The agent re-queries the records, finds Sep 9 was an operating day, and keeps the decision. A typed request that changes money ("approve the clean invoices") comes back as a money suggestion and runs only when the analyst clicks it. A valid challenge comes back as a proposed revision to accept.

### Documents tab

13. Lists the contracts with their extracted values, editable with the edit log. Until step 14, this tab shows the former Contracts tab content.

### Dashboard, per selected contract

14. Totals: billed, flagged by checks, disputed, on hold, flags cleared, and **approved for payment** (the lead number), plus the count of invoices awaiting approval.
15. A repeat-pattern callout when the same contractor has the same rule type upheld in two or more periods.
16. Flags by contractor (disputed / on hold / cleared).
17. A contractor × date-range grid of invoice status: not received, clean, dispute drafted, dispute approved, hold drafted, hold approved, flag cleared, approved.
18. Contract 2 shows an empty state until invoices exist.

## Scope

| Feature | Status |
|---|---|
| One conversation per contract; Contract dropdown and Review / Dashboard / Documents tabs | In |
| Drop any file in the chat; MERRY classifies, routes and files it, and asks when unsure | In |
| Contract reading with progress; automatic confirmation when there are no rate warnings | In |
| Edit extracted contract values, with edit log and stale flag | In |
| Batches described by date range; one run per range, processed per weekly period | In |
| Progress per period that survives a reload | In |
| One briefing per range, with a findings table | In |
| Deterministic "What I can do next" suggestions; money suggestions show their exact effect | In |
| Explain with citations | In |
| One letter per invoice, inline: edit, make firmer, approve and save PDF | In |
| Change a decision (analyst override with reason) | In |
| Challenge, with the agent re-checking records | In |
| Cross-period pattern question | In |
| Dashboard: totals, pattern callout, by contractor, grid | In |
| Free-form chat with the agent | In, but not demoed off-script |
| Separate upload lanes, invoice cards with buttons, service-period dropdown | **Out** (replaced by the conversation, D27) |
| Emailing contractors | **Out** |
| Generating new rule types from arbitrary contract clauses | **Out.** The pitch says "configurable rule library" |

## Demo plan (target 5 minutes)

1. **Before the demo:** PUP-MSA-2026-014 onboarded and confirmed. Aug 31 – Sep 6 audited and its resolutions approved, so the pattern exists.
2. **Live:**
   - Drop PUP-MSA-2026-019 in the chat: it is read and confirmed automatically, and appears in the dropdown.
   - Back on MSA-014, drop the Sep 7 – 13 invoices and tickets plus the stray AQH-INV-320. MERRY states the split; click "Audit Sep 7 – 13 only".
   - Walk through the briefing and PCS-INV-1042: explain, challenge (Sep 9), pattern, show the letter, make it firmer, approve.
   - Then "Why was TruLine cleared?" (TLW-INV-5513), and approve the clean invoices, all with suggestions.
3. Dashboard: approved for payment, the PumpCo callout, and the grid. Switch to Contract 2 to show scoping.

## Expected results (for validation only; never used by code)

Across all four periods: 9 flags, ₱360,200 flagged. Agent: ₱287,000 upheld = ₱236,500 overbilled + ₱50,500 withheld. ₱13,200 dismissed (E8). See `README.md`.
