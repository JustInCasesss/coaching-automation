"""Fee reminder engine: queueing, quiet hours, dedupe, escalation, receipts.

Design notes that matter in production
--------------------------------------
1. Meta's 24-hour rule: you may only send free-form text within 24h of the
   parent's last inbound message. Outside that window you MUST use a
   pre-approved template. `pick_channel()` decides per message, so the same
   reminder copy works either way and nothing gets silently rejected.
2. Quiet hours: reminders are queued, not dropped, and released on the next
   tick inside office hours.
3. Dedupe: reminder_log has UNIQUE(fee_schedule_id, stage) - a stage can never
   be sent twice for the same fee, even if the scheduler double-fires.
4. Reply-pause: once a parent replies, reminders for that fee stop until the
   next stage, so the bot never nags into an open conversation.
"""
from __future__ import annotations

from datetime import timedelta

from . import config, store, whatsapp as wa

STAGE_ORDER = ["T-7", "T-3", "T-1", "D0", "D+3", "D+7", "D+15"]

# Map stage -> (Meta template name, tone key). Template names must exist and be
# approved in WhatsApp Manager before go-live (see docs/TEMPLATES_FOR_META.md).
STAGE_TEMPLATE = {
    "T-7": "fee_upcoming_7d",
    "T-3": "fee_reminder_3d",
    "T-1": "fee_due_tomorrow",
    "D0": "fee_due_today",
    "D+3": "fee_overdue_3d",
    "D+7": "fee_overdue_7d",
    "D+15": "fee_final_notice",
}
STAGE_ESCALATES = {"D+7", "D+15"}


def stages() -> list[dict]:
    return config.cfg()["fee_reminder_policy"]["stages"]


def stage_cfg(key: str) -> dict | None:
    for s in stages():
        if s["key"] == key:
            return s
    return None


def within_24h_window(con, phone: str) -> bool:
    """True if the parent messaged us in the last 24 hours (free-form allowed)."""
    return _replied_within(con, phone, 24)


def _replied_within(con, phone: str, hours: float) -> bool:
    from datetime import datetime
    row = con.execute(
        "SELECT last_in_at FROM conversations WHERE phone=?", (store.normalize_phone(phone),)
    ).fetchone()
    if not row or not row["last_in_at"]:
        return False
    last = datetime.fromisoformat(row["last_in_at"])
    return (config.now_ist() - last) < timedelta(hours=hours)


def build_body(stage: str, fee: dict) -> str:
    """Human-readable reminder copy. Uses *bold* and emoji the way parents read."""
    inst = config.institute()
    name = config.given_name(fee.get("parent_name"), "Parent")
    stu = fee.get("student_name") or "your child"
    amount = config.inr(store.fee_outstanding(fee))
    due = config.fmt_date(fee["due_date"])
    label = fee["label"]
    pay = inst["payment_link"]
    upi = inst["upi_id"]
    tail = (f"\n\nPay online: {pay}\nUPI: {upi}\n"
            f"Already paid? Reply *PAID* with the screenshot or UTR.")

    if stage == "T-7":
        return (f"Namaste {name} 🙏\n\nA gentle heads-up: the *{label}* fee for "
                f"*{stu}* is due on *{due}*. Amount payable is *{amount}*.\n"
                f"No action needed if you have already scheduled the payment.{tail}")
    if stage == "T-3":
        return (f"Namaste {name} 🙏\n\nReminder: *{label}* fee for *{stu}* is due on "
                f"*{due}* - *{amount}*.\n\nPaying on time keeps the batch schedule "
                f"undisturbed.{tail}")
    if stage == "T-1":
        return (f"Namaste {name} 🙏\n\n*{stu}*'s *{label}* fee (*{amount}*) is due "
                f"*tomorrow, {due}*.\n\nPlease complete the payment to avoid a "
                f"follow-up call.{tail}")
    if stage == "D0":
        return (f"Namaste {name} 🙏\n\nToday is the due date for *{stu}*'s *{label}* "
                f"fee - *{amount}*.\n\nIf already paid, please reply *PAID* so we can "
                f"send the receipt.{tail}")
    if stage == "D+3":
        days = abs((config.today_ist() - _d(fee["due_date"])).days)
        return (f"Namaste {name} 🙏\n\n*{stu}*'s *{label}* fee of *{amount}* is now "
                f"*{days} day(s) overdue* (due {due}).\n\nWe would like to resolve this "
                f"without escalating it. If there is a difficulty, reply *CALL* and we "
                f"will work out a plan.{tail}")
    if stage == "D+7":
        days = abs((config.today_ist() - _d(fee["due_date"])).days)
        return (f"Namaste {name},\n\n*{stu}*'s *{label}* fee of *{amount}* remains unpaid "
                f"and is *{days} days overdue* (due {due}).\n\nAs per the admission terms, "
                f"a late fee of Rs.500 applies after 7 days and class access may be "
                f"paused. Please pay today, or reply *CALL* to speak with the accounts "
                f"team about an installment.{tail}")
    if stage == "D+15":
        days = abs((config.today_ist() - _d(fee["due_date"])).days)
        return (f"Namaste {name},\n\n*Final notice.* *{stu}*'s *{label}* fee of "
                f"*{amount}* is *{days} days overdue* (due {due}).\n\nPlease clear the "
                f"dues within 3 working days to keep the seat. Reply *CALL* if you need "
                f"an installment plan.{tail}")
    return f"Fee reminder for {stu}: {amount} due {due}.{tail}"


