"""Operator CLI.

    python -m coaching_bot.cli seed                    # load realistic demo data
    python -m coaching_bot.cli demo                    # run scripted conversations,
                                                       # write out/demo_transcript.html
    python -m coaching_bot.cli reminders --dry-run     # one scheduler tick
    python -m coaching_bot.cli reminders --live        # actually send (needs creds)
    python -m coaching_bot.cli score --roll NEET-2026-100 --test "Weekly Test 10" \
            --subject Physics --marks 72 --total 100 --batch-avg 61 --rank 14
    python -m coaching_bot.cli paid --roll NEET-2026-100 --amount 25000
    python -m coaching_bot.cli report --roll NEET-2026-100        # report card HTML
    python -m coaching_bot.cli reportcard-all
    python -m coaching_bot.cli stats
    python -m coaching_bot.cli serve --port 8080
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import textwrap
from datetime import timedelta

from . import config, flows, performance, reminders, seed, store, whatsapp as wa


def _con():
    return store.init()


# ---------------------------------------------------------------------------
def cmd_seed(args):
    con = _con()
    info = seed.seed(con, n_students=args.students, wipe=not args.append)
    print(f"Seeded {info['students']} students "
          f"({info['real_students']} normal + {len(info['showcase'])} reminder showcase), "
          f"{info['leads']} leads.")
    print(f"Database: {config.DB_PATH}")
    print("\nReminder showcase (one fee pinned to each stage offset):")
    for s in info["showcase"]:
        print(f"  {s['stage']:>4}  {s['student']:<20} {s['roll']:<14} "
              f"{config.inr(s['amount']):>12}  due {s['due']}  {s['phone']}")
    k = seed.kpi_snapshot(con)
    print("\nKPI:", json.dumps(k, indent=2))
    con.close()


def cmd_reminders(args):
    con = _con()
    report = reminders.run(con, force_quiet=args.force_quiet,
                           dry_run=(True if args.dry_run else (False if args.live else None)))
    mode = "LIVE" if args.live else "DRY-RUN"
    print(f"Reminder tick [{mode}]  queued={report['queued']} sent={report['sent']} "
          f"held={report['held']} skipped={report['skipped']} failed={report['failed']} "
          f"escalated={report['escalated']}")
    if report["details"]:
        print("\n" + f"{'student':<20}{'stage':>6}  {'channel':<10}{'status':<11}amount")
        print("-" * 62)
        for d in report["details"]:
            print(f"{d['student']:<20}{d['stage']:>6}  {d['channel']:<10}"
                  f"{d['status']:<11}{config.inr(d['amount'])}")
    print(f"\nOutbox: {config.OUTBOX_PATH}")
    con.close()


def cmd_score(args):
    con = _con()
    stu = store.student_by_roll(con, args.roll)
    if not stu:
        print(f"No student with roll {args.roll}", file=sys.stderr)
        return 1
    cur = con.execute(
        "INSERT INTO tests(student_id, test_name, test_date, subject, marks_obtained,"
        " marks_total, batch_avg_pct, batch_topper_pct, rank_in_batch, teacher_remark)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (stu["id"], args.test, args.date or store.ds(), args.subject, args.marks, args.total,
         args.batch_avg, args.topper, args.rank, args.remark))
    con.commit()
    res = performance.send_score_alert(con, cur.lastrowid)
    print(f"Score entered for {stu['name']} ({args.roll}): "
          f"{args.marks}/{args.total} = {res.get('pct', 0):.0f}%")
    print(f"Parent alert -> {res.get('phone')} [{res.get('status')}]")
    con.close()


def cmd_paid(args):
    con = _con()
    stu = store.student_by_roll(con, args.roll)
    if not stu:
        print(f"No student with roll {args.roll}", file=sys.stderr)
        return 1
    pend = store.pending_fees(con, stu["id"])
    fee_id = pend[0]["id"] if pend else None
    amount = args.amount or (store.fee_outstanding(pend[0]) if pend else 0)
    store.record_payment(con, stu["id"], fee_id, amount, method=args.method,
                         reference=args.reference, recorded_by="cli")
    res = reminders.send_receipt(con, stu["id"], fee_id, amount, method=args.method,
                                reference=args.reference)
    print(f"Payment recorded: {stu['name']} ({args.roll}) {config.inr(amount)} via {args.method}")
    print(f"Receipt -> {stu['parent_phone']} [{res['status']}]")
    remaining = store.pending_fees(con, stu["id"])
    print(f"Pending fees remaining: {len(remaining)} "
          f"({config.inr(sum(store.fee_outstanding(f) for f in remaining))})")
    con.close()


def cmd_report(args):
    con = _con()
    stu = store.student_by_roll(con, args.roll)
    if not stu:
        print(f"No student with roll {args.roll}", file=sys.stderr)
        return 1
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUT_DIR / f"report_card_{args.roll}.html"
    path.write_text(performance.report_card_html(con, stu["id"]), encoding="utf-8")
    print(f"Report card written: {path}")
    print(f"\nWhatsApp report message preview:\n{'-' * 60}")
    print(performance.performance_message(con, stu["id"]))
    con.close()


def cmd_reportcard_all(args):
    con = _con()
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = con.execute("SELECT * FROM students WHERE status='active' ORDER BY roll_no").fetchall()
    for r in rows:
        (config.OUT_DIR / f"report_card_{r['roll_no']}.html").write_text(
            performance.report_card_html(con, r["id"]), encoding="utf-8")
    print(f"Wrote {len(rows)} report cards to {config.OUT_DIR}")
    con.close()


def cmd_fortnightly(args):
    con = _con()
    report = performance.run_fortnightly_reports(con)
    print(f"Fortnightly performance digest: sent={report['sent']} held={report['held']} "
          f"considered={report['considered']}")
    alerts = performance.low_attendance_alerts(con)
    print(f"Low-attendance alerts sent: {len(alerts)}")
    for a in alerts:
        print(f"  {a['student']}: {a['pct']:.0f}% [{a['status']}]")
    con.close()


def cmd_stats(args):
    con = _con()
    k = seed.kpi_snapshot(con)
    print(json.dumps(k, indent=2))
    print("\nRecent alerts:")
    for a in store.recent_alerts(con, 8):
        print(f"  [{a['kind']:<18}] {(a['body'] or '').splitlines()[0][:88]}")
    con.close()


def cmd_serve(args):
    from . import server
    server.serve(host=args.host, port=args.port)


# ---------------------------------------------------------------------------
# Scripted demo: runs REAL engine turns and renders a WhatsApp-style transcript.
# ---------------------------------------------------------------------------
SCENARIOS = [
    {
        "title": "1. New admission enquiry (unknown number)",
        "subtitle": "Answers instantly at 11:42 PM - after office hours",
        "phone": "+919812300001",
        "turns": ["Hi", "ADM", "COURSE_JEE", "CLASS_11", "Aarav Deshmukh", "BATCH_0",
                  "BOOK_DEMO", "CALL_EVENING"],
    },
    {
        "title": "2. Parent asks fee status, then says PAID",
        "subtitle": "Self-serve dues + payment claim routed to accounts",
        "phone": None,          # filled at runtime with a seeded parent
        "turns": ["Hi", "FEE", "I have paid by UPI just now"],
    },
    {
        "title": "3. Parent asks for the performance report",
        "subtitle": "Marks, subject-wise average, attendance, trend",
        "phone": None,
        "turns": ["Hi", "REPORT"],
    },
    {
        "title": "4. STOP is honoured, then START resumes",
        "subtitle": "DPDP / TRAI compliance - opt-out always wins",
        "phone": "+919812300004",
        "turns": ["hi", "stop", "start"],
    },
]


def _bubble_html(msg: dict, seq: int) -> str:
    kind = msg["kind"]
    body = html.escape(msg.get("body") or "").replace("\n", "<br>")
    extra = ""
    if kind == "buttons":
        btns = "".join(f'<span class="btn">{html.escape(b["title"])}</span>'
                       for b in msg.get("buttons", []))
        extra = f'<div class="btns">{btns}</div>'
    elif kind == "list":
        rows = ""
        for sec in msg.get("sections", []):
            rows += f'<div class="secttl">{html.escape(sec.get("title", ""))}</div>'
            for r in sec.get("rows", []):
                desc = f'<small>{html.escape(r.get("description", ""))}</small>' if r.get("description") else ""
                rows += (f'<div class="row"><b>{html.escape(r["title"])}</b>{desc}</div>')
        extra = (f'<div class="listcard"><div class="listbtn">'
                 f'{html.escape(msg.get("button", "Choose"))} ▾</div>{rows}</div>')
    elif kind == "template":
        extra = f'<div class="tag">template: {html.escape(msg.get("name", ""))}</div>'
    return f'<div class="msg out" data-seq="{seq}"><div class="bubble">{body}{extra}' \
           f'<span class="tick">✓✓</span></div></div>'


def _in_html(text: str, kind: str = "text") -> str:
    label = ""
    if kind == "button":
        label = '<div class="tag">tapped a button</div>'
    return (f'<div class="msg in"><div class="bubble">{html.escape(text)}'
            f'{label}<span class="tick"></span></div></div>')


def cmd_demo(args):
    con = _con()
    if args.reseed or not con.execute("SELECT COUNT(*) n FROM students").fetchone()["n"]:
        seed.seed(con)
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)

    # pick a seeded parent that has pending fees, and one with test data
    fee_parent = con.execute(
        "SELECT s.parent_phone, s.name FROM students s JOIN fee_schedule f ON f.student_id=s.id"
        " WHERE f.status IN ('pending','partial') AND s.roll_no NOT LIKE 'SHOW-%'"
        " ORDER BY f.due_date LIMIT 1").fetchone()
    perf_parent = con.execute(
        "SELECT s.parent_phone, s.name FROM students s JOIN tests t ON t.student_id=s.id"
        " WHERE s.roll_no NOT LIKE 'SHOW-%' GROUP BY s.id HAVING COUNT(t.id)>=3"
        " ORDER BY s.roll_no LIMIT 1").fetchone()

    scenario_meta = {}
    panels = []
    for sc in SCENARIOS:
        phone = sc["phone"]
        who = sc["title"]
        if phone is None:
            if "fee" in sc["title"].lower():
                phone, who = fee_parent["parent_phone"], f"parent of {fee_parent['name']}"
            else:
                phone, who = perf_parent["parent_phone"], f"parent of {perf_parent['name']}"
        phone = store.normalize_phone(phone)
        # start from a clean slate for this number so the demo is deterministic
        con.execute("DELETE FROM conversations WHERE phone=?", (phone,))
        con.commit()
        scenario_meta[sc["title"]] = {"phone": phone, "who": who}

        bubbles = []
        engine = flows.Engine(con)
        for turn in sc["turns"]:
            kind = "text"
            if turn.upper().startswith(("COURSE_", "CLASS_", "BATCH_")) or \
               turn.upper() in ("ADM", "BOOK_DEMO", "HANDOFF", "PAID", "FEE", "REPORT"):
                kind = "button"
            bubbles.append(_in_html(turn, kind))
            outs = engine.handle(phone, turn, kind=kind)
            for out in outs:
                wa.send(con, phone, out)          # records to DB + outbox.jsonl
                bubbles.append(_bubble_html(out, len(bubbles)))
            # owner/counsellor copies go to a staff number - show them too
            if not outs:
                bubbles.append('<div class="msg sys"><div class="bubble">'
                               '<i>(no reply - number is opted out, as required)</i></div></div>')
        state = store.get_conv(con, phone)
        panels.append({"title": sc["title"], "subtitle": sc["subtitle"], "phone": phone,
                       "who": who, "bubbles": "".join(bubbles),
                       "state": state["state"], "turns": state["turns"]})

    # Run one scheduler tick BEFORE collecting staff messages, so the escalation
    # alerts it produces are part of the transcript the prospect sees.
    reminders.run(con, force_quiet=True, dry_run=True)

    # staff-facing copies: pull what the engine pushed to the owner/accounts numbers
    staff = []
    for n in (config.institute()["counsellor_phone"], config.institute()["accounts_alert_phone"]):
        rows = con.execute("SELECT * FROM messages WHERE phone=? AND direction='out' ORDER BY id",
                           (store.normalize_phone(n),)).fetchall()
        if rows:
            staff.append({"number": n, "count": len(rows),
                          "items": [{"body": r["body"], "kind": r["kind"]} for r in rows]})

    ladder = _reminder_ladder(con)
    kpi = seed.kpi_snapshot(con)

    out = _render_demo_html(panels, staff, kpi, ladder)
    path = config.OUT_DIR / "demo_transcript.html"
    path.write_text(out, encoding="utf-8")
    # A compact, chat-embeddable view: the admission conversation and the fee
    # ladder side by side. Used to demo the system inside a chat window.
    (config.OUT_DIR / "preview.html").write_text(
        _render_preview_html(panels, ladder, kpi), encoding="utf-8")
    print(f"Demo transcript written: {path}")
    print(f"Chat preview written:   {config.OUT_DIR / 'preview.html'}")
    print(f"Scenarios: {len(panels)} | messages logged: {kpi['messages_out']} "
          f"| reminders sent: {kpi['reminders_sent']} | alerts: {kpi['alerts']}")
    for p in panels:
        print(f"  - {p['title']}  [{p['who']}]  final state={p['state']} turns={p['turns']}")
    print(f"  - Reminder ladder: {len(ladder)} stages rendered")
    con.close()
    return 0


def _reminder_ladder(con) -> list[dict]:
    """The seven reminder stages, in order, with the real message body that was
    sent to each showcase parent. This is the panel that sells the fee module."""
    order = [s["key"] for s in config.cfg()["fee_reminder_policy"]["stages"]]
    rows = con.execute(
        "SELECT r.stage, r.status, r.body, r.sent_at, r.scheduled_for,"
        " s.name AS student_name, s.parent_phone, s.roll_no,"
        " f.label, f.amount, f.paid_amount, f.due_date, f.status AS fee_status"
        " FROM reminder_log r"
        " JOIN students s ON s.id=r.student_id"
        " JOIN fee_schedule f ON f.id=r.fee_schedule_id"
        " WHERE s.roll_no LIKE 'SHOW-%'").fetchall()
    by_stage = {}
    for r in rows:
        d = dict(r)
        by_stage.setdefault(d["stage"], d)
    out = []
    for key in order:
        d = by_stage.get(key)
        if d:
            d["tone"] = (reminders.stage_cfg(key) or {}).get("tone", "")
            out.append(d)
    return out


def _render_preview_html(panels, ladder, kpi) -> str:
    """Compact two-column view for embedding in a chat window: the admission
    conversation on the left, the fee reminder ladder on the right. Uses the app
    theme variables so it inherits whatever the host is themed with."""
    inst = config.institute()
    admission = panels[0] if panels else {"bubbles": "", "title": "", "who": "", "phone": ""}
    cards = ""
    for row in ladder:
        status = row.get("status") or "queued"
        badge = {"sent": "sent", "held_conversation": "held", "held_quiet_hours": "held",
                 "queued": "scheduled", "skipped": "superseded"}.get(status, status)
        cards += (
            f'<div class="st"><div class="sth"><b>{html.escape(row["stage"])}</b>'
            f'<span class="tone">{html.escape(row["tone"] or "")}</span>'
            f'<span class="bd bd-{html.escape(status)}">{html.escape(badge)}</span></div>'
            f'<div class="sw">{html.escape(row["student_name"])} &middot; '
            f'{config.inr(store.fee_outstanding(row))} &middot; '
            f'due {config.fmt_date(row["due_date"])}</div>'
            f'<div class="bb">{html.escape(row.get("body") or "")}</div></div>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(inst['name'])} - automation preview</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;font-family:inherit;color:var(--foreground,#e6ece9);font-size:13px}}
 .head{{margin-bottom:12px}}
 .head b{{font-size:15px}}
 .head .m{{color:var(--muted-foreground,#8b9c94);font-size:12px}}
 .row{{display:flex;gap:18px;align-items:flex-start;flex-wrap:nowrap}}
 .col{{flex:0 0 330px}}
 .col2{{flex:1 1 auto;min-width:390px;max-width:440px}}
 .lbl{{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
      color:var(--muted-foreground,#8b9c94);margin:0 0 6px}}
 .phone{{border:1px solid var(--border,#2a3833);border-radius:12px;overflow:hidden}}
 .phead{{display:flex;gap:9px;align-items:center;background:#075E54;padding:9px 11px;color:#fff}}
 .phead small{{display:block;color:#c8e6d8;font-size:10px}}
 .av{{width:28px;height:28px;border-radius:50%;background:#25D366;color:#05301f;
     display:grid;place-items:center;font-weight:700;font-size:13px}}
 .chat{{padding:10px 9px;max-height:430px;overflow-y:auto;background:#0d1a20}}
 .msg{{display:flex;margin:6px 0}}
 .msg.in{{justify-content:flex-start}} .msg.out{{justify-content:flex-end}}
 .msg.sys{{justify-content:center}}
 .bubble{{max-width:88%;padding:6px 10px 14px;border-radius:9px;font-size:12.2px;
         line-height:1.45;position:relative;word-wrap:break-word;color:#111}}
 .msg.in .bubble{{background:#fff;border-top-left-radius:2px}}
 .msg.out .bubble{{background:#DCF8C6;border-top-right-radius:2px}}
 .msg.sys .bubble{{background:#1b2b33;color:#8b9c94;font-size:11px;padding:5px 9px}}
 .tick{{position:absolute;right:7px;bottom:2px;font-size:9px;color:#5b8a6a}}
 .btns{{display:flex;flex-direction:column;gap:4px;margin-top:8px}}
 .btn{{background:#eef7f1;border:1px solid #cfe4d6;color:#0b5c3d;border-radius:5px;
      padding:5px 8px;font-size:11.4px;text-align:center;font-weight:600}}
 .lc{{margin-top:8px;border:1px solid #cfe4d6;border-radius:7px;overflow:hidden;background:#f7fbf9}}
 .lb{{background:#eef7f1;padding:6px 9px;font-size:11.4px;font-weight:700;color:#0b5c3d;
     text-align:center;border-bottom:1px solid #cfe4d6}}
 .stt{{font-size:9.5px;text-transform:uppercase;letter-spacing:.05em;color:#5d7a6a;
      padding:6px 9px 2px;font-weight:700}}
 .rr{{padding:4px 9px;border-top:1px solid #e6f0ea;font-size:11.4px}}
 .rr small{{display:block;color:#6b8578;font-size:10px}}
 .st{{border:1px solid var(--border,#2a3833);border-radius:10px;padding:8px 10px;margin-bottom:8px}}
 .sth{{display:flex;align-items:center;gap:7px;margin-bottom:4px}}
 .sth b{{background:var(--accent,#1d2b26);border-radius:5px;padding:1px 7px;font-size:11.5px}}
 .tone{{flex:1;font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;
       color:var(--muted-foreground,#8b9c94)}}
 .bd{{font-size:9px;padding:1px 6px;border-radius:99px;text-transform:uppercase;
     border:1px solid var(--border,#2a3833)}}
 .bd-sent{{color:#4fc98a}} .bd-queued{{color:var(--muted-foreground,#8b9c94)}}
 .sw{{font-size:10.5px;color:var(--muted-foreground,#8b9c94);margin-bottom:5px}}
 .bb{{background:#DCF8C6;color:#111;border-radius:7px;padding:6px 9px;font-size:11.6px;
     line-height:1.42;word-wrap:break-word}}
 .kpi{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 0}}
 .k{{border:1px solid var(--border,#2a3833);border-radius:8px;padding:6px 11px}}
 .k span{{display:block;font-size:9px;text-transform:uppercase;letter-spacing:.06em;
         color:var(--muted-foreground,#8b9c94)}}
 .k b{{font-size:15px}}
</style></head><body>
<div class="head">
  <b>{html.escape(inst['name'])} &mdash; WhatsApp automation</b>
  <div class="m">{html.escape(inst['tagline'])} &middot; every bubble below is live engine
   output, generated by running the bot</div>
</div>
<div class="row">
  <div class="col">
    <div class="lbl">1 &middot; Admission enquiry at 11:40 PM</div>
    <div class="phone">
      <div class="phead"><div class="av">{html.escape(inst['name'][0])}</div>
        <div><b>{html.escape(inst['name'])}</b><small>online &middot; replies instantly</small></div></div>
      <div class="chat">{admission['bubbles']}</div>
    </div>
    <div class="kpi">
      <div class="k"><span>Hot leads</span><b>{kpi['hot_leads']}</b></div>
      <div class="k"><span>Reminders sent</span><b>{kpi['reminders_sent']}</b></div>
      <div class="k"><span>Overdue</span><b>{config.inr(kpi['overdue_total'])}</b></div>
    </div>
  </div>
  <div class="col2">
    <div class="lbl">2 &middot; The fee reminder ladder &mdash; real message text</div>
    {cards}
  </div>
</div>
</body></html>"""


