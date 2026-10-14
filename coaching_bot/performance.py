"""Student performance: parent-facing reports, score alerts, attendance alerts.

Three surfaces:
  performance_message()      - on-demand reply when a parent texts REPORT
  send_score_alert()         - instant push the moment a teacher enters a score
  run_fortnightly_reports()  - scheduled digest for every active student
"""
from __future__ import annotations

from datetime import timedelta

from . import config, store, whatsapp as wa


def _trend_word(delta: float | None) -> str:
    if delta is None:
        return "not enough data yet"
    if delta >= 5:
        return f"improving 📈 (+{delta:.1f}% vs previous tests)"
    if delta <= -5:
        return f"declining 📉 ({delta:.1f}% vs previous tests)"
    return f"steady ({delta:+.1f}% vs previous tests)"


def _subject_lines(subj: dict[str, float]) -> str:
    if not subj:
        return "   (no tests recorded yet)"
    return "\n".join(
        f"   • {k}: {v:.1f}%" + ("  ⭐" if v >= 80 else ("  ⚠️" if v < 50 else ""))
        for k, v in sorted(subj.items(), key=lambda kv: -kv[1]))


def performance_message(con, student_id: int, window_days: int = 60) -> str:
    """Compact WhatsApp-native report. Deliberately text, not PDF, so it lands
    in the notification preview where parents actually read it."""
    s = store.student_performance_summary(con, student_id, window_days)
    if not s:
        return "No performance data found for this student yet."
    stu = s["student"]
    att = s["attendance"]
    lines = [f"📊 *Performance report - {stu['name']}*",
             f"{stu['roll_no']} | {stu['batch']} | {stu['course_code']}",
             ""]
    if s["tests_in_window"]:
        lines.append(f"*Tests in last {window_days} days:* {s['tests_in_window']}")
        if s["overall_pct"] is not None:
            lines.append(f"*Overall average:* {s['overall_pct']:.1f}%")
        lines.append(f"*Trend:* {_trend_word(s['trend_delta'])}")
        lines.append("")
        lines.append("*Subject-wise average:*")
        lines.append(_subject_lines(s["subject_avg"]))
        lines.append("")
        if s["best_subject"]:
            lines.append(f"*Strongest:* {s['best_subject']} ✅")
        if s["weak_subject"]:
            lines.append(f"*Needs work:* {s['weak_subject']} - extra doubt-clearing "
                         f"sessions recommended")
    else:
        lines.append(f"No tests recorded in the last {window_days} days.")
    lines.append("")
    if att["pct"] is not None:
        flag = "⚠️ below 75% - please ensure regular attendance" if att["pct"] < 75 else "✅"
        lines.append(f"*Attendance (30 days):* {att['pct']:.0f}% "
                     f"({att['present']}/{att['total_marked']} classes) {flag}")
    latest = s["latest"]
    if latest:
        pct = 100.0 * latest["marks_obtained"] / latest["marks_total"] if latest["marks_total"] else 0
        lines.append("")
        lines.append(f"*Latest test:* {latest['test_name']} ({config.fmt_date(latest['test_date'])})")
        lines.append(f"   {latest['subject']}: {latest['marks_obtained']:.0f}/"
                     f"{latest['marks_total']:.0f} = *{pct:.0f}%*")
        if latest.get("batch_avg_pct") is not None:
            delta = pct - latest["batch_avg_pct"]
            cmp_txt = (f"{delta:+.0f}% vs batch average ({latest['batch_avg_pct']:.0f}%)")
            lines.append(f"   {cmp_txt}")
        if latest.get("rank_in_batch"):
            lines.append(f"   Batch rank: {latest['rank_in_batch']}")
        if latest.get("teacher_remark"):
            lines.append(f"   Teacher: _{latest['teacher_remark']}_")
    lines.append("")
    lines.append("Reply *TEACHER* to book a 10-minute call with the subject teacher.")
    return "\n".join(lines)