def _d(value):
    from datetime import date
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def template_components(fee: dict) -> list[dict]:
    """Body params in the exact order the approved templates expect."""
    inst = config.institute()
    return [{"type": "body", "parameters": [
        {"type": "text", "text": config.given_name(fee.get("parent_name"), "Parent")},
        {"type": "text", "text": fee.get("student_name") or "your child"},
        {"type": "text", "text": fee["label"]},
        {"type": "text", "text": config.inr(store.fee_outstanding(fee)).replace("Rs.", "")},
        {"type": "text", "text": config.fmt_date(fee["due_date"])},
    ]}, {"type": "button", "sub_type": "url", "index": 0,
         "parameters": [{"type": "text", "text": inst["payment_link"]}]}]


def pick_channel(con, phone: str, stage: str, fee: dict) -> dict:
    """Return the outbound action: free-form text inside 24h, else template."""
    if within_24h_window(con, phone):
        return wa.t(build_body(stage, fee))
    return wa.template(STAGE_TEMPLATE[stage], config.WA_TEMPLATE_LANG,
                       build_body(stage, fee), template_components(fee))


def current_stage_for(due_date, today=None) -> dict | None:
    """The one stage that applies right now for a fee.

    A fee has exactly one current stage: the stage whose offset is the largest
    that is still <= the fee's current age. This is what stops an overdue fee
    from firing its whole ladder at once when the scheduler has been down, and
    it means a fee that is 20 days late gets the D+15 notice, not D+7 then D+15.
    """
    today = today or config.today_ist()
    age = (today - _d(due_date)).days          # negative = due in the future
    eligible = [s for s in stages() if int(s["offset_days"]) <= age]
    if not eligible:
        return None                            # earlier than the first stage
    return max(eligible, key=lambda s: int(s["offset_days"]))


def unpaid_fees(con) -> list[dict]:
    """Every unpaid fee with the student fields the reminder copy needs."""
    rows = con.execute(
        "SELECT f.*, s.name AS student_name, s.parent_name, s.parent_phone, s.roll_no,"
        " s.batch, s.course_code"
        " FROM fee_schedule f JOIN students s ON s.id=f.student_id"
        " WHERE f.status IN ('pending','partial') AND f.paused=0 AND s.status='active'"
        " ORDER BY f.due_date").fetchall()
    return [dict(r) for r in rows]


def queue(con, horizon_days: int = 120) -> int:
    """Materialise the reminder ladder for every unpaid fee (audit trail).

    Idempotent - UNIQUE(fee_schedule_id, stage) makes re-running safe. Rows are
    created up front so the operator can see the whole schedule for a fee, then
    dispatch() decides which single row actually fires today.
    """
    created = 0
    for fee in unpaid_fees(con):
        for st in stages():
            if int(st["offset_days"]) > horizon_days:
                continue
            fire_on = (_d(fee["due_date"]) + timedelta(days=int(st["offset_days"]))).isoformat()
            cur = con.execute(
                "INSERT OR IGNORE INTO reminder_log(fee_schedule_id, student_id, stage,"
                " tone, scheduled_for, status) VALUES(?,?,?,?,?, 'queued')",
                (fee["id"], fee["student_id"], st["key"], st["tone"], fire_on))
            created += cur.rowcount if cur.rowcount > 0 else 0
    con.commit()
    return created


def queue_all_stages_for_fee(con, fee: dict) -> None:
    """Used at admission time so a new student's whole plan is scheduled at once."""
    for st in stages():
        fire_on = (_d(fee["due_date"]) + timedelta(days=int(st["offset_days"]))).isoformat()
        con.execute(
            "INSERT OR IGNORE INTO reminder_log(fee_schedule_id, student_id, stage, tone,"
            " scheduled_for, status) VALUES(?,?,?,?,?, 'queued')",
            (fee["id"], fee["student_id"], st["key"], st["tone"], fire_on))
    con.commit()


