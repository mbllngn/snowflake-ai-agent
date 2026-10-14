"""Generate site records (gate log, equipment log, timesheets, daily reports)
from story_bible.yaml. These record what ACTUALLY happened. Invoices are
compared against them, so planted errors never touch this file."""
import csv
import random
import yaml
from datetime import date, datetime, timedelta

random.seed(42)  # same output every run
B = yaml.safe_load(open("story_bible.yaml"))
K, ACT, OUT = B["contractors"], B["activity"], "out"
REP = B["operator"]["company_rep"]["id"]

def D(x):
    return x if isinstance(x, date) else date.fromisoformat(str(x))

def days(a, b):
    a, b = D(a), D(b)
    return [a + timedelta(i) for i in range((b - a).days + 1)]

ALL_DAYS = days(B["meta"]["weeks"]["W1"]["start"], B["meta"]["weeks"]["W4"]["end"])
WEATHER = {D(w["date"]): w for w in B["weather"]}
NOTES = {D(k): v for k, v in B["daily_report_notes"].items()}

def ts(day, hh, mm):
    return datetime(day.year, day.month, day.day) + timedelta(hours=hh, minutes=mm)

def jitter(mins=15):
    return random.randint(-mins, mins)

gate, equip, sheets = [], [], []
activity_text = {d: [] for d in ALL_DAYS}

def crew_day(code, worker, day, start_h=6, hours=12, gate_name="MAIN", sheet_hours=None, remark=""):
    t_in = ts(day, start_h, 0) + timedelta(minutes=jitter())
    t_out = t_in + timedelta(hours=hours, minutes=jitter(10))
    gate.append(["PERSON", worker["id"], worker["name"], code, gate_name, t_in, t_out, remark])
    sheets.append([day, worker["id"], worker["name"], code, worker["role"],
                   sheet_hours if sheet_hours is not None else round((t_out - t_in).seconds / 3600, 1),
                   REP])

# ------------------------------------------------------------------ PumpCo
pcs, pu = K["PCS"], "PU-101"
stints = [(D("2026-09-01"), D("2026-09-16")), (D("2026-09-22"), D("2026-09-24"))]
op_days, standby_days, tonnes = set(), set(), {}
for wk, a in ACT["PCS"].items():
    ops = [D(x) for x in a["operating"]]
    op_days |= set(ops)
    standby_days |= {D(x) for x in a.get("standby_on_location", [])}
    base, extra = divmod(a["proppant_tonnes"], len(ops))
    for i, d in enumerate(ops):
        tonnes[d] = base + (1 if i < extra else 0)

for s, e in stints:
    gate.append(["EQUIPMENT", pu, "Pump unit", "PCS", "MAIN", ts(s, 6, 30), ts(e, 9, 0),
                 "Mobilized / demobilized"])
    for d in days(s, e):
        if d == e and e == D("2026-09-16"):
            equip.append([d, pu, "PCS", "DEMOBILIZED", 0, "Rigged down and released"])
            continue
        if d in op_days:
            hrs = round(random.uniform(8.5, 10.5), 1)
            equip.append([d, pu, "PCS", "OPERATING", hrs, f"Proppant pumped: {tonnes[d]} t"])
            activity_text[d].append(f"PumpCo pumping ops on PU-101, {tonnes[d]} t proppant.")
            for w in pcs["crew"]:
                crew_day("PCS", w, d)
        elif d in standby_days:
            equip.append([d, pu, "PCS", "IDLE", 0, ""])
        else:
            equip.append([d, pu, "PCS", "OFF_HIRE", 0, "No work scheduled"])

# ----------------------------------------------------------------- Liftmax
lcr = K["LCR"]
for wk, a in ACT["LCR"].items():
    ops = [D(x) for x in a["operating"]]
    gate.append(["EQUIPMENT", "CR-301", "50-ton crane", "LCR", "MAIN",
                 ts(ops[0], 6, 15), ts(ops[-1], 18, 30), "Call-out"])
    for d in ops:
        equip.append([d, "CR-301", "LCR", "OPERATING", round(random.uniform(7, 10), 1), "Lifts per Company direction"])
        activity_text[d].append("Liftmax crane CR-301 on location for lifts.")
        for w in lcr["crew"]:
            crew_day("LCR", w, d)

