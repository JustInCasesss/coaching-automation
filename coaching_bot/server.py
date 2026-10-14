"""Threaded HTTP server for the webhook and protected admin API."""
from __future__ import annotations

from datetime import date
import hashlib
import hmac
import html
import json
import logging
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db
from .config import admin_token, demo_mode, is_live_whatsapp, load_institute_config, validate_server_config
from .reminders import run_reminders
from .whatsapp import send_text

LOG = logging.getLogger("coaching_bot")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def _reply_for(text: str, institute: dict) -> str:
    value = text.strip().lower()
    name = str(institute.get("institute_name", "our institute"))
    courses = institute.get("courses", [])
    course_lines = "\n".join(
        f"• {item.get('name', 'Course')}: {item.get('details', 'Contact us for details')}"
        for item in courses if isinstance(item, dict)
    )
    if value in {"1", "admission", "admissions"}:
        return f"Admissions at {name} are open. Reply with your class and preferred course, or reply 4 to request a counsellor call."
    if value in {"2", "fee", "fees", "course", "courses", "price"}:
        fees = institute.get("fees", {})
        fee_lines = "\n".join(f"{key}: {value}" for key, value in fees.items())
        return f"Courses and fees at {name}:\n{course_lines or 'Please contact the institute for current courses.'}\n{fee_lines}\nReply 4 for a counsellor."
    if value in {"3", "demo", "trial", "demo class"}:
        return f"Thanks for your interest in a demo class at {name}. Please share the student's class and preferred time. Our team will confirm availability."
    if value in {"4", "call", "counsellor", "counselor", "callback"}:
        return f"Your request has been noted for {name}. The counselling team will follow up during institute hours."
    if value == "paid":
        return "Thanks for the update. Accounts will verify the payment. Please keep the UTR or receipt available."
    if value == "report":
        return "Your report request has been noted. Please contact the institute office to verify the student and receive the latest report."
    return (
        f"Namaste! Welcome to {name}.\n"
        "Reply with:\n1. Admissions\n2. Courses and fees\n3. Demo class\n4. Request a counsellor call\n\n"
        "Reply STOP to opt out of WhatsApp messages."
    )


