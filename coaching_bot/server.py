"""Webhook + admin HTTP server. Stdlib only, so it runs anywhere Python does.

Routes
------
GET  /health                 liveness for uptime monitors
GET  /webhook                Meta webhook verification handshake
POST /webhook                Meta inbound messages -> engine -> replies
GET  /report/<roll_no>       printable report card (HTML)
GET  /dashboard              operator dashboard (read-only)
POST /admin/mark-paid        record a payment + send receipt
POST /admin/score            enter a test score + push alert to parent
POST /admin/run-reminders    force a reminder tick (cron / manual)
POST /admin/broadcast        send a message to a filtered set of parents

Admin routes require the X-Admin-Token header (env ADMIN_TOKEN).
Run behind Caddy/nginx with TLS; Meta requires HTTPS for the webhook.
"""
from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, flows, performance, reminders, seed, store, whatsapp as wa

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
MAX_BODY = 1_000_000


def _json(handler, obj, code: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html(handler, html: str, code: int = 200) -> None:
    body = html.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text(handler, text: str, code: int = 200) -> None:
    body = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "CoachingBot/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs
        if os.environ.get("COACH_VERBOSE"):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------ auth
    def _authed(self) -> bool:
        # Fail closed: an unset secret must never make admin routes public.
        # Set ADMIN_TOKEN before using any /admin/* route.
        return bool(ADMIN_TOKEN) and self.headers.get("X-Admin-Token", "") == ADMIN_TOKEN

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------ GET
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            return _json(self, {"ok": True, "demo_mode": config.demo_mode(),
                                "version": __import__("coaching_bot").__version__})
        if u.path == "/webhook":
            mode = (q.get("hub.mode") or [""])[0]
            token = (q.get("hub.verify_token") or [""])[0]
            challenge = (q.get("hub.challenge") or [""])[0]
            if mode == "subscribe" and token == config.WA_VERIFY_TOKEN:
                return _text(self, challenge)
            return _text(self, "verification failed", 403)
        if u.path.startswith("/report/"):
            roll = u.path.split("/report/", 1)[1].strip("/")
            con = store.connect()
            try:
                stu = store.student_by_roll(con, roll)
                if not stu:
                    return _html(self, "<h1>Student not found</h1>", 404)
                return _html(self, performance.report_card_html(con, stu["id"]))
            finally:
                con.close()
        if u.path in ("/", "/dashboard"):
            return _html(self, self._dashboard())
        return _json(self, {"error": "not found", "path": u.path}, 404)

    # ------------------------------------------------------------------ POST
    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        try:
            if u.path == "/webhook":
                return self._webhook()
            if u.path.startswith("/admin/"):
                if not self._authed():
                    return _json(self, {"error": "unauthorized"}, 401)
                return self._admin(u.path.replace("/admin/", ""))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _json(self, {"error": "internal", "detail": traceback.format_exc()[-800:]}, 500)
        return _json(self, {"error": "not found"}, 404)

    # ------------------------------------------------------------- handlers
    def _webhook(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        events = wa.parse_inbound(raw)
        con = store.connect()
        handled = []
        try:
            for ev in events:
                if ev["kind"] == "status":
                    con.execute("UPDATE messages SET status=? WHERE wamid=?",
                                (ev["text"], ev["id"]))
                    con.commit()
                    continue
                phone = ev["phone"]
                engine = flows.Engine(con)
                outs = engine.handle(phone, ev.get("text") or "", kind=ev["kind"],
                                     name=ev.get("name"))
                for out in outs:
                    wa.send(con, phone, out)
                if outs:
                    store.save_conv(con, phone,
                                    store.get_conv(con, phone)["state"],
                                    store.get_conv(con, phone)["ctx"], inbound=False)
                handled.append({"phone": phone, "in": ev.get("text"), "replies": len(outs)})
        finally:
            con.close()
        return _json(self, {"ok": True, "received": len(events), "handled": handled})

    def _admin(self, action: str):
        data = self._body()
        con = store.connect()
        try:
            if action == "mark-paid":
                stu = store.student_by_roll(con, data["roll_no"]) if data.get("roll_no") else None
                if not stu:
                    return _json(self, {"error": "roll_no required and must exist"}, 400)
                fee_id = data.get("fee_schedule_id")
                if not fee_id:
                    pend = store.pending_fees(con, stu["id"])
                    fee_id = pend[0]["id"] if pend else None
                amount = float(data.get("amount") or 0)
                if not amount and fee_id:
                    row = con.execute("SELECT * FROM fee_schedule WHERE id=?", (fee_id,)).fetchone()
                    amount = store.fee_outstanding(dict(row))
                store.record_payment(con, stu["id"], fee_id, amount,
                                     method=data.get("method", "UPI"),
                                     reference=data.get("reference"),
                                     recorded_by=data.get("by", "accounts"))
                res = reminders.send_receipt(con, stu["id"], fee_id, amount,
                                            method=data.get("method", "UPI"),
                                            reference=data.get("reference"))
                return _json(self, {"ok": True, "student": stu["name"], "amount": amount,
                                    "receipt": res["status"]})
            if action == "score":
                stu = store.student_by_roll(con, data["roll_no"])
                if not stu:
                    return _json(self, {"error": "unknown roll_no"}, 400)
                cur = con.execute(
                    "INSERT INTO tests(student_id, test_name, test_date, subject, marks_obtained,"
                    " marks_total, batch_avg_pct, batch_topper_pct, rank_in_batch, teacher_remark)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (stu["id"], data["test_name"], data.get("test_date") or store.ds(),
                     data.get("subject"), float(data["marks_obtained"]),
                     float(data["marks_total"]), data.get("batch_avg_pct"),
                     data.get("batch_topper_pct"), data.get("rank_in_batch"),
                     data.get("teacher_remark")))
                con.commit()
                alert = performance.send_score_alert(con, cur.lastrowid)
                return _json(self, {"ok": True, "student": stu["name"], "alert": alert})
            if action == "run-reminders":
                report = reminders.run(con, force_quiet=bool(data.get("force_quiet")))
                return _json(self, {"ok": True, "report": report})
            if action == "broadcast":
                # filter: course / batch / class / all
                sql = "SELECT * FROM students WHERE status='active'"
                args = []
                for field in ("course_code", "batch", "student_class", "branch"):
                    if data.get(field):
                        sql += f" AND {field}=?"
                        args.append(data[field])
                rows = con.execute(sql, args).fetchall()
                sent = 0
                for r in rows:
                    res = wa.send(con, r["parent_phone"], wa.t(data["message"]))
                    sent += 1 if res["ok"] else 0
                return _json(self, {"ok": True, "targets": len(rows), "sent": sent})
            if action == "stats":
                return _json(self, {"ok": True, "kpi": seed.kpi_snapshot(con)})
            if action == "fortnightly-reports":
                perf = performance.run_fortnightly_reports(con)
                att = performance.low_attendance_alerts(con)
                return _json(self, {"ok": True, "performance": perf,
                                    "low_attendance_alerts": att})
            if action == "leads":
                rows = con.execute(
                    "SELECT * FROM leads WHERE status NOT IN ('won','lost')"
                    " ORDER BY score DESC, updated_at DESC LIMIT ?",
                    (int(data.get("limit", 50)),)).fetchall()
                return _json(self, {"ok": True, "leads": [dict(r) for r in rows]})
            if action == "alerts":
                return _json(self, {"ok": True,
                                    "alerts": store.recent_alerts(con, int(data.get("limit", 25)))})
            if action == "overdue":
                rows = store.overdue_fees(con)
                return _json(self, {"ok": True, "count": len(rows),
                                    "total": sum(store.fee_outstanding(r) for r in rows),
                                    "fees": [{"student": r["student_name"], "roll": r["roll_no"],
                                              "label": r["label"], "due_date": r["due_date"],
                                              "outstanding": store.fee_outstanding(r),
                                              "last_stage": r.get("last_reminder_stage")}
                                             for r in rows]})
            if action == "reset-demo":
                info = seed.seed(con)
                return _json(self, {"ok": True, "seeded": info["students"]})
        finally:
            con.close()
        return _json(self, {"error": f"unknown admin action {action}"}, 404)

    # ------------------------------------------------------------ dashboard
    def _dashboard(self) -> str:
        con = store.connect()
        try:
            k = seed.kpi_snapshot(con)
            leads = store.hot_leads(con, 10)
            overdue = store.overdue_fees(con)[:10]
            alerts = store.recent_alerts(con, 10)
            demo = config.demo_mode()
        finally:
            con.close()
        lead_rows = "".join(
            f"<tr><td>{l['name'] or '-'}</td><td>{l['phone']}</td>"
            f"<td>{l['course_code'] or '-'}</td><td>{l['student_class'] or '-'}</td>"
            f"<td>{l['batch_pref'] or '-'}</td><td class=n>{l['score']}</td>"
            f"<td><b>{l['temperature']}</b></td><td>{(l['notes'] or '')[:48]}</td></tr>"
            for l in leads) or "<tr><td colspan=8>No hot leads</td></tr>"
        od_rows = "".join(
            f"<tr><td>{f['student_name']}</td><td>{f['roll_no']}</td>"
            f"<td>{f['label']}</td><td class=n>{config.inr(store.fee_outstanding(f))}</td>"
            f"<td>{config.fmt_date(f['due_date'])}</td>"
            f"<td class=n>{(config.today_ist() - __import__('datetime').date.fromisoformat(f['due_date'])).days}</td>"
            f"<td>{f['last_reminder_stage'] or '-'}</td></tr>"
            for f in overdue) or "<tr><td colspan=7>Nothing overdue</td></tr>"
        alert_rows = "".join(
            f"<tr><td>{a['ts'][:16].replace('T',' ')}</td><td>{a['kind']}</td>"
            f"<td>{(a['body'] or '').splitlines()[0][:90]}</td></tr>" for a in alerts)
        return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Coaching Automation - Dashboard</title><style>
 body{{font-family:system-ui,Segoe UI,sans-serif;margin:28px;background:#0f1512;color:#e8f0ec}}
 h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:14px;text-transform:uppercase;
 letter-spacing:.08em;color:#8fbfa8;margin:26px 0 8px}}
 .badge{{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;
 background:#1e3a2c;color:#7ee2a8;border:1px solid #2e5c44}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 th,td{{padding:7px 9px;border-bottom:1px solid #22322b;text-align:left}}
 th{{color:#8fbfa8;font-weight:600}} .n{{text-align:right}}
 .kpis{{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px}}
 .kpi{{background:#16211c;border:1px solid #24382e;border-radius:10px;padding:12px 18px;min-width:150px}}
 .kpi span{{display:block;font-size:11px;color:#8fbfa8;text-transform:uppercase;letter-spacing:.06em}}
 .kpi b{{font-size:22px}}
</style></head><body>
<h1>{config.institute()['name']} - Automation Dashboard</h1>
<div class="badge">{'DEMO MODE - nothing is actually sent' if demo else 'LIVE - connected to WhatsApp'}</div>
<div class="kpis">
 <div class="kpi"><span>Active students</span><b>{k['students_active']}</b></div>
 <div class="kpi"><span>Open leads</span><b>{k['open_leads']}</b></div>
 <div class="kpi"><span>Hot leads</span><b>{k['hot_leads']}</b></div>
 <div class="kpi"><span>Collected today</span><b>{config.inr(k['collected_today'])}</b></div>
 <div class="kpi"><span>Collected this month</span><b>{config.inr(k['collected_month'])}</b></div>
 <div class="kpi"><span>Outstanding</span><b>{config.inr(k['outstanding_total'])}</b></div>
 <div class="kpi"><span>Overdue</span><b>{config.inr(k['overdue_total'])}</b></div>
 <div class="kpi"><span>Reminders sent</span><b>{k['reminders_sent']}</b></div>
 <div class="kpi"><span>Reminders queued</span><b>{k['reminders_queued']}</b></div>
 <div class="kpi"><span>Messages out</span><b>{k['messages_out']}</b></div>
</div>
<h2>Hot leads waiting on a callback</h2>
<table><thead><tr><th>Name</th><th>Phone</th><th>Course</th><th>Class</th><th>Batch pref</th>
<th class=n>Score</th><th>Temp</th><th>Notes</th></tr></thead><tbody>{lead_rows}</tbody></table>
<h2>Overdue fees</h2>
<table><thead><tr><th>Student</th><th>Roll</th><th>Fee</th><th class=n>Amount</th><th>Due</th>
<th class=n>Days late</th><th>Last stage</th></tr></thead><tbody>{od_rows}</tbody></table>
<h2>Recent alerts to staff</h2>
<table><thead><tr><th>When</th><th>Kind</th><th>Message</th></tr></thead><tbody>{alert_rows}</tbody></table>
</body></html>"""


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    # Initialise the schema, then release the handle immediately. Holding it open
    # for the process lifetime blocks `sqlite3 .backup` and file replacement
    # during a deploy, for no benefit - each request opens its own connection.
    init_con = store.init()
    init_con.close()
    httpd = ThreadingHTTPServer((host, port), Handler)
    mode = "DEMO (dry-run)" if config.demo_mode() else "LIVE"
    print(f"Coaching automation webhook server on http://{host}:{port}  [{mode}]")
    print(f"  GET  /health        POST /webhook        GET /dashboard")
    print(f"  GET  /report/<roll> POST /admin/mark-paid|score|run-reminders|broadcast")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


if __name__ == "__main__":
    serve(port=int(os.environ.get("PORT", "8080")))