def send_score_alert(con, test_id: int) -> dict:
    """Instant push when a teacher enters a score. This is the single highest
    perceived-value feature for parents - they see the mark the same evening."""
    row = con.execute(
        "SELECT t.*, s.name AS student_name, s.parent_name, s.parent_phone, s.roll_no,"
        " s.batch FROM tests t JOIN students s ON s.id=t.student_id WHERE t.id=?",
        (test_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "test not found"}
    t = dict(row)
    if not t["marks_total"]:
        return {"ok": False, "error": "marks_total is zero"}
    pct = 100.0 * t["marks_obtained"] / t["marks_total"]
    inst = config.institute()
    policy = config.cfg()["performance_policy"]

    if pct >= 85:
        headline = "🌟 Excellent result!"
        advice = "Keep the momentum going. Consider the advanced problem-solving batch."
    elif pct >= 70:
        headline = "✅ Good result"
        advice = "On track. Focus on the 2-3 questions lost to silly mistakes."
    elif pct >= 50:
        headline = "📘 Needs attention"
        advice = "Book a doubt-clearing slot this week for this subject."
    else:
        headline = "⚠️ Below expected"
        advice = "Please speak to the subject teacher. We have scheduled remedial support."

    cmp_line = ""
    if t.get("batch_avg_pct") is not None:
        delta = pct - t["batch_avg_pct"]
        cmp_line = f"Batch average: {t['batch_avg_pct']:.0f}% ({delta:+.0f}% for {t['student_name'].split()[0]})\n"
        if (t["batch_avg_pct"] - pct) >= policy.get("alert_threshold_pct_below_batch_avg", 10):
            cmp_line += "This is well below the batch average - teacher has been notified.\n"
    if t.get("rank_in_batch"):
        cmp_line += f"Batch rank: {t['rank_in_batch']}\n"

    body = (f"{headline}\n\n"
            f"*{t['student_name']}* ({t['roll_no']})\n"
            f"Test: *{t['test_name']}*\n"
            f"Subject: {t['subject'] or 'General'}\n"
            f"Date: {config.fmt_date(t['test_date'])}\n\n"
            f"Score: *{t['marks_obtained']:.0f}/{t['marks_total']:.0f} = {pct:.0f}%*\n"
            f"{cmp_line}"
            + (f"Teacher's remark: _{t['teacher_remark']}_\n" if t.get("teacher_remark") else "")
            + f"\n{advice}\n\nReply *REPORT* for the full progress report, "
              f"*TEACHER* for a call.\n{inst['branding']['signature']}")
    out = wa.t(body)
    if not reminders_within_24h(con, t["parent_phone"]):
        out = wa.template("test_score_alert_v1", config.WA_TEMPLATE_LANG, body,
                          [{"type": "body", "parameters": [
                              {"type": "text", "text": t["student_name"]},
                              {"type": "text", "text": t["test_name"]},
                              {"type": "text", "text": f"{t['marks_obtained']:.0f}/{t['marks_total']:.0f}"},
                              {"type": "text", "text": f"{pct:.0f}%"}]}])
    res = wa.send(con, t["parent_phone"], out)
    con.execute("UPDATE tests SET alerted=1 WHERE id=?", (test_id,))
    con.commit()
    if cmp_line.startswith("This is well below"):
        store.log_alert(con, "low_score", f"{t['student_name']} scored {pct:.0f}% "
                        f"(batch avg {t['batch_avg_pct']:.0f}%) in {t['test_name']}",
                        phone=t["parent_phone"])
    return {"ok": res["ok"], "pct": pct, "phone": t["parent_phone"], "status": res["status"]}


def reminders_within_24h(con, phone: str) -> bool:
    from .reminders import within_24h_window
    return within_24h_window(con, phone)


def low_attendance_alerts(con) -> list[dict]:
    """Nudge parents whose child has slipped below the attendance threshold."""
    policy = config.cfg()["performance_policy"]
    threshold = float(policy.get("attendance_alert_below_pct", 75))
    out = []
    rows = con.execute("SELECT * FROM students WHERE status='active'").fetchall()
    for r in rows:
        stu = dict(r)
        att = store.attendance_stats(con, stu["id"], 30)
        if att["pct"] is None or att["total_marked"] < 5 or att["pct"] >= threshold:
            continue
        body = (f"📉 *Attendance alert - {stu['name']}*\n\n"
                f"Attendance in the last 30 days: *{att['pct']:.0f}%* "
                f"({att['present']}/{att['total_marked']} classes)\n"
                f"Absent: {att['absent']} | Late: {att['late']}\n\n"
                f"At this level, batch pace and test performance are affected. "
                f"Please reply *TEACHER* to speak with the batch coordinator.\n"
                f"{config.institute()['branding']['signature']}")
        res = wa.send(con, stu["parent_phone"], wa.t(body))
        store.log_alert(con, "low_attendance",
                        f"{stu['name']} attendance {att['pct']:.0f}%", phone=stu["parent_phone"],
                        dedupe_key=f"low_attendance:{stu['id']}:{config.today_ist():%Y-%m}")
        out.append({"student": stu["name"], "pct": att["pct"], "status": res["status"]})
    return out


def run_fortnightly_reports(con, dry_run: bool | None = None) -> dict:
    """Scheduled digest for every active student. Intended to run Saturday 18:30."""
    rows = con.execute("SELECT * FROM students WHERE status='active'").fetchall()
    sent = held = 0
    details = []
    quiet = config.in_quiet_hours()
    for r in rows:
        stu = dict(r)
        s = store.student_performance_summary(con, stu["id"], 14)
        if not s.get("tests_in_window") and (s.get("attendance") or {}).get("pct") is None:
            continue
        body = performance_message(con, stu["id"], 14)
        out = wa.t(body)
        if quiet:
            held += 1
            details.append({"student": stu["name"], "status": "held_quiet_hours"})
            continue
        res = wa.send(con, stu["parent_phone"], out)
        sent += 1 if res["ok"] else 0
        details.append({"student": stu["name"], "status": res["status"],
                        "pct": s.get("overall_pct")})
    return {"sent": sent, "held": held, "considered": len(rows), "details": details}


def report_card_html(con, student_id: int) -> str:
    """Printable one-page report card. Hand to the parent at PTM or attach as PDF."""
    s = store.student_performance_summary(con, student_id, 180)
    if not s:
        return "<h1>No data</h1>"
    stu, inst = s["student"], config.institute()
    subj_rows = "".join(
        f"<tr><td>{k}</td><td class='num'>{v:.1f}%</td>"
        f"<td class='num'>{'⭐' if v >= 80 else ('⚠️' if v < 50 else '')}</td></tr>"
        for k, v in sorted(s["subject_avg"].items(), key=lambda kv: -kv[1]))
    tests = store.student_tests(con, student_id, 12)
    test_rows = "".join(
        f"<tr><td>{config.fmt_date(t['test_date'])}</td><td>{t['test_name']}</td>"
        f"<td>{t['subject'] or '-'}</td>"
        f"<td class='num'>{t['marks_obtained']:.0f}/{t['marks_total']:.0f}</td>"
        f"<td class='num'>{100.0 * t['marks_obtained'] / t['marks_total']:.0f}%</td>"
        f"<td class='num'>{t['batch_avg_pct']:.0f}%</td>"
        f"<td class='num'>{t['rank_in_batch'] or '-'}</td></tr>"
        for t in tests if t["marks_total"])
    att = s["attendance"]
    att_txt = f"{att['pct']:.0f}%" if att["pct"] is not None else "n/a"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Report Card - {stu['name']}</title>
<style>
 body{{font-family:Georgia,serif;margin:36px;color:#1a1a1a}}
 h1{{margin:0;font-size:22px}} .muted{{color:#666;font-size:13px}}
 .head{{display:flex;justify-content:space-between;align-items:flex-start;
        border-bottom:3px solid {inst['branding']['primary_color']};padding-bottom:12px}}
 .kpis{{display:flex;gap:26px;margin:22px 0}}
 .kpi{{background:#f4f7f6;border-left:4px solid {inst['branding']['accent_color']};padding:10px 16px}}
 .kpi b{{display:block;font-size:24px}}
 table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}}
 th,td{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}}
 th{{background:#f0f0f0}} .num{{text-align:right}}
 h2{{font-size:15px;margin:22px 0 4px;text-transform:uppercase;letter-spacing:.06em;color:#444}}
 .sign{{margin-top:34px;display:flex;justify-content:space-between;font-size:12px;color:#555}}
</style></head><body>
<div class="head">
  <div><h1>{inst['name']}</h1><div class="muted">{inst['tagline']} &nbsp;|&nbsp; {', '.join(inst['branches'])}</div></div>
  <div class="muted" style="text-align:right">Progress Report<br>Generated {config.fmt_date(config.today_ist())}</div>
</div>
<div class="kpis">
  <div class="kpi"><span class="muted">Student</span><b>{stu['name']}</b>{stu['roll_no']} | {stu['batch']}</div>
  <div class="kpi"><span class="muted">Overall average</span><b>{s['overall_pct']:.1f}%</b>{_trend_word(s['trend_delta'])}</div>
  <div class="kpi"><span class="muted">Attendance (30d)</span><b>{att_txt}</b>{att['present']}/{att['total_marked']} classes</div>
  <div class="kpi"><span class="muted">Tests taken</span><b>{s['tests_total']}</b>all time</div>
</div>
<h2>Subject-wise average</h2>
<table><thead><tr><th>Subject</th><th class="num">Average</th><th class="num">Flag</th></tr></thead>
<tbody>{subj_rows or '<tr><td colspan="3">No tests recorded</td></tr>'}</tbody></table>
<h2>Recent tests</h2>
<table><thead><tr><th>Date</th><th>Test</th><th>Subject</th><th class="num">Score</th>
<th class="num">%</th><th class="num">Batch avg</th><th class="num">Rank</th></tr></thead>
<tbody>{test_rows or '<tr><td colspan="7">No tests recorded</td></tr>'}</tbody></table>
<h2>Coordinator remarks</h2>
<p>Strongest area: <b>{s['best_subject'] or '-'}</b>. Focus area: <b>{s['weak_subject'] or '-'}</b>.
Attendance is {'satisfactory' if (att['pct'] or 0) >= 75 else 'below the 75% requirement'}.</p>
<div class="sign"><div>Class teacher ____________________</div>
<div>Centre head ____________________</div><div>{inst['phone']}</div></div>
</body></html>"""