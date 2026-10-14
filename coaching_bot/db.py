"""SQLite persistence for leads, students, fees, messages, and scores."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
import os
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    phone TEXT PRIMARY KEY,
    opted_out INTEGER NOT NULL DEFAULT 0 CHECK (opted_out IN (0, 1)),
    last_inbound_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbound_messages (
    message_id TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbound_phone ON inbound_messages(phone);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    course TEXT NOT NULL DEFAULT '',
    class_name TEXT NOT NULL DEFAULT '',
    batch TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL DEFAULT 0,
    latest_message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    parent_name TEXT NOT NULL DEFAULT '',
    parent_phone TEXT NOT NULL DEFAULT '',
    course TEXT NOT NULL DEFAULT '',
    class_name TEXT NOT NULL DEFAULT '',
    batch TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_students_parent_phone ON students(parent_phone);
CREATE TABLE IF NOT EXISTS fees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid')),
    paid_at TEXT,
    payment_method TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fees_due_date ON fees(status, due_date);
CREATE TABLE IF NOT EXISTS fee_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fee_id INTEGER NOT NULL REFERENCES fees(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(fee_id, stage)
);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    test_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    marks REAL NOT NULL,
    total REAL NOT NULL CHECK (total > 0),
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_student ON scores(student_id, created_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    raw_path = os.getenv("COACH_DB", "./data/coaching.db")
    path = Path(raw_path)
    if raw_path != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(raw_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)


def record_inbound(message_id: str, phone: str, body: str) -> bool:
    """Store a webhook message once. False means Meta delivered a duplicate."""
    stamp = now_iso()
    with connection() as conn:
        try:
            conn.execute(
                "INSERT INTO inbound_messages(message_id, phone, body, received_at) VALUES (?, ?, ?, ?)",
                (message_id, phone, body[:4000], stamp),
            )
        except sqlite3.IntegrityError:
            return False
        conn.execute(
            """INSERT INTO contacts(phone, opted_out, last_inbound_at, created_at)
               VALUES (?, 0, ?, ?)
               ON CONFLICT(phone) DO UPDATE SET last_inbound_at = excluded.last_inbound_at""",
            (phone, stamp, stamp),
        )
    return True


def set_opt_out(phone: str, opted_out: bool) -> None:
    stamp = now_iso()
    with connection() as conn:
        conn.execute(
            """INSERT INTO contacts(phone, opted_out, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(phone) DO UPDATE SET opted_out = excluded.opted_out""",
            (phone, int(opted_out), stamp),
        )


def is_opted_out(phone: str) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT opted_out FROM contacts WHERE phone = ?", (phone,)).fetchone()
    return bool(row and row["opted_out"])


def _lead_score(message: str) -> int:
    text = message.lower()
    score = 0
    if any(word in text for word in ("course", "admission", "join", "enrol", "enroll")):
        score += 5
    if any(word in text for word in ("fee", "fees", "price", "cost", "installment")):
        score += 10
    if any(word in text for word in ("demo", "trial", "visit")):
        score += 25
    if any(word in text for word in ("call", "callback", "counsellor", "counselor")):
        score += 20
    return score


def upsert_lead(phone: str, message: str) -> None:
    stamp = now_iso()
    with connection() as conn:
        conn.execute(
            """INSERT INTO leads(phone, score, latest_message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(phone) DO UPDATE SET
                   score = MAX(leads.score, excluded.score),
                   latest_message = excluded.latest_message,
                   updated_at = excluded.updated_at""",
            (phone, _lead_score(message), message[:1000], stamp, stamp),
        )


def create_student(data: dict) -> dict:
    stamp = now_iso()
    with connection() as conn:
        cur = conn.execute(
            """INSERT INTO students
               (roll_no, full_name, parent_name, parent_phone, course, class_name, batch, branch, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["roll_no"].strip(), data["full_name"].strip(),
                data.get("parent_name", "").strip(), data.get("parent_phone", "").strip(),
                data.get("course", "").strip(), data.get("class_name", "").strip(),
                data.get("batch", "").strip(), data.get("branch", "").strip(), stamp,
            ),
        )
        row = conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def get_student_by_roll(roll_no: str) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM students WHERE roll_no = ?", (roll_no,)).fetchone()
    return dict(row) if row else None


def create_fee(data: dict) -> dict:
    due_date = date.fromisoformat(data["due_date"].strip()).isoformat()
    amount = float(data["amount"])
    if amount <= 0:
        raise ValueError("Fee amount must be greater than zero.")
    stamp = now_iso()
    with connection() as conn:
        student = conn.execute(
            "SELECT id FROM students WHERE id = ?", (int(data["student_id"]),)
        ).fetchone()
        if not student:
            raise LookupError("Student not found.")
        cur = conn.execute(
            """INSERT INTO fees(student_id, description, amount, due_date, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (student["id"], data["description"].strip(), amount, due_date, stamp),
        )
        row = conn.execute(
            """SELECT fees.*, students.roll_no, students.full_name, students.parent_phone
               FROM fees JOIN students ON students.id = fees.student_id WHERE fees.id = ?""",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def reminder_stage(due_date: str, today: date) -> str | None:
    due = date.fromisoformat(due_date)
    days_until = (due - today).days
    if days_until == 7:
        return "T-7"
    if days_until == 3:
        return "T-3"
    if days_until == 1:
        return "T-1"
    if days_until == 0:
        return "D0"
    overdue = (today - due).days
    if overdue >= 15:
        return "D+15"
    if overdue >= 7:
        return "D+7"
    if overdue >= 3:
        return "D+3"
    return None


def fee_reminder_candidates(today: date) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """SELECT fees.*, students.roll_no, students.full_name, students.parent_phone
               FROM fees JOIN students ON students.id = fees.student_id
               WHERE fees.status = 'pending' ORDER BY fees.due_date, fees.id"""
        ).fetchall()
        result = []
        for row in rows:
            stage = reminder_stage(row["due_date"], today)
            if not stage or not row["parent_phone"]:
                continue
            existing = conn.execute(
                "SELECT status FROM fee_reminders WHERE fee_id = ? AND stage = ?",
                (row["id"], stage),
            ).fetchone()
            if existing and existing["status"] in ("sent", "sending"):
                continue
            result.append({**dict(row), "stage": stage})
    return result


def claim_reminder(fee_id: int, stage: str) -> bool:
    stamp = now_iso()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
    with connection() as conn:
        row = conn.execute(
            "SELECT status, updated_at FROM fee_reminders WHERE fee_id = ? AND stage = ?",
            (fee_id, stage),
        ).fetchone()
        if row:
            if row["status"] in ("sent", "sending") and not (
                row["status"] == "sending" and row["updated_at"] < stale
            ):
                return False
            conn.execute(
                """UPDATE fee_reminders SET status = 'sending', error = '', updated_at = ?
                   WHERE fee_id = ? AND stage = ?""",
                (stamp, fee_id, stage),
            )
        else:
            conn.execute(
                """INSERT INTO fee_reminders(fee_id, stage, status, created_at, updated_at)
                   VALUES (?, ?, 'sending', ?, ?)""",
                (fee_id, stage, stamp, stamp),
            )
    return True


def finish_reminder(fee_id: int, stage: str, status: str, error: str = "") -> None:
    with connection() as conn:
        conn.execute(
            """UPDATE fee_reminders SET status = ?, error = ?, updated_at = ?
               WHERE fee_id = ? AND stage = ?""",
            (status, error[:500], now_iso(), fee_id, stage),
        )


def mark_fee_paid(fee_id: int, payment_method: str = "manual") -> dict | None:
    with connection() as conn:
        row = conn.execute(
            """SELECT fees.*, students.roll_no, students.full_name, students.parent_phone
               FROM fees JOIN students ON students.id = fees.student_id WHERE fees.id = ?""",
            (fee_id,),
        ).fetchone()
        if not row:
            return None
        if row["status"] == "pending":
            conn.execute(
                "UPDATE fees SET status = 'paid', paid_at = ?, payment_method = ? WHERE id = ?",
                (now_iso(), payment_method.strip()[:80], fee_id),
            )
        updated = conn.execute(
            """SELECT fees.*, students.roll_no, students.full_name, students.parent_phone
               FROM fees JOIN students ON students.id = fees.student_id WHERE fees.id = ?""",
            (fee_id,),
        ).fetchone()
        conn.execute(
            "UPDATE fee_reminders SET status = 'sent', error = 'cancelled: fee paid', updated_at = ? "
            "WHERE fee_id = ? AND status IN ('sending', 'failed')",
            (now_iso(), fee_id),
        )
    return dict(updated)


def add_score(data: dict) -> dict:
    marks = float(data["marks"])
    total = float(data["total"])
    if total <= 0 or marks < 0 or marks > total:
        raise ValueError("Marks must be between zero and the positive total.")
    with connection() as conn:
        student = conn.execute(
            "SELECT * FROM students WHERE roll_no = ?", (data["roll_no"].strip(),)
        ).fetchone()
        if not student:
            raise LookupError("Student not found.")
        cur = conn.execute(
            """INSERT INTO scores(student_id, test_name, subject, marks, total, remark, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (student["id"], data["test_name"].strip(), data["subject"].strip(),
             marks, total, data.get("remark", "").strip(), now_iso()),
        )
        score = conn.execute("SELECT * FROM scores WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"student": dict(student), "score": dict(score)}


def stats() -> dict:
    with connection() as conn:
        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])
        return {
            "students": count("SELECT COUNT(*) FROM students"),
            "active_leads": count("SELECT COUNT(*) FROM leads WHERE status != 'closed'"),
            "pending_fees": count("SELECT COUNT(*) FROM fees WHERE status = 'pending'"),
            "overdue_fees": count("SELECT COUNT(*) FROM fees WHERE status = 'pending' AND due_date < date('now')"),
            "paid_fees": count("SELECT COUNT(*) FROM fees WHERE status = 'paid'"),
            "opted_out_contacts": count("SELECT COUNT(*) FROM contacts WHERE opted_out = 1"),
        }


def recent_leads(limit: int = 50) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, phone, name, course, class_name, batch, score, latest_message, status, created_at, updated_at "
            "FROM leads ORDER BY score DESC, updated_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]
