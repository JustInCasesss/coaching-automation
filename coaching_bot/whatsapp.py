"""WhatsApp Cloud API client.

Every outbound action is a plain dict so the flow engine stays pure and testable:

    {"kind": "text",     "body": "..."}
    {"kind": "buttons",  "body": "...", "buttons": [{"id":"x","title":"Yes"}]}
    {"kind": "list",     "body": "...", "button": "Choose", "sections":[...]}
    {"kind": "template", "name": "fee_reminder_v1", "lang": "en", "body": "...",
                         "components": [...]}
    {"kind": "document", "link": "https://...", "filename": "Report.pdf", "body": "caption"}

`send()` records the message in SQLite and appends the exact wire payload to
data/outbox.jsonl. With credentials configured it also POSTs to Meta.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config, store

# Meta limits
MAX_BUTTONS = 3
MAX_LIST_ROWS = 10
MAX_LIST_SECTIONS = 10
TEXT_LIMIT = 4096
BUTTON_TITLE_LIMIT = 20
LIST_ROW_TITLE_LIMIT = 24


def t(body: str) -> dict:
    return {"kind": "text", "body": body[:TEXT_LIMIT]}


def buttons(body: str, btns: list[tuple[str, str]]) -> dict:
    """btns: [(id, title)] - max 3, title <= 20 chars."""
    clean = [{"id": bid[:256], "title": title[:BUTTON_TITLE_LIMIT]} for bid, title in btns[:MAX_BUTTONS]]
    return {"kind": "buttons", "body": body[:1024], "buttons": clean}


def menu(body: str, button_label: str, sections: list[dict]) -> dict:
    """sections: [{"title": "...", "rows":[{"id":..,"title":..,"description":..}]}]"""
    out_sections = []
    total = 0
    for sec in sections[:MAX_LIST_SECTIONS]:
        rows = []
        for r in sec.get("rows", []):
            if total >= MAX_LIST_ROWS:
                break
            rows.append({"id": str(r["id"])[:200], "title": str(r["title"])[:LIST_ROW_TITLE_LIMIT],
                         "description": str(r.get("description", ""))[:72]})
            total += 1
        if rows:
            out_sections.append({"title": str(sec.get("title", ""))[:24], "rows": rows})
    return {"kind": "list", "body": body[:1024], "button": button_label[:20], "sections": out_sections}


def template(name: str, lang: str, body: str, components: list[dict] | None = None) -> dict:
    return {"kind": "template", "name": name, "lang": lang, "body": body,
            "components": components or []}


def document(link: str, filename: str, body: str = "") -> dict:
    return {"kind": "document", "link": link, "filename": filename, "body": body[:1024]}


# --------------------------------------------------------------------------
# wire payload builders
# --------------------------------------------------------------------------
def _payload(to: str, out: dict) -> dict:
    to = store.normalize_phone(to)
    kind = out["kind"]
    if kind == "text":
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                "type": "text", "text": {"preview_url": False, "body": out["body"]}}
    if kind == "buttons":
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                "type": "interactive",
                "interactive": {"type": "button", "body": {"text": out["body"]},
                                "action": {"buttons": [
                                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                                    for b in out["buttons"]]}}}
    if kind == "list":
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                "type": "interactive",
                "interactive": {"type": "list", "body": {"text": out["body"]},
                                "action": {"button": out["button"], "sections": out["sections"]}}}
    if kind == "template":
        comps = out.get("components") or []
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                "type": "template",
                "template": {"name": out["name"], "language": {"code": out["lang"]},
                             "components": comps}}
    if kind == "document":
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                "type": "document",
                "document": {"link": out["link"], "filename": out["filename"],
                             "caption": out.get("body") or ""}}
    raise ValueError(f"unknown outbound kind: {kind}")


def _post(payload: dict) -> tuple[str | None, str | None]:
    """Returns (wamid, error). Only called when credentials exist."""
    url = (f"https://graph.facebook.com/{config.WA_API_VERSION}/"
           f"{config.WA_PHONE_ID}/messages")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {config.WA_TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        msgs = data.get("messages") or []
        return (msgs[0].get("id") if msgs else None), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}"
    except Exception as e:  # noqa: BLE001 - network layer, surface anything
        return None, f"{type(e).__name__}: {e}"


def _append_outbox(to: str, out: dict, payload: dict, status: str) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": store.ts(), "to": store.normalize_phone(to), "status": status, "payload": payload}
    with config.OUTBOX_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def send(con, to: str, out: dict, status_hint: str | None = None) -> dict:
    """Send one outbound action. Returns {ok, status, wamid, error, payload}."""
    payload = _payload(to, out)
    if config.demo_mode():
        status, wamid, err = "simulated", None, None
    else:
        wamid, err = _post(payload)
        status = "sent" if wamid else "failed"
    if status_hint:
        status = status_hint
    store.log_message(con, store.normalize_phone(to), "out", out.get("body", ""),
                      kind=out["kind"], payload=out, status=status, wamid=wamid, error=err)
    _append_outbox(to, out, payload, status)
    return {"ok": status in ("sent", "simulated", "delivered"), "status": status,
            "wamid": wamid, "error": err, "payload": payload}


def send_many(con, to: str, outs: list[dict]) -> list[dict]:
    return [send(con, to, o) for o in outs]


def parse_inbound(body: bytes) -> list[dict]:
    """Normalise a Meta webhook POST into a list of inbound events.

    Returns dicts: {phone, text, kind, id, name, timestamp, raw}
    kind is 'text' | 'button' | 'list' | 'status' | 'other'.
    """
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return []
    events: list[dict] = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                        for c in value.get("contacts", []) or []}
            for msg in value.get("messages", []) or []:
                frm = msg.get("from", "")
                mtype = msg.get("type", "other")
                ev = {"phone": frm, "kind": "other", "text": "", "id": msg.get("id"),
                      "name": contacts.get(frm), "timestamp": msg.get("timestamp"),
                      "raw": msg}
                if mtype == "text":
                    ev["kind"] = "text"
                    ev["text"] = (msg.get("text") or {}).get("body", "")
                elif mtype == "interactive":
                    inter = msg.get("interactive") or {}
                    if inter.get("type") == "button_reply":
                        ev["kind"] = "button"
                        ev["text"] = (inter.get("button_reply") or {}).get("id", "")
                        ev["title"] = (inter.get("button_reply") or {}).get("title", "")
                    elif inter.get("type") == "list_reply":
                        ev["kind"] = "list"
                        ev["text"] = (inter.get("list_reply") or {}).get("id", "")
                        ev["title"] = (inter.get("list_reply") or {}).get("title", "")
                elif mtype == "button":
                    ev["kind"] = "button"
                    ev["text"] = (msg.get("button") or {}).get("payload", "")
                elif mtype == "document":
                    ev["kind"] = "other"
                    ev["text"] = "PAYMENT_PROOF_DOCUMENT"
                elif mtype == "image":
                    ev["kind"] = "other"
                    ev["text"] = "PAYMENT_PROOF_IMAGE"
                events.append(ev)
            for st in value.get("statuses", []) or []:
                events.append({"phone": st.get("recipient_id", ""), "kind": "status",
                               "text": st.get("status", ""), "id": st.get("id"),
                               "timestamp": st.get("timestamp"), "raw": st})
    return events