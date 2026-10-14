"""SQLite storage layer. No ORM, no dependencies."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    phone         TEXT NOT NULL,
    name          TEXT,
    student_class TEXT,
    course_code   TEXT,
    batch_pref    TEXT,
    callback_pref TEXT,
    source        TEXT DEFAULT 'whatsapp',
    status        TEXT DEFAULT 'new',      -- new|contacted|demo_booked|won|lost
    score         INTEGER DEFAULT 0,
    temperature   TEXT DEFAULT 'COLD',     -- COLD|WARM|HOT
    notes         TEXT,
    raw           TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

CREATE TABLE IF NOT EXISTS students (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no      TEXT UNIQUE,
    name         TEXT NOT NULL,
    student_class TEXT,
    course_code  TEXT,
    batch        TEXT,
    branch       TEXT,
    parent_name  TEXT,
    parent_phone TEXT NOT NULL,
    joined_on    TEXT,
    status       TEXT DEFAULT 'active',    -- active|paused|alumni
    fee_total    REAL DEFAULT 0,
    fee_plan     TEXT,
    alt_phone    TEXT,
    email        TEXT
);
CREATE INDEX IF NOT EXISTS idx_students_parent ON students(parent_phone);

CREATE TABLE IF NOT EXISTS fee_schedule (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id   INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    amount       REAL NOT NULL,
    due_date     TEXT NOT NULL,
    status       TEXT DEFAULT 'pending',   -- pending|partial|paid|waived
    paid_amount  REAL DEFAULT 0,
    paid_on      TEXT,
    method       TEXT,
    last_reminder_stage TEXT,
    last_reminder_on    TEXT,
    remind_count INTEGER DEFAULT 0,
    paused       INTEGER DEFAULT 0,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_fee_due ON fee_schedule(due_date, status);
CREATE INDEX IF NOT EXISTS idx_fee_student ON fee_schedule(student_id);

CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    fee_schedule_id INTEGER REFERENCES fee_schedule(id),
    amount          REAL NOT NULL,
    paid_on         TEXT NOT NULL,
    method          TEXT,
    reference       TEXT,
    recorded_by     TEXT,
    receipt_sent    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    test_name      TEXT NOT NULL,
    test_date      TEXT NOT NULL,
    subject        TEXT,
    marks_obtained REAL NOT NULL,
    marks_total    REAL NOT NULL,
    batch_avg_pct  REAL,
    batch_topper_pct REAL,
    rank_in_batch  INTEGER,
    teacher_remark TEXT,
    alerted        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tests_student ON tests(student_id, test_date);

CREATE TABLE IF NOT EXISTS attendance (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    on_date    TEXT NOT NULL,
    status     TEXT NOT NULL,              -- present|absent|late|leave
    UNIQUE(student_id, on_date)
);

CREATE TABLE IF NOT EXISTS conversations (
    phone      TEXT PRIMARY KEY,
    state      TEXT DEFAULT 'new',
    ctx        TEXT DEFAULT '{}',
    role       TEXT DEFAULT 'unknown',     -- unknown|lead|parent|student
    student_id INTEGER,
    opted_out  INTEGER DEFAULT 0,
    last_in_at TEXT,
    last_out_at TEXT,
    turns      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    phone      TEXT NOT NULL,
    direction  TEXT NOT NULL,              -- in|out
    kind       TEXT DEFAULT 'text',        -- text|buttons|list|template|document
    body       TEXT,
    payload    TEXT,
    status     TEXT,                       -- simulated|sent|failed|delivered|read
    wamid      TEXT,
    error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone, ts);

CREATE TABLE IF NOT EXISTS reminder_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fee_schedule_id INTEGER NOT NULL REFERENCES fee_schedule(id) ON DELETE CASCADE,
    student_id      INTEGER NOT NULL,
    stage           TEXT NOT NULL,
    tone            TEXT,
    scheduled_for   TEXT NOT NULL,
    sent_at         TEXT,
    status          TEXT DEFAULT 'queued', -- queued|sent|held_quiet_hours|skipped|failed
    reason          TEXT,
    body            TEXT,
    UNIQUE(fee_schedule_id, stage)
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,              -- hot_lead|payment_claim|overdue_escalation|low_attendance
    phone      TEXT,
    body       TEXT NOT NULL,
    status     TEXT DEFAULT 'simulated',
    dedupe_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_dedupe ON alerts(dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS kpi_daily (
    day        TEXT PRIMARY KEY,
    enquiries  INTEGER DEFAULT 0,
    leads_won  INTEGER DEFAULT 0,
    reminders  INTEGER DEFAULT 0,
    collected  REAL DEFAULT 0,
    overdue    REAL DEFAULT 0
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init(path: Path | str | None = None) -> sqlite3.Connection:
    con = connect(path)
    con.executescript(SCHEMA)
    _migrate(con)
    con.commit()
    return con


def _migrate(con) -> None:
    """Additive migrations for databases created by an earlier version.

    CREATE TABLE IF NOT EXISTS does not add columns to an existing table, so any
    column added after v1.0 needs an explicit ALTER here.
    """
    cols = {r["name"] for r in con.execute("PRAGMA table_info(alerts)")}
    if "dedupe_key" not in cols:
        con.execute("ALTER TABLE alerts ADD COLUMN dedupe_key TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_dedupe ON alerts(dedupe_key)"
                " WHERE dedupe_key IS NOT NULL")


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------
def ts(dt: datetime | None = None) -> str:
    return (dt or config.now_ist()).isoformat(timespec="seconds")


def ds(d=None) -> str:
    if d is None:
        return config.today_ist().isoformat()
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------
def get_conv(con, phone: str) -> dict:
    row = con.execute("SELECT * FROM conversations WHERE phone=?", (phone,)).fetchone()
    if not row:
        con.execute("INSERT INTO conversations(phone, state, ctx) VALUES(?,?,?)", (phone, "new", "{}"))
        con.commit()
        row = con.execute("SELECT * FROM conversations WHERE phone=?", (phone,)).fetchone()
    d = dict(row)
    try:
        d["ctx"] = json.loads(d.get("ctx") or "{}")
    except json.JSONDecodeError:
        d["ctx"] = {}
    return d


def save_conv(con, phone: str, state: str, ctx: dict, role: str | None = None,
              student_id: int | None = None, inbound: bool = True) -> None:
    now = ts()
    cur = con.execute("SELECT role, student_id, turns, opted_out FROM conversations WHERE phone=?", (phone,)).fetchone()
    role = role or (cur["role"] if cur else "unknown")
    sid = student_id if student_id is not None else (cur["student_id"] if cur else None)
    turns = (cur["turns"] if cur else 0) + (1 if inbound else 0)
    if inbound:
        con.execute(
            "UPDATE conversations SET state=?, ctx=?, role=?, student_id=?, last_in_at=?, turns=? WHERE phone=?",
            (state, json.dumps(ctx), role, sid, now, turns, phone))
    else:
        con.execute("UPDATE conversations SET state=?, ctx=?, last_out_at=? WHERE phone=?",
                    (state, json.dumps(ctx), now, phone))
    con.commit()


def set_optout(con, phone: str, opted_out: bool = True) -> None:
    con.execute("UPDATE conversations SET opted_out=? WHERE phone=?", (1 if opted_out else 0, phone))
    con.commit()


# --------------------------------------------------------------------------
# messages / outbox
# --------------------------------------------------------------------------
def log_message(con, phone: str, direction: str, body: str, kind: str = "text",
                payload=None, status: str | None = None, wamid: str | None = None,
                error: str | None = None) -> int:
    cur = con.execute(
        "INSERT INTO messages(ts, phone, direction, kind, body, payload, status, wamid, error)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (ts(), phone, direction, kind, body,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None,
         status, wamid, error))
    con.commit()
    return cur.lastrowid


def transcript(con, phone: str, limit: int = 100) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM messages WHERE phone=? ORDER BY id DESC LIMIT ?", (phone, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# --------------------------------------------------------------------------
# leads
# --------------------------------------------------------------------------
def upsert_lead(con, phone: str, **fields) -> dict:
    row = con.execute(
        "SELECT * FROM leads WHERE phone=? AND status NOT IN ('won','lost') ORDER BY id DESC LIMIT 1",
        (phone,)).fetchone()
    now = ts()
    if row is None:
        cols = ["created_at", "updated_at", "phone"]
        vals = [now, now, phone]
        for k, v in fields.items():
            if v is not None:
                cols.append(k)
                vals.append(v)
        q = f"INSERT INTO leads({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
        cur = con.execute(q, vals)
        con.commit()
        row = con.execute("SELECT * FROM leads WHERE id=?", (cur.lastrowid,)).fetchone()
    else:
        sets, vals = ["updated_at=?"], [now]
        for k, v in fields.items():
            if v is not None:
                sets.append(f"{k}=?")
                vals.append(v)
        vals.append(row["id"])
        con.execute(f"UPDATE leads SET {','.join(sets)} WHERE id=?", vals)
        con.commit()
        row = con.execute("SELECT * FROM leads WHERE id=?", (row["id"],)).fetchone()
    return dict(row)


def score_lead(lead: dict) -> tuple[int, str]:
    """Deterministic lead score. Higher = closer to enrolment."""
    s = 0
    if lead.get("course_code"):
        s += 5
    if lead.get("student_class"):
        s += 5
    if lead.get("name"):
        s += 10
    if lead.get("batch_pref"):
        s += 15
    if lead.get("callback_pref"):
        s += 20
    notes = (lead.get("notes") or "").lower()
    if "fee" in notes or "fees" in notes:
        s += 10
    if "demo" in notes:
        s += 25
    if "scholarship" in notes or "discount" in notes:
        s += 8
    temp = "HOT" if s >= 50 else ("WARM" if s >= 25 else "COLD")
    return s, temp


def hot_leads(con, limit: int = 50) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM leads WHERE temperature='HOT' AND status NOT IN ('won','lost')"
        " ORDER BY score DESC, updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# students
# --------------------------------------------------------------------------
def find_students_by_phone(con, phone: str) -> list[dict]:
    norm = normalize_phone(phone)
    rows = con.execute("SELECT * FROM students WHERE status='active'").fetchall()
    out = []
    for r in rows:
        if normalize_phone(r["parent_phone"]) == norm or (
                r["alt_phone"] and normalize_phone(r["alt_phone"]) == norm):
            out.append(dict(r))
    return out


def normalize_phone(p: str | None) -> str:
    """E.164-ish: keep digits, drop a leading 0, assume 91 for bare 10-digit."""
    if not p:
        return ""
    d = "".join(ch for ch in str(p) if ch.isdigit())
    if len(d) == 10:
        d = "91" + d
    elif len(d) == 11 and d.startswith("0"):
        d = "91" + d[1:]
    return d


def student_by_roll(con, roll: str) -> dict | None:
    row = con.execute("SELECT * FROM students WHERE roll_no=?", (roll,)).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# fees
# --------------------------------------------------------------------------
def pending_fees(con, student_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM fee_schedule WHERE student_id=? AND status IN ('pending','partial')"
        " ORDER BY due_date", (student_id,)).fetchall()
    return [dict(r) for r in rows]


def fee_outstanding(row: dict) -> float:
    return max(0.0, float(row["amount"]) - float(row["paid_amount"] or 0))


def dues_in_window(con, offset_days: int) -> list[dict]:
    """Fees whose due_date == today + offset_days, still unpaid, not paused."""
    target = (config.today_ist() + timedelta(days=offset_days)).isoformat()
    rows = con.execute(
        "SELECT f.*, s.name AS student_name, s.parent_name, s.parent_phone, s.batch,"
        " s.course_code, s.roll_no, s.branch"
        " FROM fee_schedule f JOIN students s ON s.id=f.student_id"
        " WHERE f.due_date=? AND f.status IN ('pending','partial') AND f.paused=0"
        "   AND s.status='active'", (target,)).fetchall()
    return [dict(r) for r in rows]


def overdue_fees(con) -> list[dict]:
    today = config.today_ist().isoformat()
    rows = con.execute(
        "SELECT f.*, s.name AS student_name, s.parent_phone, s.roll_no, s.course_code"
        " FROM fee_schedule f JOIN students s ON s.id=f.student_id"
        " WHERE f.due_date < ? AND f.status IN ('pending','partial') AND f.paused=0"
        "   AND s.status='active' ORDER BY f.due_date", (today,)).fetchall()
    return [dict(r) for r in rows]


def record_payment(con, student_id: int, fee_id: int | None, amount: float,
                   method: str = "UPI", reference: str | None = None,
                   paid_on: str | None = None, recorded_by: str = "system") -> dict:
    paid_on = paid_on or ds()
    con.execute(
        "INSERT INTO payments(student_id, fee_schedule_id, amount, paid_on, method, reference, recorded_by)"
        " VALUES(?,?,?,?,?,?,?)",
        (student_id, fee_id, amount, paid_on, method, reference, recorded_by))
    if fee_id:
        row = con.execute("SELECT * FROM fee_schedule WHERE id=?", (fee_id,)).fetchone()
        if row:
            new_paid = float(row["paid_amount"] or 0) + float(amount)
            status = "paid" if new_paid >= float(row["amount"]) - 0.5 else "partial"
            con.execute(
                "UPDATE fee_schedule SET paid_amount=?, status=?, paid_on=?, method=?, paused=0 WHERE id=?",
                (new_paid, status, paid_on, method, fee_id))
            con.execute("UPDATE reminder_log SET status='skipped', reason='paid'"
                        " WHERE fee_schedule_id=? AND status IN ('queued','held_quiet_hours')", (fee_id,))
    con.commit()
    return {"student_id": student_id, "fee_schedule_id": fee_id, "amount": amount,
            "paid_on": paid_on, "method": method, "reference": reference}


def collection_summary(con) -> dict:
    today = config.today_ist()
    month_start = today.replace(day=1).isoformat()
    collected_month = con.execute(
        "SELECT COALESCE(SUM(amount),0) c FROM payments WHERE paid_on >= ?", (month_start,)).fetchone()["c"]
    collected_today = con.execute(
        "SELECT COALESCE(SUM(amount),0) c FROM payments WHERE paid_on = ?", (today.isoformat(),)).fetchone()["c"]
    overdue = sum(fee_outstanding(r) for r in overdue_fees(con))
    pending = con.execute(
        "SELECT COALESCE(SUM(amount - COALESCE(paid_amount,0)),0) c FROM fee_schedule"
        " WHERE status IN ('pending','partial')").fetchone()["c"]
    return {"collected_today": collected_today, "collected_month": collected_month,
            "outstanding_total": pending, "overdue_total": overdue}


# --------------------------------------------------------------------------
# performance
# --------------------------------------------------------------------------
def student_tests(con, student_id: int, limit: int = 50) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM tests WHERE student_id=? ORDER BY test_date DESC LIMIT ?",
        (student_id, limit)).fetchall()
    return [dict(r) for r in rows]


def attendance_stats(con, student_id: int, days: int = 30) -> dict:
    since = (config.today_ist() - timedelta(days=days)).isoformat()
    rows = con.execute(
        "SELECT status, COUNT(*) n FROM attendance WHERE student_id=? AND on_date>=? GROUP BY status",
        (student_id, since)).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())
    present = counts.get("present", 0) + counts.get("late", 0)
    pct = (100.0 * present / total) if total else None
    return {"total_marked": total, "present": present, "absent": counts.get("absent", 0),
            "late": counts.get("late", 0), "leave": counts.get("leave", 0), "pct": pct,
            "window_days": days}


def student_performance_summary(con, student_id: int, window_days: int = 60) -> dict:
    stu = con.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if not stu:
        return {}
    stu = dict(stu)
    tests = student_tests(con, student_id, 200)
    recent = [t for t in tests if t["test_date"] >= (config.today_ist() - timedelta(days=window_days)).isoformat()]
    subject_pct: dict[str, list[float]] = {}
    for t in recent:
        if t["marks_total"]:
            subject_pct.setdefault(t["subject"] or "General", []).append(
                100.0 * t["marks_obtained"] / t["marks_total"])
    subj_avg = {k: sum(v) / len(v) for k, v in subject_pct.items()}
    overall = sum(subj_avg.values()) / len(subj_avg) if subj_avg else None
    best = max(subj_avg, key=subj_avg.get) if subj_avg else None
    weak = min(subj_avg, key=subj_avg.get) if subj_avg else None
    # trend: last 3 vs previous 3 overall pct
    pcts = [100.0 * t["marks_obtained"] / t["marks_total"] for t in tests if t["marks_total"]]
    trend = None
    if len(pcts) >= 6:
        new3, old3 = sum(pcts[:3]) / 3, sum(pcts[3:6]) / 3
        trend = new3 - old3
    att = attendance_stats(con, student_id, 30)
    return {"student": stu, "tests_in_window": len(recent), "tests_total": len(tests),
            "overall_pct": overall, "subject_avg": subj_avg, "best_subject": best,
            "weak_subject": weak, "trend_delta": trend, "attendance": att,
            "latest": recent[0] if recent else None, "window_days": window_days}


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------
def log_alert(con, kind: str, body: str, phone: str | None = None,
              status: str = "simulated", dedupe_key: str | None = None) -> int | None:
    """Record a staff alert.

    `dedupe_key` makes the alert fire once ever. That matters for escalations:
    a held or retried reminder must not re-notify accounts every tick for the
    same fee and stage. Returns None when the alert was already recorded.
    """
    cur = con.execute(
        "INSERT OR IGNORE INTO alerts(ts, kind, phone, body, status, dedupe_key)"
        " VALUES(?,?,?,?,?,?)", (ts(), kind, phone, body, status, dedupe_key))
    con.commit()
    return cur.lastrowid if cur.rowcount else None


def alert_exists(con, dedupe_key: str) -> bool:
    row = con.execute("SELECT 1 FROM alerts WHERE dedupe_key=?", (dedupe_key,)).fetchone()
    return row is not None


def recent_alerts(con, limit: int = 25) -> list[dict]:
    rows = con.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]