def _render_demo_html(panels, staff, kpi, ladder=None) -> str:
    inst = config.institute()
    phone_frame = "".join(
        f'<div class="phone"><div class="phead"><div class="avatar">'
        f'{html.escape(inst["name"][0])}</div><div><b>{html.escape(inst["name"])}</b>'
        f'<small>online &middot; replies instantly</small></div></div>'
        f'<div class="chat">{p["bubbles"]}</div></div>'
        f'<div class="cap"><b>{html.escape(p["title"])}</b><br>'
        f'<span class="muted">{html.escape(p["subtitle"])}</span><br>'
        f'<code>{p["phone"]}</code> &middot; {html.escape(p["who"])} '
        f'&middot; final state <code>{p["state"]}</code></div>'
        for p in panels)

    staff_html = ""
    for s in staff:
        items = "".join(
            f'<div class="staffmsg"><span class="tag">{html.escape(i["kind"])}</span>'
            f'{html.escape(i["body"]).replace(chr(10), "<br>")}</div>' for i in s["items"])
        staff_html += (f'<div class="staffcard"><b>Alert sent to {html.escape(s["number"])}</b>'
                       f'<span class="muted"> &middot; {s["count"]} messages</span>{items}</div>')

    ladder_html = ""
    if ladder:
        cards = ""
        for row in ladder:
            body = html.escape(row.get("body") or "(not sent in this run)")
            status = row.get("status") or "queued"
            badge = {"sent": "sent", "held_conversation": "held - parent in chat",
                     "held_quiet_hours": "held - quiet hours", "queued": "scheduled",
                     "skipped": "superseded"}.get(status, status)
            cards += (
                f'<div class="stage">'
                f'<div class="stagehead"><span class="stagekey">{html.escape(row["stage"])}</span>'
                f'<span class="tone">{html.escape(row["tone"] or "")}</span>'
                f'<span class="st st-{html.escape(status)}">{html.escape(badge)}</span></div>'
                f'<div class="stagewho">{html.escape(row["student_name"])} '
                f'&middot; {html.escape(row["label"])} '
                f'&middot; {config.inr(store.fee_outstanding(row))} '
                f'&middot; due {config.fmt_date(row["due_date"])}</div>'
                f'<div class="bubble out-b">{body}</div></div>')
        ladder_html = (
            '<h2>The fee reminder ladder &mdash; all seven stages, real message text</h2>'
            '<div class="muted" style="padding:0 30px 10px;font-size:12.5px">'
            'One fee per stage, pinned to today so a single tick fires the whole ladder. '
            'In production each fee walks up this ladder over 22 days. '
            'A parent in an active chat is held, not messaged.</div>'
            f'<div class="ladder">{cards}</div>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(inst['name'])} - WhatsApp Automation Demo</title>
<style>
 :root{{--wa:#075E54;--wa2:#128C7E;--green:#25D366;--in:#fff;--out:#DCF8C6;
        --bg:#0b141a;--panel:#111b21;--line:#22303a;--tx:#e9edef;--mut:#8696a0}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--tx);
      font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}}
 header{{padding:26px 30px 14px;border-bottom:1px solid var(--line)}}
 h1{{margin:0 0 4px;font-size:22px}}
 .sub{{color:var(--mut);font-size:13px}}
 .badge{{display:inline-block;margin-top:10px;padding:4px 12px;border-radius:99px;font-size:12px;
        background:#1e3a2c;color:#7ee2a8;border:1px solid #2e5c44}}
 .kpis{{display:flex;flex-wrap:wrap;gap:10px;padding:16px 30px;border-bottom:1px solid var(--line)}}
 .kpi{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 15px}}
 .kpi span{{display:block;font-size:10px;color:var(--mut);text-transform:uppercase;
           letter-spacing:.07em}}
 .kpi b{{font-size:19px}}
 .grid{{display:flex;flex-wrap:wrap;gap:26px;padding:26px 30px;align-items:flex-start}}
 .phone{{width:360px;background:var(--panel);border:1px solid var(--line);border-radius:16px;
        overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,.45)}}
 .phead{{display:flex;gap:11px;align-items:center;background:var(--wa);padding:12px 14px}}
 .phead small{{display:block;color:#c8e6d8;font-size:11px}}
 .avatar{{width:34px;height:34px;border-radius:50%;background:var(--green);color:#05301f;
         display:grid;place-items:center;font-weight:700}}
 .chat{{padding:14px 12px;min-height:320px;max-height:640px;overflow-y:auto;
       background:#0d1a20 url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><circle cx="20" cy="20" r="1" fill="%2318242b"/></svg>')}}
 .msg{{display:flex;margin:7px 0}}
 .msg.in{{justify-content:flex-start}} .msg.out{{justify-content:flex-end}}
 .msg.sys{{justify-content:center}}
 .bubble{{max-width:86%;padding:7px 11px 16px;border-radius:9px;font-size:13px;
         line-height:1.45;position:relative;word-wrap:break-word;color:#111}}
 .msg.in .bubble{{background:var(--in);border-top-left-radius:2px}}
 .msg.out .bubble{{background:var(--out);border-top-right-radius:2px}}
 .msg.sys .bubble{{background:#1b2b33;color:var(--mut);font-size:12px;padding:6px 10px}}
 .tick{{position:absolute;right:8px;bottom:3px;font-size:10px;color:#5b8a6a}}
 .btns{{display:flex;flex-direction:column;gap:5px;margin-top:9px}}
 .btn{{background:#eef7f1;border:1px solid #cfe4d6;color:#0b5c3d;border-radius:6px;
      padding:6px 9px;font-size:12px;text-align:center;font-weight:600}}
 .listcard{{margin-top:9px;border:1px solid #cfe4d6;border-radius:8px;overflow:hidden;background:#f7fbf9}}
 .listbtn{{background:#eef7f1;padding:7px 10px;font-size:12px;font-weight:700;color:#0b5c3d;
          text-align:center;border-bottom:1px solid #cfe4d6}}
 .secttl{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#5d7a6a;
         padding:7px 10px 3px;font-weight:700}}
 .row{{padding:5px 10px;border-top:1px solid #e6f0ea;font-size:12px}}
 .row small{{display:block;color:#6b8578;font-size:10.5px}}
 .tag{{display:inline-block;font-size:9.5px;text-transform:uppercase;letter-spacing:.05em;
      background:#dcefe4;color:#31624a;border-radius:4px;padding:1px 5px;
      margin-right:5px}}
 .cap{{padding:12px 14px;font-size:12px;border-top:1px solid var(--line)}}
 .cap code{{background:#0b141a;padding:1px 5px;border-radius:4px;font-size:11px}}
 .muted{{color:var(--mut)}}
 h2{{font-size:15px;margin:26px 30px 0;text-transform:uppercase;letter-spacing:.08em;
    color:#8fbfa8}}
 .staffcard{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--green);
            border-radius:10px;padding:13px 16px;margin:14px 30px;font-size:13px}}
 .staffmsg{{margin-top:9px;padding-top:9px;border-top:1px solid var(--line);
           white-space:normal;line-height:1.5;color:#cfe3d8}}
 .ladder{{display:flex;flex-wrap:wrap;gap:16px;padding:14px 30px 8px;align-items:flex-start}}
 .stage{{width:352px;background:var(--panel);border:1px solid var(--line);border-radius:12px;
        padding:12px 13px}}
 .stagehead{{display:flex;align-items:center;gap:8px;margin-bottom:7px}}
 .stagekey{{font-weight:800;font-size:13px;background:#0d1a20;border:1px solid var(--line);
           border-radius:6px;padding:2px 8px;color:#7ee2a8}}
 .tone{{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;
       flex:1}}
 .st{{font-size:9.5px;padding:2px 7px;border-radius:99px;text-transform:uppercase;
     letter-spacing:.04em;border:1px solid}}
 .st-sent{{background:#14351f;color:#7ee2a8;border-color:#2e5c44}}
 .st-held_conversation{{background:#3a2f12;color:#f0c674;border-color:#5c4a1e}}
 .st-held_quiet_hours{{background:#2b2b3a;color:#a9b6f0;border-color:#3d3d5c}}
 .st-queued{{background:#1b2b33;color:var(--mut);border-color:var(--line)}}
 .stagewho{{font-size:11px;color:var(--mut);margin-bottom:7px}}
 .out-b{{background:var(--out);color:#111;border-radius:9px;padding:8px 11px;font-size:12.5px;
        line-height:1.45;word-wrap:break-word;max-width:100%}}
 footer{{padding:22px 30px 40px;color:var(--mut);font-size:12px;border-top:1px solid var(--line)}}
</style></head><body>
<header>
  <h1>{html.escape(inst['name'])} &mdash; WhatsApp Automation</h1>
  <div class="sub">{html.escape(inst['tagline'])} &middot; {', '.join(inst['branches'])}
   &middot; office {inst['office_hours']}</div>
  <div class="badge">Live engine output &mdash; every bubble below was produced by the
   running bot, not mocked up</div>
</header>
<div class="kpis">
 <div class="kpi"><span>Students</span><b>{kpi['students_active']}</b></div>
 <div class="kpi"><span>Open leads</span><b>{kpi['open_leads']}</b></div>
 <div class="kpi"><span>Hot leads</span><b>{kpi['hot_leads']}</b></div>
 <div class="kpi"><span>Reminders sent</span><b>{kpi['reminders_sent']}</b></div>
 <div class="kpi"><span>Reminders queued</span><b>{kpi['reminders_queued']}</b></div>
 <div class="kpi"><span>Overdue</span><b>{config.inr(kpi['overdue_total'])}</b></div>
 <div class="kpi"><span>Staff alerts</span><b>{kpi['alerts']}</b></div>
</div>
<div class="grid">{phone_frame}</div>
{ladder_html}
<h2>What the staff actually receives</h2>
{staff_html}
<footer>Generated by coaching_bot {__import__('coaching_bot').__version__} &middot;
 every reminder, receipt and alert shown is also written to
 <code>data/outbox.jsonl</code> as the exact WhatsApp Cloud API payload.</footer>
</body></html>"""


# ---------------------------------------------------------------------------
def cmd_output(args):
    """Write out/OUTPUT.md - the complete evidence pack from the live database:
    every conversation, the full reminder ladder with real message text, staff
    alerts, scheduler non-duplication proof, KPIs and the wire payloads."""
    import collections
    con = _con()
    L: list[str] = []
    W = L.append
    inst = config.institute()
    W(f"# OUTPUT - {inst['name']} WhatsApp Automation")
    W("")
    W("Every line below was produced by running the engine. Nothing is hand-written.")
    W(f"Generated: {config.now_ist():%d %b %Y %H:%M} IST   |   mode: "
      f"{'DRY-RUN (nothing transmitted)' if config.demo_mode() else 'LIVE'}")
    W("")

    def conv(title, phone, limit=60):
        W("=" * 78)
        W(title)
        W("=" * 78)
        for r in store.transcript(con, phone, limit):
            tag = "PARENT >" if r["direction"] == "in" else "   BOT <"
            W(f"{tag} [{r['kind']}] {(r['body'] or '').strip()}")
            if r["kind"] == "buttons" and r["payload"]:
                for b in json.loads(r["payload"]).get("buttons", []):
                    W(f"          ( button: {b['title']}  -> id={b['id']} )")
            if r["kind"] == "list" and r["payload"]:
                for sec in json.loads(r["payload"]).get("sections", []):
                    W(f"          -- {sec['title']} --")
                    for row in sec["rows"]:
                        W(f"             {row['title']}: {row.get('description', '')}")
            W("")

    conv("PART 1 - ADMISSION ENQUIRY CONVERSATION (new number, after office hours)",
         "919812300001")

    W("=" * 78)
    W("PART 2 - LEAD RECORD CREATED BY THAT CONVERSATION")
    W("=" * 78)
    lead = con.execute("SELECT * FROM leads WHERE phone=?", ("919812300001",)).fetchone()
    if lead:
        for k in lead.keys():
            W(f"  {k:<16} {lead[k]}")
    W("")

    W("=" * 78)
    W("PART 3 - FEE REMINDER LADDER: ALL STAGES, REAL MESSAGE TEXT")
    W("=" * 78)
    order = {s["key"]: i for i, s in enumerate(config.cfg()["fee_reminder_policy"]["stages"])}
    rows = list(con.execute(
        "SELECT r.stage, r.status, r.body, r.tone, s.name sn, s.parent_phone,"
        " f.label, f.amount, f.paid_amount, f.due_date"
        " FROM reminder_log r JOIN students s ON s.id=r.student_id"
        " JOIN fee_schedule f ON f.id=r.fee_schedule_id"
        " WHERE s.roll_no LIKE 'SHOW-%' AND r.status != 'skipped'"))
    for r in sorted(rows, key=lambda x: order.get(x["stage"], 99)):
        W("")
        W(f"--- {r['stage']}  ({r['tone']})  status={r['status']}  "
          f"to parent of {r['sn']} @ {r['parent_phone']}")
        W(f"    {r['label']} | {config.inr(r['amount'])} | due {config.fmt_date(r['due_date'])}")
        W("")
        for line in (r["body"] or "").splitlines():
            W(f"    | {line}")
    W("")

    fee_parent = con.execute(
        "SELECT s.parent_phone FROM students s JOIN fee_schedule f ON f.student_id=s.id"
        " WHERE f.status IN ('pending','partial') AND s.roll_no NOT LIKE 'SHOW-%'"
        " ORDER BY f.due_date LIMIT 1").fetchone()
    perf_parent = con.execute(
        "SELECT s.parent_phone FROM students s JOIN tests t ON t.student_id=s.id"
        " WHERE s.roll_no NOT LIKE 'SHOW-%' GROUP BY s.id HAVING COUNT(t.id)>=3"
        " ORDER BY s.roll_no LIMIT 1").fetchone()
    if fee_parent:
        conv("PART 4 - EXISTING PARENT: FEE STATUS + PAYMENT CLAIM",
             store.normalize_phone(fee_parent["parent_phone"]), 40)
    if perf_parent:
        conv("PART 5 - EXISTING PARENT: PERFORMANCE REPORT",
             store.normalize_phone(perf_parent["parent_phone"]), 40)
    conv("PART 6 - OPT-OUT / OPT-IN COMPLIANCE (STOP then START)", "919812300004", 20)

    W("=" * 78)
    W("PART 7 - WHAT STAFF ACTUALLY RECEIVE")
    W("=" * 78)
    for a in reversed(store.recent_alerts(con, 30)):
        W("")
        W(f"[{a['kind']}]  {a['ts'][:16].replace('T', ' ')}")
        for line in (a["body"] or "").splitlines():
            W(f"   {line}")
    W("")

    W("=" * 78)
    W("PART 8 - SCHEDULER RUN REPORT + NON-DUPLICATION PROOF")
    W("=" * 78)
    dups = list(con.execute("SELECT fee_schedule_id, stage, COUNT(*) n FROM reminder_log"
                            " GROUP BY fee_schedule_id, stage HAVING n>1"))
    edups = list(con.execute("SELECT dedupe_key, COUNT(*) n FROM alerts"
                             " WHERE dedupe_key IS NOT NULL"
                             " GROUP BY dedupe_key HAVING n>1"))
    W(f"  duplicate (fee, stage) reminder rows : {len(dups)}   (must be 0)")
    W(f"  duplicate escalation keys           : {len(edups)}   (must be 0)")
    W("")
    W("  reminder_log status breakdown:")
    for r in con.execute("SELECT status, COUNT(*) n FROM reminder_log"
                         " GROUP BY status ORDER BY n DESC"):
        W(f"     {r['status']:<20} {r['n']}")
    W("")

    W("=" * 78)
    W("PART 9 - KPI SNAPSHOT")
    W("=" * 78)
    for k, v in seed.kpi_snapshot(con).items():
        W(f"  {k:<24} {v}")
    W("")

    W("=" * 78)
    W("PART 10 - EXACT WHATSAPP CLOUD API PAYLOADS (data/outbox.jsonl)")
    W("=" * 78)
    W("")
    W("  The wire format. Nothing is transmitted while uncredentialed; the outbox")
    W("  is the audit trail and the proof of what a parent would receive.")
    recs = []
    if config.OUTBOX_PATH.exists():
        recs = [json.loads(l) for l in config.OUTBOX_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    W(f"  records: {len(recs)}   types: "
      f"{dict(collections.Counter(r['payload']['type'] for r in recs))}")
    W("")
    tpl = [r for r in recs if r["payload"]["type"] == "template"]
    if tpl:
        W("  --- a UTILITY TEMPLATE send (fee reminder, parent outside the 24h window) ---")
        W(textwrap.indent(json.dumps(tpl[0]["payload"], indent=2, ensure_ascii=False), "  "))
    W("")

    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUT_DIR / "OUTPUT.md"
    text = "\n".join(L)
    path.write_text(text, encoding="utf-8")
    print(f"Evidence pack written: {path}  ({len(text)} chars, {len(L)} lines)")
    print(f"  duplicate (fee,stage) rows : {len(dups)}")
    print(f"  duplicate escalation keys  : {len(edups)}")
    con.close()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="coaching_bot", description="Coaching institute WhatsApp automation")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="load realistic demo data")
    s.add_argument("--students", type=int, default=24)
    s.add_argument("--append", action="store_true", help="do not wipe existing rows")
    s.set_defaults(fn=cmd_seed)

    s = sub.add_parser("reminders", help="run one fee-reminder scheduler tick")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--live", action="store_true")
    s.add_argument("--force-quiet", action="store_true", help="ignore office-hours hold")
    s.set_defaults(fn=cmd_reminders)

    s = sub.add_parser("score", help="enter a test score and alert the parent")
    s.add_argument("--roll", required=True)
    s.add_argument("--test", required=True)
    s.add_argument("--subject", default="General")
    s.add_argument("--marks", type=float, required=True)
    s.add_argument("--total", type=float, default=100)
    s.add_argument("--batch-avg", type=float, dest="batch_avg")
    s.add_argument("--topper", type=float)
    s.add_argument("--rank", type=int)
    s.add_argument("--remark")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_score)

    s = sub.add_parser("paid", help="record a payment and send the receipt")
    s.add_argument("--roll", required=True)
    s.add_argument("--amount", type=float)
    s.add_argument("--method", default="UPI")
    s.add_argument("--reference")
    s.set_defaults(fn=cmd_paid)

    s = sub.add_parser("report", help="write a report card for one student")
    s.add_argument("--roll", required=True)
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("reportcard-all", help="write report cards for every active student")
    s.set_defaults(fn=cmd_reportcard_all)

    s = sub.add_parser("fortnightly", help="run the fortnightly performance digest")
    s.set_defaults(fn=cmd_fortnightly)

    s = sub.add_parser("demo", help="run scripted conversations and render the demo transcript")
    s.add_argument("--reseed", action="store_true")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("stats", help="print KPI snapshot")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("output", help="write out/OUTPUT.md - the full evidence pack")
    s.set_defaults(fn=cmd_output)

    s = sub.add_parser("serve", help="run the webhook server")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())