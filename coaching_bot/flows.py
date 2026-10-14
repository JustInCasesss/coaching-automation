"""Inbound conversation state machine.

`Engine.handle(phone, text, kind=...)` returns a list of outbound actions.
It is pure with respect to WhatsApp: no network, no globals. The caller decides
whether to actually send. That is what makes the CLI demo and the webhook share
one code path, and what makes it unit-testable.

States
------
new            -> greet, show main menu
menu           -> routing hub
adm_course     -> admission: which course
adm_class      -> admission: student's current class
adm_name       -> admission: student name
adm_batch      -> admission: batch preference
adm_done       -> admission captured; offer demo class
stu_menu       -> existing-parent hub
fee_query      -> fee status lookup
perf_query     -> performance report lookup
handoff        -> human counsellor requested
paid_confirm   -> parent claims payment
opted_out      -> STOP received
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import config, performance, store, whatsapp as wa

GREET_WORDS = ("hi", "hii", "hey", "hello", "helo", "namaste", "namaskar", "good morning",
               "good evening", "good afternoon", "start", "menu")
OPT_OUT_WORDS = ("stop", "unsubscribe", "band karo", "mat bhejo", "dont message", "don't message")
PAID_WORDS = ("paid", "payed", "done payment", "payment done", "paid kar diya", "transferred",
              "utr", "paid ho gaya")
HANDOFF_WORDS = ("counsellor", "counselor", "call me", "talk to", "talk to someone", "admission officer",
                 "office", "baat karni", "human", "agent", "staff")
FEE_WORDS = ("fee", "fees", "fees kitni", "payment", "installment", "instalment", "due", "pending",
             "outstanding", "balance", "kitna baki", "receipt")
PERF_WORDS = ("report", "performance", "marks", "score", "result", "progress", "test",
              "attendance", "rank", "how is", "kaisa", "report card")
ADMISSION_WORDS = ("admission", "admission lena", "enroll", "enrol", "join", "new batch",
                   "course", "coaching", "classes", "demo", "trial", "seat", "vacancy")

CLASS_RE = re.compile(r"\b(1[0-2]|[6-9])\b")
PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?([6-9]\d{9})")
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)


def _contains(text: str, words: tuple[str, ...]) -> bool:
    t = f" {(text or '').lower().strip()} "
    return any(f" {w} " in t or t.strip().startswith(w) or t.strip() == w for w in words)


def _d(value) -> "date":
    """Parse an ISO date string (or date) into a date."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def main_menu() -> dict:
    inst = config.institute()
    return wa.menu(
        f"Welcome to *{inst['name']}* - {inst['tagline']}.\n\n"
        "How can we help you today?",
        "Choose option",
        [{"title": "Admissions & Courses", "rows": [
            {"id": "ADM", "title": "New admission", "description": "Courses, fees, batch timings, demo class"},
            {"id": "FEE_STRUCT", "title": "Course fee structure", "description": "Fees & installment plans"},
            {"id": "DEMO", "title": "Book free demo class", "description": "2 days free, no commitment"},
        ]},
         {"title": "Existing Students", "rows": [
            {"id": "FEE_STATUS", "title": "My fee status", "description": "Dues, due date, receipt"},
            {"id": "PERF", "title": "Performance report", "description": "Marks, attendance, progress"},
            {"id": "PAID", "title": "I have paid", "description": "Confirm a payment you made"},
        ]},
         {"title": "Talk to us", "rows": [
            {"id": "HANDOFF", "title": "Talk to counsellor", "description": f"Call back within {inst['office_hours']}"},
            {"id": "VISIT", "title": "Visit a branch", "description": ", ".join(inst["branches"])},
        ]}],
    )


