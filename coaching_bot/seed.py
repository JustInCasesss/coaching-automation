"""Seed a realistic coaching-institute database so every feature can be demoed
and verified end to end without touching a real client's data.

Deliberately plants a "reminder showcase": seven fees whose due dates land
exactly on the seven reminder offsets, so one scheduler tick exercises every
stage (T-7 through D+15) and produces a full, verifiable run report.
"""
from __future__ import annotations

import random
from datetime import timedelta

from . import config, reminders, store

RNG = random.Random(20261014)

FIRST_M = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan",
           "Krishna", "Ishaan", "Rudra", "Kabir", "Atharva", "Dhruv", "Yash", "Om"]
FIRST_F = ["Ananya", "Diya", "Aadhya", "Saanvi", "Pari", "Anika", "Navya", "Myra",
           "Sara", "Ira", "Riya", "Kiara", "Aarohi", "Prisha", "Tanvi", "Meera"]
LAST = ["Sharma", "Patil", "Deshmukh", "Kulkarni", "Joshi", "Iyer", "Nair", "Reddy",
        "Gupta", "Verma", "Singh", "Mehta", "Chavan", "Bhosale", "Rao", "Kadam"]
PARENT_PREFIX = ["Mr.", "Mrs.", "Mr.", "Mrs.", "Dr."]
REMARKS = ["Consistent effort, needs speed practice.",
           "Concepts clear, careless mistakes in calculation.",
           "Improving steadily - keep revising formulae.",
           "Strong in theory, must attempt more numericals.",
           "Excellent grasp of fundamentals.",
           "Needs to revise the last two chapters before the next test.",
           "Very good improvement since last test.",
           "Attendance is affecting performance - please ensure regularity."]

SUBJECTS = {
    "NEET": ["Physics", "Chemistry", "Botany", "Zoology"],
    "JEE": ["Physics", "Chemistry", "Mathematics"],
    "FOUNDATION": ["Mathematics", "Science", "English", "Social Science"],
    "BOARD": ["Mathematics", "Science", "English"],
}
TEST_NAMES = {
    "NEET": ["Weekly Test 07 - Human Physiology", "Weekly Test 08 - Thermodynamics",
             "Full Syllabus Mock 02", "Weekly Test 09 - Organic Chemistry",
             "Part Test 04 - Genetics"],
    "JEE": ["Weekly Test 07 - Rotational Motion", "Weekly Test 08 - Calculus",
            "Full Syllabus Mock 02", "Weekly Test 09 - Coordination Chemistry",
            "Part Test 04 - Matrices"],
    "FOUNDATION": ["Unit Test 04 - Linear Equations", "Unit Test 05 - Light",
                   "Half Yearly", "Unit Test 06 - Cell Biology"],
    "BOARD": ["Prelim 01", "Prelim 02", "Chapter Test - Trigonometry"],
}
BATCHES = {
    "NEET": ["NEET-11-A Morning", "NEET-12-B Morning", "NEET-Dropper Evening"],
    "JEE": ["JEE-11-A Morning", "JEE-12-B Evening", "JEE-Dropper Morning"],
    "FOUNDATION": ["FND-9-A Evening", "FND-10-B Evening"],
    "BOARD": ["BRD-10-A Evening", "BRD-12-A Evening"],
}


def _name(i: int) -> tuple[str, str]:
    first = (FIRST_M + FIRST_F)[i % (len(FIRST_M) + len(FIRST_F))]
    last = LAST[(i * 7) % len(LAST)]
    return first, last


def _phone(n: int) -> str:
    return "+9198" + f"{n:08d}"[-8:]