# --------------------------------------------------------------- AquaHaul
aqh = K["AQH"]
trucks = list(zip([e["id"] for e in aqh["equipment"]], aqh["crew"]))
for wk, a in ACT["AQH"].items():
    for d, n in a["loads"].items():
        d = D(d)
        used = trucks if n > 3 else trucks[:1]
        for _, drv in used:
            crew_day("AQH", drv, d, start_h=6, hours=10)
        for i in range(n):
            truck, _ = used[i % len(used)]
            t_in = ts(d, 7, 0) + timedelta(minutes=90 * (i // len(used)) + 20 * (i % len(used)) + jitter(10))
            gate.append(["EQUIPMENT", truck, "Water truck", "AQH", "MAIN", t_in,
                         t_in + timedelta(minutes=40 + jitter(5)), "Water delivery, 20 m3"])
        activity_text[d].append(f"AquaHaul delivered {n} loads of water.")

# ---------------------------------------------------------------- TruLine
tlw = K["TLW"]
for wk, a in ACT["TLW"].items():
    for d, runs in a["runs"].items():
        d = D(d)
        travel = a["travel_hours_each_way"]
        on_site = a["hours_on_site"]
        g = "SECONDARY" if travel else "MAIN"
        start = 8 if travel else 6
        gate.append(["EQUIPMENT", "WL-501", "Wireline unit", "TLW", g, ts(d, start, 5),
                     ts(d, start + on_site, 0), ""])
        equip.append([d, "WL-501", "TLW", "OPERATING", on_site - 1, f"{runs} wireline runs"])
        activity_text[d].append(f"TruLine wireline unit WL-501, {runs} runs.")
        for w in tlw["crew"]:
            # gate shows on-site hours; timesheet shows on-site + travel
            t_in = ts(d, start, 0)
            gate.append(["PERSON", w["id"], w["name"], "TLW", g, t_in, t_in + timedelta(hours=on_site), ""])
            sheets.append([d, w["id"], w["name"], "TLW", w["role"], on_site + 2 * travel, REP])

# ---------------------------------------------------------- daily reports
kcs_end = D(K["KCS"]["term"]["end"])
reports = []
for d in ALL_DAYS:
    w = WEATHER.get(d)
    camp = K["KCS"]["camp_headcount"] if d <= D("2026-09-20") else 0
    acts = " ".join(activity_text[d]) or ("No field work scheduled." if not w else "")
    note = NOTES.get(d, "")
    if d == D("2026-09-21"):
        note = "Camp closed. Crews lodged off-site from this date."
    if not note:
        note = "No incidents."
    reports.append([d, w["condition"] if w else random.choice(["Fair", "Partly cloudy", "Fair", "Light showers"]),
                    "SUSPENDED" if w else "NORMAL", camp, acts, note, REP])

# ------------------------------------------------------------------ write
gate.sort(key=lambda r: (r[5], r[1]))
with open(f"{OUT}/gate_log.csv", "w", newline="") as f:
    wr = csv.writer(f)
    wr.writerow(["gate_entry_id", "entry_date", "subject_type", "subject_id", "subject_name",
                 "contractor_code", "gate", "time_in", "time_out", "remarks"])
    for i, r in enumerate(gate, 1):
        wr.writerow([f"G-{i:05d}", r[5].date()] + r[:5] + [r[5].strftime("%Y-%m-%d %H:%M"),
                     r[6].strftime("%Y-%m-%d %H:%M"), r[7]])

equip.sort(key=lambda r: (r[0], r[1]))
with open(f"{OUT}/equipment_log.csv", "w", newline="") as f:
    wr = csv.writer(f)
    wr.writerow(["log_date", "equipment_id", "contractor_code", "status", "hours_run", "remarks"])
    wr.writerows(equip)

sheets.sort(key=lambda r: (r[0], r[1]))
with open(f"{OUT}/timesheets.csv", "w", newline="") as f:
    wr = csv.writer(f)
    wr.writerow(["work_date", "worker_id", "worker_name", "contractor_code", "role", "hours", "approved_by"])
    wr.writerows(sheets)

with open(f"{OUT}/daily_reports.csv", "w", newline="") as f:
    wr = csv.writer(f)
    wr.writerow(["report_date", "weather", "ops_status", "camp_headcount", "activities",
                 "supervisor_notes", "reported_by"])
    wr.writerows(reports)

print(f"gate_log: {len(gate)}  equipment_log: {len(equip)}  timesheets: {len(sheets)}  daily_reports: {len(reports)}")
