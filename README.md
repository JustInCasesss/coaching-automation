# Coaching Institute WhatsApp Automation

**Instant admission answers on WhatsApp + automated fee reminders + student
performance reports to parents.** A complete, deployable, sellable automation
for coaching institutes, tuition centres and schools — built to be white-labelled
per client.

This is not a slide deck. The engine in `coaching_bot/` answers WhatsApp
conversations, runs a seven-stage fee reminder ladder, and pushes test-score and
attendance alerts to parents. Every claim below can be checked with the commands
in the next section. All institute data in this repo is **fictional demo data**.

---

## The 5-minute proof

```bash
python -m coaching_bot.cli demo --reseed      # seeds a centre, runs 4 real conversations
python -m coaching_bot.cli reminders --dry-run --force-quiet
python -m coaching_bot.cli reminders --dry-run --force-quiet   # must send 0
python -m coaching_bot.cli stats
```

Open `out/demo_transcript.html` — a WhatsApp-styled transcript of **real engine
output**, not a mockup. On Windows run `scripts\run_demo.bat`; on Linux/macOS
`./scripts/run_demo.sh`.

Requires Python 3.11+. **No pip install. No dependencies.** Everything is stdlib,
including the web server and SQLite access.

---

## What it does

### 1. Admission enquiries answered instantly
A parent messages at 11:40 PM. They get a reply in under a second, not at 10 AM.

- Rich WhatsApp menus (not "reply 1 for admissions") — tap-to-choose buttons and
  lists, which is what parents actually complete.
- Multi-turn qualification: course → student's class → name → batch preference.
- Full fee structure, installment plans, live seat counts, current offers.
- Free demo class booking, then an instant **HOT lead alert** to the counsellor
  with a lead score and the exact reason to call.
- Lead scoring is deterministic, so a ₹500/month receptionist and the owner see
  the same ranking: course +5, class +5, name +10, batch preference +15,
  callback slot +20, fee question +10, demo request +25. ≥50 = HOT.
- Anything ambiguous routes to a human with the context attached — it never
  dead-ends a parent in a loop.

### 2. Automated fee reminders
A seven-stage ladder per installment, not one generic blast:

| Stage | Fires | Tone |
|---|---|---|
| T-7 | 7 days before due | friendly heads-up |
| T-3 | 3 days before | polite reminder |
| T-1 | 1 day before | due tomorrow |
| D0 | due date | due today |
| D+3 | 3 days late | overdue, offers an installment plan |
| D+7 | 7 days late | firm + **escalates to accounts** |
| D+15 | 15 days late | final notice + **escalates to accounts** |

What makes it survive contact with real parents:

- **Exactly one reminder per fee per tick.** A fee that is 20 days overdue gets
  the final notice, not seven messages at once because the server was down. Older
  unsent stages are retired as `superseded`.
- **Never double-sends.** `UNIQUE(fee_schedule_id, stage)` in the database, so a
  double-fired cron or a restarted scheduler cannot duplicate. The second
  consecutive tick in the 5-minute proof sends 0.
- **Quiet hours.** Nothing goes out between 20:00 and 09:00 except the D+7 and
  D+15 escalations, which are held and released on the next in-hours tick.
- **Pause on reply.** If the parent is already in the chat, the reminder is held
  and staff are still escalated. Nagging a parent who is actively talking to you
  is how you get blocked.
- **Opt-out is absolute.** `STOP` silences everything immediately; `START`
  restores it. This supports DPDP/TRAI consent requirements, but it is not legal
  advice - each institute must confirm its own compliance.
- **Reply `PAID`** → routed to accounts with the parent's exact message, plus a
  request for the screenshot or UTR.
- **Receipt on payment** — the moment accounts marks a fee paid, the ladder is
  cancelled and the parent gets an acknowledgement with the remaining balance.