def _extract_text(message: dict) -> str:
    msg_type = message.get("type", "")
    if msg_type == "text":
        return str(message.get("text", {}).get("body", "")).strip()
    interactive = message.get("interactive", {})
    if msg_type == "interactive":
        button = interactive.get("button_reply", {})
        listing = interactive.get("list_reply", {})
        return str(button.get("title") or button.get("id") or listing.get("title") or listing.get("id") or "").strip()
    if msg_type == "button":
        return str(message.get("button", {}).get("text", "")).strip()
    return ""


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "CoachingAutomation/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        # Do not log request paths: Meta webhook verification tokens can appear in query strings.
        LOG.debug("HTTP request handled.")

    def _headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, status: int, payload: object) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self._headers("application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body_text: str) -> None:
        body = body_text.encode("utf-8")
        self.send_response(status)
        self._headers("text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length.") from None
        if length < 0 or length > 1_000_000:
            raise ValueError("Request body must be smaller than 1 MB.")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must contain valid JSON.") from None
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Admin-Token", "")
        expected = admin_token()
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def _check_signature(self, raw: bytes) -> bool:
        secret = os.getenv("META_APP_SECRET", "").strip()
        if not secret:
            return demo_mode()
        supplied = self.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            try:
                db.init_db()
                self._send_json(200, {"status": "ok", "service": "coaching-automation", "whatsapp_mode": "live" if is_live_whatsapp() else "dry_run"})
            except Exception:
                self._send_json(503, {"status": "error", "service": "coaching-automation"})
            return

        if parsed.path == "/":
            try:
                cfg = load_institute_config()
                institute = html.escape(str(cfg.get("institute_name", "Coaching Institute")))
            except RuntimeError:
                institute = "Coaching Institute"
            mode = "Live WhatsApp" if is_live_whatsapp() else "Safe demo mode"
            page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{institute} · Coaching Automation</title>
<style>
:root{{color-scheme:dark;--bg:#0b1020;--panel:#121a2e;--line:#273450;--text:#edf3ff;--muted:#a8b5cf;--accent:#8bd3c7}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% 0,#19284a,var(--bg) 55%);color:var(--text);font:16px/1.6 system-ui,sans-serif;min-height:100vh;display:grid;place-items:center;padding:24px}}
main{{max-width:760px;width:100%}}.eyebrow{{text-transform:uppercase;letter-spacing:.16em;color:var(--accent);font-weight:700;font-size:.75rem}}
h1{{font-size:clamp(2rem,6vw,3.7rem);line-height:1.05;letter-spacing:-.04em;margin:.7rem 0 1rem}}p{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:28px 0}}
article{{background:#121a2eeb;border:1px solid var(--line);border-radius:18px;padding:19px}}article strong{{display:block;margin-bottom:5px;color:var(--text)}}.status{{display:inline-flex;align-items:center;gap:9px;border:1px solid var(--line);padding:8px 12px;border-radius:999px;color:var(--accent);font-size:.9rem}}.dot{{width:8px;height:8px;background:var(--accent);border-radius:100%}}footer{{color:#71809c;font-size:.85rem;border-top:1px solid var(--line);padding-top:18px}}
</style></head><body><main><div class="eyebrow">Coaching Institute Automation</div><h1>{institute}<br>runs smarter.</h1>
<p>Admission enquiries, student records, fee tracking, and parent notifications in one lightweight service.</p>
<span class="status"><span class="dot"></span>{html.escape(mode)} · Service online</span>
<div class="grid"><article><strong>WhatsApp enquiries</strong><p>Webhook handling with STOP/START opt-out support and duplicate-message protection.</p></article>
<article><strong>Student and fee records</strong><p>Protected admin endpoints backed by SQLite.</p></article>
<article><strong>Scheduled reminders</strong><p>Fee reminders use approved WhatsApp templates and enforce quiet hours.</p></article></div>
<footer>API status: <a href="/health" style="color:var(--accent)">/health</a> · Private admin API requires the X-Admin-Token header.</footer>
</main></body></html>"""
            self._send_html(200, page)
            return

        if parsed.path == "/webhook":
            query = parse_qs(parsed.query)
            mode = query.get("hub.mode", [""])[0]
            token = query.get("hub.verify_token", [""])[0]
            challenge = query.get("hub.challenge", [""])[0]
            expected = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
            if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
                body = challenge.encode("utf-8")
                self.send_response(200)
                self._headers("text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json(403, {"error": "Webhook verification failed."})
            return

        if parsed.path in {"/admin/stats", "/admin/leads"}:
            if not self._authorized():
                self._send_json(401, {"error": "Unauthorized."})
                return
            db.init_db()
            if parsed.path == "/admin/stats":
                self._send_json(200, db.stats())
            else:
                self._send_json(200, {"leads": db.recent_leads()})
            return
        self._send_json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1_000_000:
                raise ValueError("Request body must be smaller than 1 MB.")
            raw = self.rfile.read(length)
            if parsed.path == "/webhook":
                if not self._check_signature(raw):
                    self._send_json(403, {"error": "Webhook signature missing or invalid."})
                    return
                payload = json.loads(raw or b"{}")
                self._handle_webhook(payload)
                self._send_json(200, {"ok": True})
                return
            if not parsed.path.startswith("/admin/"):
                self._send_json(404, {"error": "Not found."})
                return
            if not self._authorized():
                self._send_json(401, {"error": "Unauthorized."})
                return
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object.")
            db.init_db()
            self._handle_admin(parsed.path, payload)
        except (ValueError, KeyError) as exc:
            self._send_json(400, {"error": str(exc)})
        except LookupError as exc:
            self._send_json(404, {"error": str(exc)})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Request body must contain valid JSON."})
        except Exception:
            LOG.exception("Request failed on %s", parsed.path)
            self._send_json(500, {"error": "Internal server error."})

    def _handle_webhook(self, payload: dict) -> None:
        if payload.get("object") != "whatsapp_business_account":
            return
        institute = load_institute_config()
        entries = payload.get("entry", [])
        for entry in entries if isinstance(entries, list) else []:
            for change in entry.get("changes", []) if isinstance(entry, dict) else []:
                value = change.get("value", {}) if isinstance(change, dict) else {}
                messages = value.get("messages", []) if isinstance(value, dict) else []
                for message in messages if isinstance(messages, list) else []:
                    if not isinstance(message, dict):
                        continue
                    phone = str(message.get("from", "")).strip()
                    text = _extract_text(message)
                    if not phone or not text:
                        continue
                    event_id = str(message.get("id") or hashlib.sha256(f"{phone}:{text}".encode()).hexdigest())
                    if not db.record_inbound(event_id, phone, text):
                        continue
                    db.upsert_lead(phone, text)
                    command = text.strip().upper()
                    if command == "STOP":
                        db.set_opt_out(phone, True)
                        reply = "You have been opted out of institute WhatsApp messages. Reply START to opt in again."
                    elif command == "START":
                        db.set_opt_out(phone, False)
                        reply = f"You are subscribed to updates from {institute.get('institute_name', 'the institute')} again."
                    elif db.is_opted_out(phone):
                        continue
                    else:
                        reply = _reply_for(text, institute)
                    try:
                        send_text(phone, reply)
                    except Exception as exc:
                        LOG.warning("Could not send WhatsApp reply (%s).", type(exc).__name__)

    def _handle_admin(self, path: str, payload: dict) -> None:
        if path == "/admin/students":
            for field in ("roll_no", "full_name"):
                if not str(payload.get(field, "")).strip():
                    raise ValueError(f"{field} is required.")
            student = db.create_student(payload)
            self._send_json(201, {"student": student})
            return
        if path == "/admin/fees":
            for field in ("student_id", "description", "amount", "due_date"):
                if field not in payload or str(payload[field]).strip() == "":
                    raise ValueError(f"{field} is required.")
            try:
                fee = db.create_fee(payload)
            except sqlite3.IntegrityError as exc:
                raise ValueError("Unable to create fee; check the student and amount.") from exc
            self._send_json(201, {"fee": fee})
            return
        if path == "/admin/run-reminders":
            dry_run = bool(payload.get("dry_run", True))
            force = bool(payload.get("force", False))
            self._send_json(200, run_reminders(dry_run=dry_run, force=force))
            return
        if path == "/admin/mark-paid":
            if not payload.get("fee_id"):
                raise ValueError("fee_id is required.")
            fee = db.mark_fee_paid(int(payload["fee_id"]), str(payload.get("payment_method", "manual")))
            if fee is None:
                raise LookupError("Fee not found.")
            if fee.get("parent_phone"):
                try:
                    send_text(fee["parent_phone"], f"Payment acknowledgement: {fee['description']} for {fee['full_name']} is marked as paid. Thank you.")
                except Exception as exc:
                    LOG.warning("Payment was recorded but WhatsApp receipt failed (%s).", type(exc).__name__)
            self._send_json(200, {"fee": fee})
            return
        if path == "/admin/score":
            for field in ("roll_no", "test_name", "subject", "marks", "total"):
                if field not in payload or str(payload[field]).strip() == "":
                    raise ValueError(f"{field} is required.")
            scored = db.add_score(payload)
            student = scored["student"]
            score = scored["score"]
            if student.get("parent_phone"):
                message = (
                    f"Test update for {student['full_name']}: {score['test_name']} · {score['subject']} "
                    f"{score['marks']}/{score['total']} ({score['marks'] / score['total'] * 100:.1f}%). "
                    f"{score['remark']}".strip()
                )
                try:
                    send_text(student["parent_phone"], message)
                except Exception as exc:
                    LOG.warning("Score saved but WhatsApp alert failed (%s).", type(exc).__name__)
            self._send_json(201, {"student": student, "score": score})
            return
        if path == "/admin/stats":
            self._send_json(200, db.stats())
            return
        self._send_json(404, {"error": "Unknown admin endpoint."})


def serve(host: str = "0.0.0.0", port: int | None = None) -> None:
    validate_server_config()
    db.init_db()
    actual_port = port if port is not None else int(os.getenv("PORT", "8080"))
    if not 1 <= actual_port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535.")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((host, actual_port), RequestHandler)
    server.daemon_threads = True
    LOG.info("Coaching Automation listening on %s:%s (WhatsApp %s)", host, actual_port, "live" if is_live_whatsapp() else "dry-run")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOG.info("Shutdown requested.")
    finally:
        server.server_close()