def seed(con, n_students: int = 24, wipe: bool = True) -> dict:
    if wipe:
        for tbl in ("reminder_log", "payments", "tests", "attendance", "fee_schedule",
                    "students", "leads", "conversations", "messages", "alerts", "kpi_daily"):
            con.execute(f"DELETE FROM {tbl}")
        con.commit()

    inst = config.institute()
    today = config.today_ist()
    students = []

    # ---- students + fees -------------------------------------------------
    for i in range(n_students):
        course = config.courses()[i % len(config.courses())]
        batch_pool = BATCHES[course["code"]]
        batch = batch_pool[i % len(batch_pool)]
        klass = course["classes"][i % len(course["classes"])]
        first, last = _name(i)
        parent = f"{PARENT_PREFIX[i % len(PARENT_PREFIX)]} {RNG.choice(FIRST_M + FIRST_F)} {last}"
        total = float(course["fee_total"])
        plan = RNG.choice(course["fee_plans"])
        n_inst = 1 if "One-time" in plan else (2 if "2 " in plan else (3 if "3 " in plan else 6))
        roll = f"{course['code'][:3]}-{2026}-{100 + i}"
        joined = (today - timedelta(days=RNG.randint(30, 240))).isoformat()
        cur = con.execute(
            "INSERT INTO students(roll_no, name, student_class, course_code, batch, branch,"
            " parent_name, parent_phone, joined_on, status, fee_total, fee_plan, email)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (roll, f"{first} {last}", klass, course["code"], batch,
             inst["branches"][i % len(inst["branches"])], parent, _phone(10000000 + i * 137),
             joined, "active", total, plan, f"{first.lower()}.{last.lower()}@example.com"))
        sid = cur.lastrowid
        students.append({"id": sid, "roll": roll, "name": f"{first} {last}",
                         "course": course["code"], "total": total, "plan": plan,
                         "n_inst": n_inst, "parent_phone": _phone(10000000 + i * 137),
                         "parent": parent, "batch": batch})

        per = round(total / n_inst, -2)
        for k in range(n_inst):
            # Space installments ~60 days apart, first one already in the past.
            due = today + timedelta(days=(k * 60) - 45 + RNG.randint(-4, 4))
            amt = per if k < n_inst - 1 else total - per * (n_inst - 1)
            label = ("Full course fee" if n_inst == 1 else f"Installment {k + 1} of {n_inst}")
            status, paid_amt, paid_on = "pending", 0.0, None
            if k == 0:
                if RNG.random() < 0.88:
                    status, paid_amt, paid_on = "paid", amt, (due - timedelta(days=RNG.randint(0, 6))).isoformat()
                elif RNG.random() < 0.5:
                    status, paid_amt = "partial", round(amt * 0.5, -2)
            fcur = con.execute(
                "INSERT INTO fee_schedule(student_id, label, amount, due_date, status,"
                " paid_amount, paid_on, method) VALUES(?,?,?,?,?,?,?,?)",
                (sid, label, amt, due.isoformat(), status, paid_amt, paid_on,
                 RNG.choice(["UPI", "Cash", "Bank transfer"]) if paid_on else None))
            fee_id = fcur.lastrowid
            if paid_amt:
                con.execute(
                    "INSERT INTO payments(student_id, fee_schedule_id, amount, paid_on, method,"
                    " reference, recorded_by, receipt_sent) VALUES(?,?,?,?,?,?,?,1)",
                    (sid, fee_id, paid_amt, paid_on or today.isoformat(), "UPI",
                     f"UPI{RNG.randint(10**11, 10**12 - 1)}", "front-desk"))
            # Schedule the full reminder ladder for every unpaid fee.
            if status != "paid":
                reminders.queue_all_stages_for_fee(
                    con, {"id": fee_id, "student_id": sid, "due_date": due.isoformat()})

        # ---- tests + attendance ------------------------------------------
        subjects = SUBJECTS[course["code"]]
        names = TEST_NAMES[course["code"]]
        base_ability = RNG.uniform(0.45, 0.92)
        for j in range(RNG.randint(3, 5)):
            tname = names[j % len(names)]
            tdate = today - timedelta(days=7 * j + RNG.randint(0, 5))
            subj = subjects[j % len(subjects)]
            tot = 100.0 if "Mock" not in tname and "Prelim" not in tname else 720.0
            ability = min(0.98, max(0.25, base_ability + RNG.uniform(-0.08, 0.12) + j * 0.01))
            marks = round(tot * ability)
            bavg = round(min(96.0, max(30.0, RNG.gauss(63, 7))), 1)
            con.execute(
                "INSERT INTO tests(student_id, test_name, test_date, subject, marks_obtained,"
                " marks_total, batch_avg_pct, batch_topper_pct, rank_in_batch, teacher_remark)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, tname, tdate.isoformat(), subj, marks, tot, bavg,
                 round(min(99.0, bavg + RNG.uniform(12, 26)), 1),
                 RNG.randint(1, 42), RNG.choice(REMARKS)))
        # attendance: one deliberately low-attendance student for the alert demo
        low = (i == 3)
        for d in range(30):
            day = today - timedelta(days=d)
            if day.weekday() == 6:      # Sunday - no class
                continue
            r = RNG.random()
            if low:
                st = "present" if r < 0.58 else ("absent" if r < 0.92 else "late")
            else:
                st = "present" if r < 0.87 else ("absent" if r < 0.95 else ("late" if r < 0.98 else "leave"))
            con.execute("INSERT OR IGNORE INTO attendance(student_id, on_date, status)"
                        " VALUES(?,?,?)", (sid, day.isoformat(), st))
    con.commit()

    # ---- reminder showcase ----------------------------------------------
    # Seven extra fees pinned to the seven stage offsets so one tick fires all.
    showcase = []
    showcase_names = ["Krishna Pillai", "Ishaan Bhatt", "Rudra Shetty", "Kabir Saxena",
                      "Atharva Menon", "Dhruv Pandey", "Yash Trivedi"]
    for idx, st in enumerate(config.cfg()["fee_reminder_policy"]["stages"]):
        course = config.courses()[idx % len(config.courses())]
        full_name = showcase_names[idx % len(showcase_names)]
        first, last = full_name.split()
        parent = f"Mr. {RNG.choice(FIRST_M)} {last}"
        roll = f"SHOW-{2026}-{500 + idx}"
        ph = _phone(20000000 + idx * 991)
        cur = con.execute(
            "INSERT INTO students(roll_no, name, student_class, course_code, batch, branch,"
            " parent_name, parent_phone, joined_on, status, fee_total, fee_plan)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (roll, f"{first} {last}", course["classes"][0], course["code"],
             BATCHES[course["code"]][0], inst["branches"][0], parent, ph,
             (today - timedelta(days=90)).isoformat(), "active", float(course["fee_total"]),
             "3 installments"))
        sid = cur.lastrowid
        due = today - timedelta(days=int(st["offset_days"]))
        amt = float(round(course["fee_total"] / 3, -2))
        fcur = con.execute(
            "INSERT INTO fee_schedule(student_id, label, amount, due_date, status)"
            " VALUES(?,?,?,?, 'pending')",
            (sid, f"Installment {idx % 3 + 1} of 3", amt, due.isoformat()))
        fee_id = fcur.lastrowid
        # only the stage under test is queued today; later stages stay queued
        con.execute(
            "INSERT OR IGNORE INTO reminder_log(fee_schedule_id, student_id, stage, tone,"
            " scheduled_for, status) VALUES(?,?,?,?,?, 'queued')",
            (fee_id, sid, st["key"], st["tone"], today.isoformat()))
        showcase.append({"stage": st["key"], "student": f"{first} {last}", "roll": roll,
                         "phone": ph, "amount": amt, "due": due.isoformat()})
    con.commit()

    # ---- open leads ------------------------------------------------------
    lead_defs = [
        ("+919812345670", "Rohit Kamble", "11", "JEE", "07:00-10:30", "wants demo class this Saturday", 75),
        ("+919812345671", "Sneha Pawar", "12", "NEET", "16:00-19:30", "asked about fee discount, wants counsellor", 55),
        ("+919812345672", None, "10", "FOUNDATION", None, "asked course fees only", 20),
        ("+919812345673", "Imran Sheikh", "Dropper", "NEET", "07:00-10:30", "demo booked, father will visit", 80),
        ("+919812345674", "Kavita Jain", "9", "FOUNDATION", "18:15-20:15", "asked about batch timing", 30),
        ("+919812345675", None, None, None, None, "said hi, no details yet", 0),
    ]
    for ph, nm, kl, cc, bp, notes, sc in lead_defs:
        lead = store.upsert_lead(con, ph, name=nm, student_class=kl, course_code=cc,
                                 batch_pref=bp, notes=notes, status="contacted")
        store.upsert_lead(con, ph, score=sc, temperature="HOT" if sc >= 50 else ("WARM" if sc >= 25 else "COLD"))
        con.execute("INSERT OR IGNORE INTO conversations(phone, state, ctx, role) VALUES(?,?,?,?)",
                    (ph, "menu", "{}", "lead"))
    con.commit()

    return {"students": n_students + len(showcase), "showcase": showcase,
            "real_students": n_students, "leads": len(lead_defs)}


