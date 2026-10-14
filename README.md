# Coaching Institute WhatsApp Automation

A Python standard-library application for coaching centres to handle WhatsApp admission enquiries, fee reminders, payment acknowledgements, and student performance updates.

> **Demo-first:** the committed institute configuration uses placeholders. Without both WhatsApp Cloud API credentials, outbound messages are logged locally instead of being sent. Review all data, consent, and deployment requirements before connecting a live number.

## Features
- WhatsApp Cloud API webhook verification and inbound message handling.
- Multi-turn admission enquiry flow and lead scoring.
- Seven-stage fee reminders with quiet hours, opt-out handling, escalation, and duplicate protection.
- Payment acknowledgement and staff routing.
- Student reports, test-score alerts, and attendance/performance digests.
- SQLite storage, dashboard, health endpoint, and authenticated admin routes.
- Docker Compose deployment with n8n and Caddy TLS termination.

## Quick start (Python 3.11+)

```bash
git clone https://github.com/JustInCasesss/coaching-automation.git
cd coaching-automation
python -m coaching_bot.cli demo --reseed
python -m coaching_bot.cli stats
```

Open `out/demo_transcript.html`. Local `data/` and `out/` files are ignored by Git.

Run tests:

```bash
python -m compileall -q coaching_bot tests
python -m unittest discover -s tests -v
```

## Docker deployment

1. Prepare a Linux VPS with Docker Engine and the Docker Compose plugin.
2. Point `BOT_HOST` and `N8N_HOST` DNS records to the VPS.
3. Copy `.env.example` to `.env`, generate a strong admin secret, and set a pinned `N8N_VERSION`:

   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. Replace the default hostnames with your domains and paste the secret into `ADMIN_TOKEN`.
5. Validate, build, then start:

   ```bash
   docker compose config --quiet
   docker compose build
   docker compose up -d
   docker compose ps
   docker compose logs --tail=100
   ```

Caddy provisions HTTPS automatically when DNS is correct and ports 80/443 are reachable. Import workflows from `n8n/` into n8n and test them with demo mode enabled before activation.

The included Render Blueprint is **demo-only** unless durable storage is configured; its free filesystem is ephemeral.

## Important environment variables

| Variable | Purpose |
|---|---|
| `ADMIN_TOKEN` | Required secret for all `/admin/*` routes |
| `WHATSAPP_TOKEN` | Meta WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta phone-number ID |
| `WHATSAPP_VERIFY_TOKEN` | Meta webhook verification secret |
| `COACH_DEMO_MODE` | Set `true` to force dry-run mode |
| `N8N_VERSION` | Pin an exact n8n release, not `latest` |
| `BOT_HOST`, `N8N_HOST` | Public DNS hostnames |

Never commit a populated `.env`, credentials, student records, databases, or outbox logs.

## HTTP endpoints

| Method and path | Purpose |
|---|---|
| `GET /health` | Health probe |
| `GET /webhook` | Meta webhook verification |
| `POST /webhook` | Inbound WhatsApp events |
| `GET /dashboard` | Operator dashboard |
| `GET /report/<roll_no>` | Printable student report |
| `POST /admin/run-reminders` | Run one reminder tick |
| `POST /admin/mark-paid` | Record a payment |
| `POST /admin/score` | Record a test score |
| `POST /admin/fortnightly-reports` | Generate performance digests |
| `POST /admin/stats`, `/admin/overdue`, `/admin/leads`, `/admin/alerts` | Workflow data |
| `POST /admin/reset-demo` | Reset demo data |

Admin routes require the `X-Admin-Token: <ADMIN_TOKEN>` header. An unset token fails closed.

## Repository layout

```text
coaching_bot/   Python application and CLI
config/         demo config and editable example
n8n/            importable workflow JSON
deploy/         Caddy reverse-proxy configuration
docs/           setup and operations documentation
scripts/        demo and smoke-test helpers
tests/          security regression tests
```

## Before going live
- Replace all example institute values and environment settings.
- Keep demo mode enabled until webhook delivery, credentials, template selection, and opt-out handling are tested end to end.
- Obtain any required Meta template approvals before business-initiated sends outside the customer-service window.
- Use persistent storage; test database and n8n backups and restores.
- Restrict dashboard and report access before public exposure. Treat guardian, student, payment, attendance, and exam data as sensitive.
- Review consent, retention, opt-out handling, and applicable legal obligations with the institute. This software is not legal advice.
- After deployment, run `BASE_URL=https://your-bot-host sh scripts/smoke_test.sh`.

## License
No license has been added yet. Add one before distributing or commercially reusing this repository.