### 3. Student performance to parents
- **Instant test-score alert** the evening a teacher enters marks: score, batch
  average, the delta, batch rank, teacher's remark, and what to do next.
- **On-demand report**: parent texts `REPORT` and gets subject-wise averages,
  trend versus previous tests, attendance percentage, strongest and weakest
  subject, and the latest test in full.
- **Fortnightly digest** to every active parent (default Saturday 18:30).
- **Low-attendance alerts** when a child drops below 75% in 30 days.
- **Printable report card** per student at `GET /report/<roll_no>` — hand it over
  at a parent-teacher meeting or attach it as a PDF.

---

## Quick start (Docker)

```bash
git clone https://github.com/JustInCasesss/coaching-automation.git
cd coaching-automation
cp .env.example .env     # set ADMIN_TOKEN and N8N_VERSION (both required)
docker compose up -d --build
```

Without Docker: `python -m coaching_bot.cli demo --reseed` (Python 3.11+).

## Repository layout

```
coaching_bot/   the engine (server, flows, reminders, performance, whatsapp client, cli)
config/         institute.json - demo configuration (replace per client)
n8n/            importable n8n workflows
deploy/         Caddyfile and systemd units
scripts/        run_demo / smoke_test scripts
docs/           offer, sales script, setup guide, Meta templates, runbook
Dockerfile  docker-compose.yml  .env.example  .gitignore  .dockerignore
```

---

## Architecture

```
   Parent's WhatsApp
          │
          ▼
  Meta WhatsApp Cloud API  ──webhook──▶  coaching_bot/server.py
          ▲                                   │
          │                                   ▼
          │                          coaching_bot/flows.py       conversation state machine
          │                          coaching_bot/reminders.py   fee ladder, quiet hours, dedupe
          │                          coaching_bot/performance.py score + attendance reporting
          │                                   │
          │                                   ▼
          └────────── coaching_bot/whatsapp.py ──▶  SQLite (data/coaching.db)
                     Cloud API client, dry-run capable    + data/outbox.jsonl (exact payloads)

  n8n (n8n/*.json)  ── schedules, digests, CRM sync, payment-gateway webhooks ──▶ the same admin API
```

Why this split: the conversation logic is a pure state machine that returns
outbound actions instead of sending them. That is what lets the CLI demo, the
webhook, and any future channel share one code path — and it is why the demo
transcript is real output rather than a mockup.

**White-label:** every client-specific value lives in `config/institute.json` —
courses, fees, batch timings, branches, offers, reminder policy, branding, alert
numbers. Onboarding a new centre is a new JSON file, not a code change.

---

## Operator commands

```bash
python -m coaching_bot.cli seed --students 40          # load demo data
python -m coaching_bot.cli demo                        # run conversations, write the transcript
python -m coaching_bot.cli reminders --dry-run         # one tick, nothing sent
python -m coaching_bot.cli reminders --live            # one tick, actually sends
python -m coaching_bot.cli score --roll NEET-2026-100 --test "Weekly Test 10" \
        --subject Physics --marks 74 --total 100 --batch-avg 61 --rank 12
python -m coaching_bot.cli paid --roll NEET-2026-100 --amount 25000 --method UPI
python -m coaching_bot.cli report --roll NEET-2026-100
python -m coaching_bot.cli fortnightly                 # performance digests + attendance alerts
python -m coaching_bot.cli stats
python -m coaching_bot.cli output                      # write out/OUTPUT.md evidence pack
python -m coaching_bot.cli serve --port 8080
```

## HTTP API

