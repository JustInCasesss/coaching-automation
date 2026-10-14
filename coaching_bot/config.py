"""Configuration loader. White-label: everything client-specific lives in
config/institute.json, so a new client is a new JSON file, not a code change."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("COACH_DATA_DIR", ROOT / "data"))
OUT_DIR = Path(os.environ.get("COACH_OUT_DIR", ROOT / "out"))
DB_PATH = Path(os.environ.get("COACH_DB", DATA_DIR / "coaching.db"))
OUTBOX_PATH = Path(os.environ.get("COACH_OUTBOX", DATA_DIR / "outbox.jsonl"))
CONFIG_PATH = Path(os.environ.get("COACH_CONFIG", ROOT / "config" / "institute.json"))

IST = timezone(timedelta(hours=5, minutes=30))

# --- WhatsApp Cloud API (Meta) credentials. Absent => DEMO_MODE dry-run. ---
WA_TOKEN = os.environ.get("WHATSAPP_TOKEN", "").strip()
WA_PHONE_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WA_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "vidyasagar-verify-2026").strip()
WA_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0").strip()
WA_TEMPLATE_LANG = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en").strip()


def demo_mode() -> bool:
    """True when no real WhatsApp credentials are configured.

    In demo mode every outbound message is recorded to the DB and appended to
    data/outbox.jsonl instead of being sent. Nothing is ever silently dropped,
    so a demo shows the exact bytes a parent would receive.
    """
    if os.environ.get("COACH_DEMO_MODE", "").lower() in ("1", "true", "yes"):
        return True
    return not (WA_TOKEN and WA_PHONE_ID)


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist():
    return now_ist().date()


def load_config(path: Path | str | None = None) -> dict:
    p = Path(path or CONFIG_PATH)
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    # Convenience: expose the top-level `offers` and `branding` blocks inside
    # `institute` too, so message builders can read everything from one dict
    # (`institute()['offers']['demo_class']`) while the file keeps them separate.
    inst = data.setdefault("institute", {})
    for key in ("offers", "branding", "fee_reminder_policy", "performance_policy"):
        if key in data:
            inst.setdefault(key, data[key])
    return data


_CFG_CACHE: dict | None = None


def cfg() -> dict:
    global _CFG_CACHE
    if _CFG_CACHE is None:
        _CFG_CACHE = load_config()
    return _CFG_CACHE


def reset_cache() -> None:
    global _CFG_CACHE
    _CFG_CACHE = None


def institute() -> dict:
    return cfg()["institute"]


def courses() -> list[dict]:
    return cfg()["courses"]


def course_by_code(code: str) -> dict | None:
    code = (code or "").strip().upper()
    for c in courses():
        if c["code"].upper() == code:
            return c
    return None


def course_by_label(text: str) -> dict | None:
    """Loose match so a parent typing 'neet coaching' or 'JEE' resolves."""
    t = (text or "").lower()
    for c in courses():
        if c["code"].lower() in t or c["label"].lower() in t:
            return c
    if "medical" in t or "doctor" in t:
        return course_by_code("NEET")
    if "engineering" in t or "iit" in t:
        return course_by_code("JEE")
    if "foundation" in t:
        return course_by_code("FOUNDATION")
    if "board" in t or "10th" in t or "12th" in t:
        return course_by_code("BOARD")
    return None


def in_quiet_hours(ts: datetime | None = None) -> bool:
    """Outbound marketing/reminder traffic is held during quiet hours."""
    ts = ts or now_ist()
    start_s, end_s = institute().get("quiet_hours", "20:00-09:00").split("-")
    sh, sm = (int(x) for x in start_s.split(":"))
    eh, em = (int(x) for x in end_s.split(":"))
    cur = ts.hour * 60 + ts.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start > end:  # crosses midnight
        return cur >= start or cur < end
    return start <= cur < end


def inr(amount: float | int) -> str:
    """Indian digit grouping: 78000 -> 78,000 ; 1250000 -> 12,50,000."""
    n = int(round(float(amount)))
    neg = n < 0
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts) + "," + tail
    return ("-Rs." if neg else "Rs.") + out


def fmt_date(d) -> str:
    """Accepts a date, a datetime, or an ISO string. Always returns '26 Aug 2026'."""
    if d is None:
        return "-"
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d %b %Y").lstrip("0")


_HONORIFICS = {"mr", "mrs", "ms", "dr", "shri", "smt", "sri", "prof", "master", "md"}


def given_name(full: str | None, fallback: str = "Parent") -> str:
    """'Dr. Meera Patil' -> 'Meera'. Reminders that open with 'Namaste Dr.,'
    read like a mail-merge; the first real name reads like a person."""
    parts = [p for p in (full or "").replace(",", " ").split() if p]
    while parts and parts[0].lower().rstrip(".") in _HONORIFICS:
        parts.pop(0)
    return parts[0] if parts else fallback