def plan(con, today=None) -> list[dict]:
    """Fees that need exactly one reminder right now, with that stage attached.

    Also retires any older queued stages for the same fee as 'superseded', so a
    backlog can never be dumped on a parent in one burst.
    """
    today = today or config.today_ist()
    max_r = int(config.cfg()["fee_reminder_policy"].get("max_reminders_per_fee", 7))
    out = []
    for fee in unpaid_fees(con):
        st = current_stage_for(fee["due_date"], today)
        if not st:
            continue
        if int(fee.get("remind_count") or 0) >= max_r:
            continue
        rows = con.execute(
            "SELECT id, stage, status FROM reminder_log WHERE fee_schedule_id=?",
            (fee["id"],)).fetchall()
        by_stage = {r["stage"]: dict(r) for r in rows}
        cur_row = by_stage.get(st["key"])
        if cur_row and cur_row["status"] in ("sent", "skipped", "failed"):
            continue
        # retire anything older that never went out
        for other_key, other in by_stage.items():
            if other["status"] != "queued":
                continue
            o_stage = stage_cfg(other_key)
            if o_stage and int(o_stage["offset_days"]) < int(st["offset_days"]):
                con.execute("UPDATE reminder_log SET status='skipped',"
                            " reason='superseded by ' || ? WHERE id=?", (st["key"], other["id"]))
        con.commit()
        if cur_row is None:
            fire_on = (_d(fee["due_date"]) + timedelta(days=int(st["offset_days"]))).isoformat()
            con.execute(
                "INSERT OR IGNORE INTO reminder_log(fee_schedule_id, student_id, stage, tone,"
                " scheduled_for, status) VALUES(?,?,?,?,?, 'queued')",
                (fee["id"], fee["student_id"], st["key"], st["tone"], fire_on))
            con.commit()
            cur_row = {"id": con.execute(
                "SELECT id FROM reminder_log WHERE fee_schedule_id=? AND stage=?",
                (fee["id"], st["key"])).fetchone()["id"], "stage": st["key"], "status": "queued"}
        item = dict(fee)
        item["_stage"] = st
        item["_log_id"] = cur_row["id"]
        out.append(item)
    return out


def due_now(con, include_quiet_held: bool = True) -> list[dict]:
    """Kept for API compatibility - now returns the single-stage plan."""
    return plan(con)


def dispatch(con, force_quiet: bool = False, dry_run: bool | None = None) -> dict:
    """Send exactly one reminder per fee that is due for one. Returns a report."""
    if dry_run is None:
        dry_run = config.demo_mode()
    quiet = config.in_quiet_hours() and not force_quiet
    stats = {"sent": 0, "held": 0, "skipped": 0, "escalated": 0, "failed": 0, "details": []}
    for fee in plan(con):
        st = fee["_stage"]
        stage = st["key"]
        log_id = fee["_log_id"]
        phone = fee["parent_phone"]
        conv = store.get_conv(con, phone)
        if conv.get("opted_out"):
            con.execute("UPDATE reminder_log SET status='skipped', reason='opted_out' WHERE id=?",
                        (log_id,))
            con.commit()
            stats["skipped"] += 1
            continue
        policy = config.cfg()["fee_reminder_policy"]
        # pause_on_reply: never drop a fee reminder on top of an open
        # conversation. This applies to every stage including escalations - if
        # the parent is already talking to us, a final notice is noise. Staff
        # still get the escalation, so the signal is never lost.
        paused = False
        if policy.get("pause_on_reply"):
            hrs = float(policy.get("reply_pause_hours", 12))
            paused = _replied_within(con, phone, hrs)

        if quiet and stage not in STAGE_ESCALATES and not paused:
            con.execute("UPDATE reminder_log SET status='held_quiet_hours',"
                        " reason='outside office hours' WHERE id=?", (log_id,))
            con.commit()
            stats["held"] += 1
            continue
        if paused:
            con.execute("UPDATE reminder_log SET status='held_conversation',"
                        " reason='parent replied recently' WHERE id=?", (log_id,))
            con.commit()
            stats["held"] += 1
            if stage in STAGE_ESCALATES:
                escalate(con, fee, stage, held=True)
                stats["escalated"] += 1
            stats["details"].append({"phone": phone, "student": fee["student_name"],
                                     "stage": stage, "status": "held_conversation",
                                     "channel": "-", "amount": store.fee_outstanding(fee)})
            continue
        out = pick_channel(con, phone, stage, fee)
        res = wa.send(con, phone, out)
        con.execute(
            "UPDATE reminder_log SET status=?, sent_at=?, body=?, reason=? WHERE id=?",
            ("sent" if res["ok"] else "failed", store.ts(), out.get("body"),
             res.get("error"), log_id))
        con.execute(
            "UPDATE fee_schedule SET last_reminder_stage=?, last_reminder_on=?,"
            " remind_count=COALESCE(remind_count,0)+1 WHERE id=?",
            (stage, store.ts(), fee["id"]))
        con.commit()
        stats["sent" if res["ok"] else "failed"] += 1
        stats["details"].append({"phone": phone, "student": fee["student_name"],
                                 "stage": stage, "status": res["status"],
                                 "channel": out["kind"], "amount": store.fee_outstanding(fee)})
        if stage in STAGE_ESCALATES:
            escalate(con, fee, stage)
            stats["escalated"] += 1
    return stats