def kpi_snapshot(con) -> dict:
    c = store.collection_summary(con)
    rows = con.execute(
        "SELECT COUNT(*) n FROM fee_schedule WHERE status IN ('pending','partial')").fetchone()["n"]
    return {
        "students_active": con.execute("SELECT COUNT(*) n FROM students WHERE status='active'").fetchone()["n"],
        "open_leads": con.execute("SELECT COUNT(*) n FROM leads WHERE status NOT IN ('won','lost')").fetchone()["n"],
        "hot_leads": con.execute("SELECT COUNT(*) n FROM leads WHERE temperature='HOT' AND status NOT IN ('won','lost')").fetchone()["n"],
        "fees_pending_count": rows,
        "collected_today": c["collected_today"],
        "collected_month": c["collected_month"],
        "outstanding_total": c["outstanding_total"],
        "overdue_total": c["overdue_total"],
        "reminders_sent": con.execute("SELECT COUNT(*) n FROM reminder_log WHERE status='sent'").fetchone()["n"],
        "reminders_queued": con.execute("SELECT COUNT(*) n FROM reminder_log WHERE status='queued'").fetchone()["n"],
        "messages_out": con.execute("SELECT COUNT(*) n FROM messages WHERE direction='out'").fetchone()["n"],
        "alerts": con.execute("SELECT COUNT(*) n FROM alerts").fetchone()["n"],
    }