class Engine:
    def __init__(self, con):
        self.con = con
        self.inst = config.institute()

    # ---------------------------------------------------------------- helpers
    def _greeting(self) -> list[dict]:
        inst = self.inst
        return [
            wa.t(f"Namaste 🙏 Welcome to *{inst['name']}*.\n"
                 f"{inst['tagline']}\n\n"
                 f"🎁 {inst['offers']['demo_class']}\n"
                 f"🎁 {inst['offers']['early_bird']}\n\n"
                 f"Branches: {', '.join(inst['branches'])}\n"
                 f"Office hours: {inst['office_hours']}"),
            main_menu(),
        ]

    def _parent_menu(self) -> dict:
        return wa.menu("How can we help you today?", "Choose option",
                       [{"title": "Your options", "rows": [
                           {"id": "FEE_STATUS", "title": "Fee status",
                            "description": "Dues, due date, receipt"},
                           {"id": "PERF", "title": "Performance report",
                            "description": "Marks, attendance, progress"},
                           {"id": "PAID", "title": "I have paid",
                            "description": "Send payment proof"},
                           {"id": "HANDOFF", "title": "Talk to office",
                            "description": "We call you back within 15 min"},
                       ]}])

    def _course_block(self, code: str) -> str:
        c = config.course_by_code(code)
        if not c:
            return ""
        plans = "\n".join(f"   • {p}" for p in c["fee_plans"])
        timings = ", ".join(c["batch_timings"])
        return (f"*{c['label']}*\n"
                f"• Duration: {c['duration']}\n"
                f"• Total fee: {config.inr(c['fee_total'])}\n"
                f"• Payment options:\n{plans}\n"
                f"• Batch timings: {timings}\n"
                f"• Seats left this batch: {c['seats_left']}")

    def _resolve_student(self, phone: str) -> dict | None:
        studs = store.find_students_by_phone(self.con, phone)
        return studs[0] if studs else None

    def _fee_status_text(self, stu: dict) -> str:
        pend = store.pending_fees(self.con, stu["id"])
        if not pend:
            return (f"✅ *{stu['name']}* ({stu['roll_no']}) - no pending fees.\n"
                    f"All dues are clear. Thank you!")
        lines = []
        total = 0.0
        today = config.today_ist()
        for f in pend:
            amt = store.fee_outstanding(f)
            total += amt
            due = f["due_date"]
            d = _d(due)
            if d < today:
                tag = f"⚠️ OVERDUE by {(today - d).days} day(s)"
            elif d == today:
                tag = "📅 DUE TODAY"
            else:
                tag = f"due in {(d - today).days} day(s)"
            lines.append(f"• {f['label']}: *{config.inr(amt)}* - {config.fmt_date(d)} ({tag})")
        body = (f"Fee status for *{stu['name']}* ({stu['roll_no']}, {stu['batch']}):\n\n"
                + "\n".join(lines)
                + f"\n\n*Total payable: {config.inr(total)}*")
        if stu["fee_plan"]:
            body += f"\nPlan: {stu['fee_plan']}"
        body += (f"\n\nPay here: {self.inst['payment_link']}"
                 f"\nUPI: {self.inst['upi_id']}"
                 f"\n\nAfter paying, reply *PAID* with the screenshot or UTR number "
                 f"and we will send the receipt.")
        return body

    # ---------------------------------------------------------------- entry
    def handle(self, phone: str, text: str, kind: str = "text", name: str | None = None) -> list[dict]:
        phone = store.normalize_phone(phone)
        text = (text or "").strip()
        conv = store.get_conv(self.con, phone)
        if conv.get("opted_out"):
            if _contains(text, ("start", "unstop", "resume")):
                # Opt-in restores the conversation from scratch, so the parent
                # gets a real greeting back rather than a dead-end state.
                store.set_optout(self.con, phone, False)
                store.save_conv(self.con, phone, "new", conv["ctx"])
                conv = store.get_conv(self.con, phone)
                store.log_message(self.con, phone, "in", text, kind=kind)
                return self._s_new(phone, conv, text)
            store.save_conv(self.con, phone, "opted_out", conv["ctx"])
            return []
        store.log_message(self.con, phone, "in", text, kind=kind)

        if name and not conv["ctx"].get("wa_name"):
            conv["ctx"]["wa_name"] = name

        # Global interrupts work from any state.
        if _contains(text, OPT_OUT_WORDS):
            store.set_optout(self.con, phone, True)
            store.save_conv(self.con, phone, "opted_out", conv["ctx"])
            return [wa.t("You have been unsubscribed. You will not receive further "
                         "reminders on this number.\nReply *START* anytime to resume.")]
        if _contains(text, PAID_WORDS):
            return self._paid_claim(phone, conv, text)
        if _contains(text, HANDOFF_WORDS):
            return self._handoff(phone, conv, text, reason="parent requested a call")
        if _contains(text, PERF_WORDS) and self._resolve_student(phone):
            return self._performance(phone, conv, text)
        if _contains(text, FEE_WORDS) and self._resolve_student(phone):
            return self._fee_status(phone, conv, text)

        state = conv["state"]
        handler = {
            "new": self._s_new, "menu": self._s_menu,
            "adm_course": self._s_adm_course, "adm_class": self._s_adm_class,
            "adm_name": self._s_adm_name, "adm_batch": self._s_adm_batch,
            "adm_done": self._s_adm_done, "stu_menu": self._s_stu_menu,
            "fee_query": self._s_fee_query, "perf_query": self._s_perf_query,
            "handoff": self._s_handoff, "paid_confirm": self._s_paid_confirm,
            "opted_out": lambda p, c, t: [],
        }.get(state, self._s_new)
        return handler(phone, conv, text)

    # ---------------------------------------------------------------- states
    def _s_new(self, phone: str, conv: dict, text: str) -> list[dict]:
        stu = self._resolve_student(phone)
        if stu:
            # Known parent: one message, the parent menu. Do not also fire the
            # generic marketing greeting - that reads as a bot that forgot them.
            conv["ctx"]["student_id"] = stu["id"]
            store.save_conv(self.con, phone, "stu_menu", conv["ctx"], role="parent",
                            student_id=stu["id"])
            return [
                wa.t(f"Namaste 🙏\n\nWe have *{stu['name']}* ({stu['roll_no']}, "
                     f"{stu['batch']}) on record.\n\n"
                     f"Reply *FEE* for dues, *REPORT* for the latest performance "
                     f"report, or pick an option below."),
                self._parent_menu(),
            ]
        store.save_conv(self.con, phone, "menu", conv["ctx"], role="lead")
        store.upsert_lead(self.con, phone, name=conv["ctx"].get("wa_name"))
        return self._greeting()

    def _s_menu(self, phone: str, conv: dict, text: str) -> list[dict]:
        up = text.upper()
        if up in ("ADM", "DEMO", "FEE_STRUCT", "VISIT") or _contains(text, ADMISSION_WORDS):
            if up == "VISIT":
                return self._visit(phone, conv)
            if up == "FEE_STRUCT":
                return self._fee_structure(phone, conv)
            conv["ctx"]["intent"] = "demo" if up == "DEMO" or "demo" in text.lower() else "admission"
            store.save_conv(self.con, phone, "adm_course", conv["ctx"])
            return self._ask_course(conv)
        if up in ("FEE_STATUS", "PERF", "PAID"):
            return self.handle(phone, {"FEE_STATUS": "FEE", "PERF": "REPORT", "PAID": "PAID"}[up])
        if _contains(text, GREET_WORDS):
            return [main_menu()]
        return [wa.t("I did not catch that. Please pick an option below 👇"), main_menu()]

    def _ask_course(self, conv: dict) -> list[dict]:
        rows = [{"id": f"COURSE_{c['code']}", "title": c["label"][:24],
                 "description": f"{config.inr(c['fee_total'])} | {c['duration']} | {c['seats_left']} seats left"}
                for c in config.courses()]
        return [wa.menu("Which course are you enquiring about?",
                        "Select course", [{"title": "Available courses", "rows": rows[:10]}])]

    def _s_adm_course(self, phone: str, conv: dict, text: str) -> list[dict]:
        code = None
        up = text.upper()
        if up.startswith("COURSE_"):
            code = up.replace("COURSE_", "")
        else:
            c = config.course_by_label(text)
            code = c["code"] if c else None
        if not code:
            return [wa.t("Please choose from the list below 👇"), *self._ask_course(conv)]
        conv["ctx"]["course_code"] = code
        store.upsert_lead(self.con, phone, course_code=code)
        store.save_conv(self.con, phone, "adm_class", conv["ctx"])
        c = config.course_by_code(code)
        rows = [{"id": f"CLASS_{cl}", "title": f"Class {cl}", "description": ""} for cl in c["classes"]]
        return [wa.menu(f"Got it - *{c['label']}*.\n\nWhich class is the student in currently?",
                        "Select class", [{"title": "Current class", "rows": rows[:10]}])]

    def _s_adm_class(self, phone: str, conv: dict, text: str) -> list[dict]:
        val = text.upper().replace("CLASS_", "").strip()
        if val not in ("8", "9", "10", "11", "12", "DROPPER"):
            m = CLASS_RE.search(text)
            if not m:
                return [wa.t("Please tell me the current class - 8, 9, 10, 11, 12 or Dropper.")]
            val = m.group(1)
        conv["ctx"]["student_class"] = val
        store.upsert_lead(self.con, phone, student_class=val)
        store.save_conv(self.con, phone, "adm_name", conv["ctx"])
        return [wa.t("Thank you. What is the *student's full name*?\n"
                     "(This helps us check scholarship and seat availability.)")]

    def _s_adm_name(self, phone: str, conv: dict, text: str) -> list[dict]:
        name = text.strip().title()[:80] or conv["ctx"].get("wa_name") or "Student"
        conv["ctx"]["student_name"] = name
        store.upsert_lead(self.con, phone, name=name)
        store.save_conv(self.con, phone, "adm_batch", conv["ctx"])
        c = config.course_by_code(conv["ctx"].get("course_code") or "")
        rows = [{"id": f"BATCH_{i}", "title": bt, "description": "Preferred batch"}
                for i, bt in enumerate((c or {}).get("batch_timings", ["Morning", "Evening"]))]
        rows.append({"id": "BATCH_ANY", "title": "Any / decide later", "description": ""})
        return [wa.t(f"Noted, *{name}*."),
                wa.menu("Which batch timing suits you?", "Select timing",
                        [{"title": "Batch timings", "rows": rows[:10]}])]

    def _s_adm_batch(self, phone: str, conv: dict, text: str) -> list[dict]:
        pref = text.replace("BATCH_", "").strip()
        c = config.course_by_code(conv["ctx"].get("course_code") or "")
        if pref.isdigit() and c:
            idx = int(pref)
            pref = c["batch_timings"][idx] if idx < len(c["batch_timings"]) else "Any"
        if pref.upper() == "ANY":
            pref = "Flexible"
        conv["ctx"]["batch_pref"] = pref
        store.upsert_lead(self.con, phone, batch_pref=pref)
        store.save_conv(self.con, phone, "adm_done", conv["ctx"])
        return self._adm_summary(phone, conv)

    def _adm_summary(self, phone: str, conv: dict) -> list[dict]:
        ctx = conv["ctx"]
        code = ctx.get("course_code") or ""
        lead = store.upsert_lead(self.con, phone, name=ctx.get("student_name"),
                                 course_code=code, student_class=ctx.get("student_class"),
                                 batch_pref=ctx.get("batch_pref"), status="contacted")
        score, temp = store.score_lead(lead)
        store.upsert_lead(self.con, phone, score=score, temperature=temp)
        body = ("Here is what I have noted:\n\n"
                f"• Student: *{ctx.get('student_name', '-')}*\n"
                f"• Class: *{ctx.get('student_class', '-')}*\n"
                f"• Course: *{(config.course_by_code(code) or {}).get('label', code)}*\n"
                f"• Preferred batch: *{ctx.get('batch_pref', '-')}*\n\n"
                + self._course_block(code)
                + f"\n\n🎁 *{self.inst['offers']['demo_class']}*"
                  f"\n🎁 {self.inst['offers']['early_bird']}")
        return [wa.t(body),
                wa.buttons("Would you like to book the free demo class?",
                           [("BOOK_DEMO", "Book free demo"), ("FEE_STRUCT", "Fee details"),
                            ("HANDOFF", "Talk to counsellor")])]

    def _s_adm_done(self, phone: str, conv: dict, text: str) -> list[dict]:
        up = text.upper()
        if up in ("BOOK_DEMO",) or "demo" in text.lower() or "yes" in text.lower() or "haan" in text.lower():
            return self._handoff(phone, conv, text, reason="wants free demo class", hot=True,
                                 extra="🎯 Demo class slot blocked for you.")
        if up == "FEE_STRUCT":
            return self._fee_structure(phone, conv)
        if up == "HANDOFF":
            return self._handoff(phone, conv, text, reason="wants counsellor after fee details")
        return [wa.buttons("Shall I book the free demo class for you?",
                           [("BOOK_DEMO", "Book free demo"), ("FEE_STRUCT", "Fee details"),
                            ("HANDOFF", "Talk to counsellor")])]

    def _fee_structure(self, phone: str, conv: dict) -> list[dict]:
        code = conv["ctx"].get("course_code")
        if code:
            store.save_conv(self.con, phone, "adm_done", conv["ctx"])
            return [wa.t(self._course_block(code)),
                    wa.buttons("Want to book the free demo class?",
                               [("BOOK_DEMO", "Book free demo"), ("HANDOFF", "Talk to counsellor")])]
        blocks = "\n\n".join(self._course_block(c["code"]) for c in config.courses())
        store.save_conv(self.con, phone, "adm_course", conv["ctx"])
        return [wa.t("*Fee structure*\n\n" + blocks
                     + f"\n\n🎁 {self.inst['offers']['referral']}"
                       f"\n🎁 {self.inst['offers']['sibling']}"),
                *self._ask_course(conv)]

    def _visit(self, phone: str, conv: dict) -> list[dict]:
        inst = self.inst
        store.save_conv(self.con, phone, "menu", conv["ctx"])
        return [wa.t(f"*{inst['name']}* branches:\n"
                     f"• {inst['branches'][0]} - Main branch\n"
                     f"• {inst['branches'][1]} - Second branch\n\n"
                     f"Office hours: {inst['office_hours']}\n"
                     f"Phone: {inst['phone']}\n"
                     f"Walk-ins welcome. Bring the student's last report card for a free "
                     f"aptitude check.")]

    # ------------------------------------------------------- existing parents
    def _s_stu_menu(self, phone: str, conv: dict, text: str) -> list[dict]:
        up = text.upper()
        if up in ("FEE_STATUS", "FEE") or _contains(text, FEE_WORDS):
            return self._fee_status(phone, conv, text)
        if up in ("PERF", "REPORT") or _contains(text, PERF_WORDS):
            return self._performance(phone, conv, text)
        if up in ("PAID",):
            return self._paid_claim(phone, conv, text)
        if _contains(text, GREET_WORDS) or not text:
            return [wa.menu("How can we help you today?", "Choose option",
                            [{"title": "Your options", "rows": [
                                {"id": "FEE_STATUS", "title": "Fee status", "description": "Dues, due date, receipt"},
                                {"id": "PERF", "title": "Performance report", "description": "Marks & attendance"},
                                {"id": "PAID", "title": "I have paid", "description": "Send payment proof"},
                                {"id": "HANDOFF", "title": "Talk to office", "description": "We call you back"},
                            ]}])]
        return self._handoff(phone, conv, text, reason="unrecognised message from parent")

    def _fee_status(self, phone: str, conv: dict, text: str) -> list[dict]:
        stu = self._resolve_student(phone)
        if not stu:
            return self._s_new(phone, conv, text)
        store.save_conv(self.con, phone, "stu_menu", conv["ctx"], role="parent", student_id=stu["id"])
        return [wa.t(self._fee_status_text(stu)),
                wa.buttons("Anything else?", [("PAID", "I have paid"), ("PERF", "Performance"),
                                             ("HANDOFF", "Talk to office")])]

    def _s_fee_query(self, phone: str, conv: dict, text: str) -> list[dict]:
        return self._fee_status(phone, conv, text)

    def _performance(self, phone: str, conv: dict, text: str) -> list[dict]:
        stu = self._resolve_student(phone)
        if not stu:
            return self._s_new(phone, conv, text)
        store.save_conv(self.con, phone, "stu_menu", conv["ctx"], role="parent", student_id=stu["id"])
        summary = performance.performance_message(self.con, stu["id"])
        return [wa.t(summary),
                wa.buttons("Need anything else?", [("FEE_STATUS", "Fee status"),
                                                   ("HANDOFF", "Talk to teacher"),
                                                   ("PERF", "Full report")])]

    def _s_perf_query(self, phone: str, conv: dict, text: str) -> list[dict]:
        return self._performance(phone, conv, text)

    # ---------------------------------------------------------------- actions
    def _handoff(self, phone: str, conv: dict, text: str, reason: str = "",
                 hot: bool = False, extra: str = "") -> list[dict]:
        lead = store.upsert_lead(self.con, phone,
                                 name=conv["ctx"].get("student_name") or conv["ctx"].get("wa_name"),
                                 course_code=conv["ctx"].get("course_code"),
                                 student_class=conv["ctx"].get("student_class"),
                                 batch_pref=conv["ctx"].get("batch_pref"),
                                 status="demo_booked" if hot else "contacted",
                                 notes=(reason + (" demo" if hot else "")))
        if hot:
            store.upsert_lead(self.con, phone, score=max(lead.get("score") or 0, 60), temperature="HOT")
        else:
            score, temp = store.score_lead(lead)
            store.upsert_lead(self.con, phone, score=score, temperature=temp)
        lead = store.upsert_lead(self.con, phone)
        store.save_conv(self.con, phone, "handoff", conv["ctx"], role="lead")
        alert = (f"🔥 HOT LEAD - {lead.get('name') or 'Unknown'}\n"
                 f"Phone: {phone}\n"
                 f"Course: {(config.course_by_code(lead.get('course_code') or '') or {}).get('label', lead.get('course_code') or '-')}\n"
                 f"Class: {lead.get('student_class') or '-'}\n"
                 f"Batch pref: {lead.get('batch_pref') or '-'}\n"
                 f"Score: {lead.get('score')} ({lead.get('temperature')})\n"
                 f"Reason: {reason}\n"
                 f"Action: call back within 15 minutes.")
        store.log_alert(self.con, "hot_lead" if lead.get("temperature") == "HOT" else "handoff",
                        alert, phone=phone)
        wa.send(self.con, self.inst["counsellor_phone"],
                wa.t("🔔 *Lead alert*\n" + alert))
        conv["ctx"]["callback_asked"] = True
        store.save_conv(self.con, phone, "handoff", conv["ctx"])
        body = (extra + "\n\n" if extra else "")
        body += (f"A counsellor from *{self.inst['name']}* will call you back "
                 f"within 15 minutes during office hours ({self.inst['office_hours']}).")
        return [wa.t(body),
                wa.buttons("When should we call?", [("CALL_NOW", "Call me now"),
                                                    ("CALL_EVENING", "Call in evening"),
                                                    ("CALL_TOMORROW", "Call tomorrow")])]

    def _s_handoff(self, phone: str, conv: dict, text: str) -> list[dict]:
        up = text.upper()
        slot = {"CALL_NOW": "immediately", "CALL_EVENING": "this evening (5-8 PM)",
                "CALL_TOMORROW": "tomorrow morning"}.get(up)
        if slot:
            conv["ctx"]["callback_pref"] = slot
            lead = store.upsert_lead(self.con, phone, callback_pref=slot)
            score, temp = store.score_lead(lead)
            store.upsert_lead(self.con, phone, score=score, temperature=temp)
            store.log_alert(self.con, "callback_slot",
                            f"Callback slot set: {slot} for {phone} ({lead.get('name') or 'lead'})",
                            phone=phone)
            return [wa.t(f"Done ✅ - we will call you *{slot}*.\n"
                         f"If you miss the call, reply here anytime.")]
        return [wa.t("Noted. A counsellor will call you back shortly.")]

    def _paid_claim(self, phone: str, conv: dict, text: str) -> list[dict]:
        stu = self._resolve_student(phone)
        who = f"{stu['name']} ({stu['roll_no']})" if stu else f"unknown student @ {phone}"
        store.log_alert(self.con, "payment_claim",
                        f"💰 Payment claimed by parent of {who}\n"
                        f"Message: \"{text[:200]}\"\n"
                        f"Action: verify in bank/UPI statement, then mark paid and send receipt.",
                        phone=phone)
        wa.send(self.con, self.inst["accounts_alert_phone"],
                wa.t(f"💰 *Payment claim*\nParent of {who} says payment is done.\n"
                     f"Reply: \"{text[:200]}\"\nVerify and mark paid."))
        store.save_conv(self.con, phone, "paid_confirm", conv["ctx"],
                        role="parent", student_id=stu["id"] if stu else None)
        return [wa.t("Thank you! 🙏\n\nPlease send the *payment screenshot* or the *UTR / "
                     "transaction reference number* here, and our accounts team will "
                     "verify and send you the receipt within 30 minutes.")]

    def _s_paid_confirm(self, phone: str, conv: dict, text: str) -> list[dict]:
        up = text.upper()
        if up in ("PAYMENT_PROOF_IMAGE", "PAYMENT_PROOF_DOCUMENT"):
            return [wa.t("Received ✅ Our accounts team will verify and send the receipt shortly.")]
        return [wa.t("Received ✅ Accounts team will confirm within 30 minutes.")]