def escalate(con, row: dict, stage: str, held: bool = False) -> None:
    """Tell the owner/accounts team about a fee that has crossed the line.

    Fires at most once per fee per stage - `dedupe_key` is enforced by a unique
    index, so a held or retried reminder cannot re-notify accounts every tick.
    """
    inst = config.institute()
    days = abs((config.today_ist() - _d(row["due_date"])).days)
    dedupe_key = f"escalation:{row['id']}:{stage}"
    if store.alert_exists(con, dedupe_key):
        return
    body = (f"🚨 *Fee escalation - {stage}*\n"
            f"Student: {row['student_name']} ({row['roll_no']}, {row['batch']})\n"
            f"Parent: {row['parent_name'] or '-'} | {row['parent_phone']}\n"
            f"Fee: {row['label']} | {config.inr(store.fee_outstanding(row))}\n"
            f"Due: {config.fmt_date(row['due_date'])} | *{days} days overdue*\n"
            f"Reminders sent: {row.get('remind_count', 0)}\n"
            + ("Parent message *held* - they are active in the chat right now, so the "
               "notice was not sent. Call them instead.\n" if held else "")
            + "Next: accounts call + installment offer.")
    if store.log_alert(con, "overdue_escalation", body, phone=row["parent_phone"],
                       dedupe_key=dedupe_key) is None:
        return
    wa.send(con, inst["accounts_alert_phone"], wa.t(body))


def send_receipt(con, student_id: int, fee_id: int | None, amount: float,
                 method: str = "UPI", reference: str | None = None) -> dict:
    """Payment confirmation to the parent. Fires the moment accounts marks paid."""
    stu = con.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if not stu:
        return {"ok": False, "error": "student not found"}
    stu = dict(stu)
    pend = store.pending_fees(con, student_id)
    outstanding = sum(store.fee_outstanding(f) for f in pend)
    label = ""
    if fee_id:
        fr = con.execute("SELECT label FROM fee_schedule WHERE id=?", (fee_id,)).fetchone()
        label = f" towards {fr['label']}" if fr else ""
    body = (f"✅ *Payment received*\n\n"
            f"Student: *{stu['name']}* ({stu['roll_no']})\n"
            f"Amount: *{config.inr(amount)}*{label}\n"
            f"Method: {method}" + (f"\nReference: {reference}" if reference else "") + "\n"
            f"Date: {config.fmt_date(config.today_ist())}\n"
            f"Received by: {config.institute()['name']}\n\n"
            + (f"*Remaining balance: {config.inr(outstanding)}*\n\n" if outstanding > 0.5
               else "🎉 All dues are now clear. Thank you!\n\n")
            + f"This message is your payment acknowledgement. For a GST invoice, "
              f"reply *INVOICE*.\n{config.institute()['branding']['signature']}")
    out = wa.t(body)
    if not within_24h_window(con, stu["parent_phone"]):
        out = wa.template("payment_receipt_v1", config.WA_TEMPLATE_LANG, body,
                          [{"type": "body", "parameters": [
                              {"type": "text", "text": stu["name"]},
                              {"type": "text", "text": config.inr(amount).replace("Rs.", "")},
                              {"type": "text", "text": stu["roll_no"]}]}])
    res = wa.send(con, stu["parent_phone"], out)
    con.execute("UPDATE payments SET receipt_sent=1 WHERE student_id=? AND receipt_sent=0",
                (student_id,))
    con.commit()
    return res


def run(con, force_quiet: bool = False, dry_run: bool | None = None) -> dict:
    """Full tick: queue new stages, then dispatch everything due."""
    queued = queue(con)
    report = dispatch(con, force_quiet=force_quiet, dry_run=dry_run)
    report["queued"] = queued
    return report