| Route | Purpose |
|---|---|
| `GET /webhook` | Meta verification handshake |
| `POST /webhook` | Meta inbound messages |
| `GET /health` | liveness probe |
| `GET /dashboard` | operator dashboard: hot leads, overdue fees, collections, alerts |
| `GET /report/<roll_no>` | printable report card |
| `POST /admin/run-reminders` | run one reminder tick |
| `POST /admin/mark-paid` | record a payment, cancel the ladder, send the receipt |
| `POST /admin/score` | enter a test score, push the parent alert |
| `POST /admin/fortnightly-reports` | send performance digests + attendance alerts |
| `POST /admin/broadcast` | message a filtered set of parents (course/batch/class/branch) |
| `POST /admin/stats` · `overdue` · `leads` · `alerts` | data for n8n digests |
| `POST /admin/reset-demo` | reload demo data |

`/admin/*` requires the `X-Admin-Token` header. **Always set `ADMIN_TOKEN`** -
`docker-compose.yml` refuses to start without it.

## Demo mode

With no `WHATSAPP_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID`, the system runs in **dry-run**:
every message is recorded in SQLite and appended to `data/outbox.jsonl` as the
exact Cloud API JSON payload, and nothing is transmitted. That is what makes it
safe to demo to a prospect with their real number in front of them — and what
makes the audit trail provable. Set the credentials and the same code goes live.

## Deploy

- `Dockerfile` — zero-dependency image, non-root user, healthcheck.
- `docker-compose.yml` — bot + n8n + Caddy (automatic TLS) on one 1 vCPU box.
- `deploy/Caddyfile`, `deploy/coaching-bot.service`,
  `deploy/coaching-bot-reminders.timer` — bare-metal systemd path.
- `scripts/smoke_test.sh` — post-deploy verification, exits non-zero on failure.

A 1 vCPU / 1 GB VPS handles a 2,000-student centre comfortably; the workload is
webhook-driven and a reminder tick is a few seconds twice a day.

## Documentation

| File | For |
|---|---|
| `docs/OFFER.md` | the productized offer, tiers, pricing, ROI model, guarantee |
| `docs/SALES_SCRIPT.md` | discovery questions, demo flow, objection handling |
| `docs/SETUP_GUIDE.md` | onboarding a new centre end to end |
| `docs/TEMPLATES_FOR_META.md` | exact WhatsApp template copy to submit for approval |
| `docs/RUNBOOK.md` | day-2 operations, troubleshooting, incident playbook |

## The thing that actually blocks go-live

Meta's **24-hour rule**: you may only send free-form text within 24 hours of the
parent's last message. Outside that window every outbound message must use a
pre-approved template. The engine handles this automatically — `pick_channel()`
sends rich free-form text inside the window and the matching approved template
outside it. But the templates themselves must be submitted and approved before
go-live, which takes 1–3 working days. `docs/TEMPLATES_FOR_META.md` has the exact
copy. **Submit templates on day one of onboarding, not day ten.**

## Security notes

- Never commit `.env`, tokens, real student records, or the `data/` folder
  (`.gitignore` already excludes them).
- n8n does not receive the WhatsApp token; it only gets the variables its
  workflows need (see `docker-compose.yml`).
- Pin `N8N_VERSION` to a specific release instead of `latest`.
- Student names, fees and scores are personal data of minors and their families:
  use a private repo and a persistent, backed-up server for any real client.

## Before publishing or deploying

- This repository contains a **demo institute configuration** in `config/institute.json`. Replace all example institute details, phone numbers, payment links, UPI ID, and tax identifiers with verified values before any real use.
- Do not add `.env`, API tokens, real student records, or database/outbox files to GitHub.
- For a safe demo, set `COACH_DEMO_MODE=true` and a strong `ADMIN_TOKEN` in Render Environment Variables.
- Render's free filesystem is ephemeral. Do not use this free setup for real student records or production operation without persistent storage and a security review.

## Render quick setup

- Runtime: Python 3
- Build command: `python --version`
- Start command: `python -m coaching_bot.cli serve --host 0.0.0.0 --port $PORT`
- Environment variables: `COACH_DEMO_MODE=true` and `ADMIN_TOKEN` set to a long random secret.

The project uses Python's standard library only; no `requirements.txt` is needed.
