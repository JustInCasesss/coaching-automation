"""Idempotent fee-reminder runner."""
from __future__ import annotations

from datetime import datetime
import os
from zoneinfo import ZoneInfo

from . import db
from .config import is_live_whatsapp
from .whatsapp import send_template


def _message_parameters(fee: dict, stage: str, institute_name: str) -> list[str]:
    amount = f"₹{float(fee['amount']):,.2f}"
    return [fee["full_name"], amount, fee["due_date"], stage, institute_name]


def run_reminders(*, dry_run: bool = True, force: bool = False, now: datetime | None = None) -> dict:
    zone = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
    current = now.astimezone(zone) if now else datetime.now(zone)
    if not force and not (9 <= current.hour < 20):
        return {
            "ok": True, "dry_run": dry_run, "sent": 0, "failed": 0, "skipped": 0,
            "reason": "quiet_hours", "message": "Reminder window is 09:00-20:00 local time.",
        }

    template = os.getenv("WHATSAPP_REMINDER_TEMPLATE", "").strip()
    if is_live_whatsapp() and not template:
        return {
            "ok": False, "dry_run": dry_run, "sent": 0, "failed": 0, "skipped": 0,
            "reason": "template_not_configured",
            "message": "Live reminders require WHATSAPP_REMINDER_TEMPLATE to name an approved Meta template.",
        }

    try:
        from .config import load_institute_config
        institute_name = str(load_institute_config().get("institute_name", "Your Institute"))
    except RuntimeError:
        institute_name = "Your Institute"

    candidates = db.fee_reminder_candidates(current.date())
    result = {"ok": True, "dry_run": dry_run, "sent": 0, "failed": 0, "skipped": 0, "items": []}
    for fee in candidates:
        item = {
            "fee_id": fee["id"], "roll_no": fee["roll_no"], "stage": fee["stage"],
            "due_date": fee["due_date"], "status": "preview" if dry_run else "sending",
        }
        if dry_run:
            result["items"].append(item)
            continue
        if not db.claim_reminder(fee["id"], fee["stage"]):
            result["skipped"] += 1
            item["status"] = "skipped_duplicate"
            result["items"].append(item)
            continue
        try:
            response = send_template(
                fee["parent_phone"], template,
                os.getenv("WHATSAPP_TEMPLATE_LANG", "en_US"),
                _message_parameters(fee, fee["stage"], institute_name),
            )
            status = "simulated" if response.get("mode") == "dry_run" else "sent"
            db.finish_reminder(fee["id"], fee["stage"], status)
            result["sent"] += 1
            item["status"] = status
        except Exception as exc:
            db.finish_reminder(fee["id"], fee["stage"], "failed", str(exc))
            result["failed"] += 1
            item["status"] = "failed"
            item["error"] = str(exc)[:250]
        result["items"].append(item)
    result["candidate_count"] = len(candidates)
    return result
