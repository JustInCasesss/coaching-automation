"""WhatsApp Cloud API transport with a safe local outbox mode."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import demo_mode, is_live_whatsapp


class WhatsAppError(RuntimeError):
    """A WhatsApp API request failed or is incorrectly configured."""


def _validate_phone(phone: str) -> str:
    value = re.sub(r"\D", "", str(phone or ""))
    if not 8 <= len(value) <= 15:
        raise ValueError("Recipient phone must include the country code and 8 to 15 digits.")
    return value


def _write_outbox(payload: dict) -> None:
    path = Path(os.getenv("COACH_OUTBOX", "./data/outbox.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "payload": payload}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _send(payload: dict) -> dict:
    if demo_mode() or not is_live_whatsapp():
        _write_outbox(payload)
        return {"mode": "dry_run", "recorded": True}

    token = os.getenv("WHATSAPP_TOKEN", "").strip()
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    version = os.getenv("WHATSAPP_API_VERSION", "v26.0").strip()
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise WhatsAppError("WHATSAPP_API_VERSION must look like v26.0.")
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(500_000).decode("utf-8", errors="replace")
            return json.loads(body) if body else {"status": response.status}
    except HTTPError as exc:
        detail = exc.read(3000).decode("utf-8", errors="replace")
        raise WhatsAppError(f"WhatsApp API returned HTTP {exc.code}: {detail}") from None
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WhatsAppError(f"WhatsApp API request failed: {type(exc).__name__}") from None


def send_text(phone: str, body: str) -> dict:
    recipient = _validate_phone(phone)
    if not body.strip():
        raise ValueError("Message body cannot be empty.")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4000]},
    }
    return _send(payload)


def send_template(phone: str, name: str, language: str, parameters: list[str]) -> dict:
    """Send only a pre-approved WhatsApp template for scheduled/out-of-window messages."""
    recipient = _validate_phone(phone)
    live = is_live_whatsapp()
    template_name = (name or "").strip()
    if live and not template_name:
        raise WhatsAppError("Set WHATSAPP_REMINDER_TEMPLATE to the name of an approved Meta template.")
    if not template_name:
        template_name = "demo_fee_reminder"
    if not re.fullmatch(r"[a-z0-9_]{1,512}", template_name):
        raise WhatsAppError("WhatsApp template names may contain lowercase letters, digits, and underscores.")
    components = []
    if parameters:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(value)[:1000]} for value in parameters],
        })
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language or "en_US"},
            "components": components,
        },
    }
    return _send(payload)
