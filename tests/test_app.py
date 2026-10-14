"""Regression tests for the core persistence and reminder logic."""
from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
import tempfile
import unittest

from coaching_bot import db


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["COACH_DB"] = str(Path(self.tempdir.name) / "test.db")
        db.init_db()

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("COACH_DB", None)

    def test_inbound_message_is_idempotent(self) -> None:
        self.assertTrue(db.record_inbound("wamid-1", "919999999999", "hello"))
        self.assertFalse(db.record_inbound("wamid-1", "919999999999", "hello"))
        self.assertEqual(db.stats()["active_leads"], 0)

    def test_opt_out_is_persisted(self) -> None:
        db.record_inbound("wamid-2", "919999999999", "STOP")
        db.set_opt_out("919999999999", True)
        self.assertTrue(db.is_opted_out("919999999999"))
        db.record_inbound("wamid-3", "919999999999", "another message")
        self.assertTrue(db.is_opted_out("919999999999"))

    def test_reminder_stages_skip_missed_old_stages(self) -> None:
        self.assertEqual(db.reminder_stage("2026-10-08", date(2026, 10, 15)), "D+7")
        self.assertEqual(db.reminder_stage("2026-10-01", date(2026, 10, 15)), "D+15")
        self.assertEqual(db.reminder_stage("2026-10-22", date(2026, 10, 15)), "T-7")
        self.assertIsNone(db.reminder_stage("2026-10-20", date(2026, 10, 15)))

    def test_fee_paid_transition(self) -> None:
        student = db.create_student({"roll_no": "R-100", "full_name": "Demo Student"})
        fee = db.create_fee({
            "student_id": student["id"], "description": "Monthly fee",
            "amount": 1200, "due_date": "2026-10-15",
        })
        paid = db.mark_fee_paid(fee["id"], "UPI")
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(paid["payment_method"], "UPI")
        self.assertEqual(db.stats()["pending_fees"], 0)

    def test_score_validation_and_storage(self) -> None:
        db.create_student({"roll_no": "R-200", "full_name": "Demo Learner"})
        result = db.add_score({
            "roll_no": "R-200", "test_name": "Weekly test", "subject": "Maths",
            "marks": 8, "total": 10, "remark": "Good progress",
        })
        self.assertEqual(result["score"]["marks"], 8)
        with self.assertRaises(ValueError):
            db.add_score({
                "roll_no": "R-200", "test_name": "Bad test", "subject": "Maths",
                "marks": 12, "total": 10,
            })


if __name__ == "__main__":
    unittest.main()
