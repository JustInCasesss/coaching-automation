# Coaching Institute Automation

A small, self-hostable service for coaching centres. It combines WhatsApp Cloud API webhook handling, opt-out controls, lead capture, student and fee records, fee-reminder templates, and test-score alerts. It uses Python's standard library and SQLite, so there are no Python packages to install.

> The repository ships with fictional demo institute configuration. Use demo mode first. Do not add real student records, credentials, or message logs to Git.

## Features

- WhatsApp webhook verification and optional Meta request-signature validation.
- Admission enquiry replies and lead capture with a simple, explainable lead score.
- STOP/START opt-out handling and duplicate webhook-message protection.
- Protected admin endpoints for student records, fee schedules, payment marking, scores, lead lists, and aggregate statistics.
- Fee reminders for T-7, T-3, T-1, due date, D+3, D+7, and D+15 stages. Each fee/stage is deduplicated in SQLite, old missed stages are skipped, and default quiet hours are 20:00–09:00 Asia/Kolkata.
- Scheduled fee reminders use a configured, pre-approved WhatsApp template. Replies to incoming messages use free-form text only in response to the active conversation.
- Docker Compose deployment with Caddy HTTPS, persistent database storage, health checks, a non-root application user, and GitHub Actions tests.

This is an extensible MVP, not an all-in-one school ERP. Payment collection is recorded manually through the admin API; no payment gateway is integrated. Reminders run when invoked by a scheduler; there is no hidden background scheduler. n8n workflows and automated spreadsheet integrations are not included.

## Quick start

Requirements: Docker Engine and the Docker Compose plugin. For running tests without Docker, use Python 3.11 or newer.

1. Clone the repository and enter its folder.

       git clone https://github.com/JustInCasesss/coaching-automation.git
       cd coaching-automation

2. Copy the example environment file.

   Windows PowerShell:

       Copy-Item .env.example .env

   Linux/macOS:

       cp .env.example .env

3. Generate a strong admin token and paste it into the ADMIN_TOKEN entry in .env.

       python -c "import secrets; print(secrets.token_urlsafe(32))"

4. Keep COACH_DEMO_MODE=true while testing. The app writes outbound WhatsApp payloads to data/outbox.jsonl instead of transmitting them.

5. Build and start the service.

       docker compose up --build -d

Open https://localhost. Caddy may use a local development certificate. Check service health at https://localhost/health. For a real deployment, point a DNS hostname at the server, set BOT_HOST in .env to that hostname, allow inbound ports 80 and 443, and restart Compose. Caddy will provision HTTPS for a public hostname.

To stop the service:

       docker compose down

The named bot-data volume is retained. To remove all saved data, you would have to delete that volume explicitly, so do not run volume-removal commands casually.

## Run locally without Docker

Create a virtual environment if preferred, then set COACH_DB, COACH_OUTBOX, ADMIN_TOKEN, and COACH_DEMO_MODE in your shell. No pip install is required.

    python -m compileall -q coaching_bot tests
    python -m unittest discover -s tests -v
    python -m coaching_bot.cli init-db
    python -m coaching_bot.cli serve --host 127.0.0.1 --port 8080

The server refuses to start with a missing or obviously weak ADMIN_TOKEN. For local testing, use a unique random value at least 24 characters long.

## Admin API

Every /admin endpoint requires the X-Admin-Token request header. Use HTTPS for remote calls and never place the token in a URL.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /health | Health check; no personal data |
| GET | /admin/stats | Aggregate counts |
| GET | /admin/leads | Recent captured leads |
| POST | /admin/students | Create a student record |
| POST | /admin/fees | Create a fee schedule entry |
| POST | /admin/mark-paid | Mark a fee paid and attempt a receipt message |
| POST | /admin/score | Record a test score and attempt a parent alert |
| POST | /admin/run-reminders | Preview or run due fee reminders |

Example: create a student. Replace the example token with your own secret and use a valid parent number only in your private test environment.

    curl -X POST https://YOUR_HOST/admin/students ^
      -H "X-Admin-Token: YOUR_ADMIN_TOKEN" ^
      -H "Content-Type: application/json" ^
      -d "{\"roll_no\":\"DEMO-001\",\"full_name\":\"Example Student\",\"parent_phone\":\"910000000000\",\"course\":\"Foundation\"}"

The API returns the newly-created student's integer id. Use it as student_id when creating a fee.

    curl -X POST https://YOUR_HOST/admin/fees ^
      -H "X-Admin-Token: YOUR_ADMIN_TOKEN" ^
      -H "Content-Type: application/json" ^
      -d "{\"student_id\":1,\"description\":\"October fee\",\"amount\":1200,\"due_date\":\"2026-10-30\"}"

The multi-line curl examples use Windows Command Prompt line continuation (^). In PowerShell, paste each request on one line or use Invoke-RestMethod. On Windows PowerShell, curl may be an alias for Invoke-WebRequest. Use curl.exe for the examples above or use Invoke-RestMethod.

### Preview a reminder run

The preview never sends a message and does not claim a reminder stage as sent.

    curl -X POST https://YOUR_HOST/admin/run-reminders ^
      -H "X-Admin-Token: YOUR_ADMIN_TOKEN" ^
      -H "Content-Type: application/json" ^
      -d "{\"dry_run\":true}"

To run reminders live, pass dry_run=false after verifying the Meta credentials, approved template, consent process, and recipient data. Normal quiet hours are enforced even for manual runs. The force option can override quiet hours and should be reserved for controlled testing.

## Connect WhatsApp Cloud API

1. Create and configure a Meta app with a WhatsApp Business Account and a production business phone number.
2. Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in the deployment environment.
3. Set WHATSAPP_VERIFY_TOKEN to a long random value and configure the Meta webhook callback URL as https://YOUR_HOST/webhook using that same verification token.
4. Set META_APP_SECRET so incoming webhook POST requests can be checked against X-Hub-Signature-256. In live deployments, webhook POST requests are rejected if the app secret is not configured.
5. Leave COACH_DEMO_MODE=true until the webhook and replies have been tested. Set it to false only when you are ready to use live messaging.
6. Submit and obtain approval for a fee-reminder template in WhatsApp Manager. Set WHATSAPP_REMINDER_TEMPLATE to its exact name.

The configured reminder template must contain five body placeholders in this order: student name, amount, due date, reminder stage, and institute name. Adjust the code or template contract together if your approved template uses a different number or order. Set WHATSAPP_TEMPLATE_LANG to the approved language code.

Scheduled fee reminders should use approved templates because parents may be outside the 24-hour customer-service window. Do not treat template submission or approval as automatic.

## Schedule fee reminders

The service runs one reminder tick per command or API call. Add a daily scheduler on your server after confirming the instance timezone and the approved template. For example, from the repository directory at 09:15 server-local time:

    docker compose exec -T bot python -m coaching_bot.cli reminders --live

A sample crontab entry is included in deploy/reminders.cron. The application itself enforces Asia/Kolkata quiet hours by default. Test the command first without --live and inspect its JSON output.

## Configuration reference

| Variable | Required | Notes |
|---|---|---|
| ADMIN_TOKEN | Yes | Unique random secret, at least 24 characters |
| COACH_DEMO_MODE | Recommended true initially | Forces outbound messages into the local outbox |
| WHATSAPP_TOKEN | For live WhatsApp | Use a long-lived system-user token with least privilege |
| WHATSAPP_PHONE_NUMBER_ID | For live WhatsApp | Phone number ID from Meta |
| WHATSAPP_VERIFY_TOKEN | For webhook verification | Must match Meta's webhook setting |
| META_APP_SECRET | For live webhooks | Validates inbound webhook signatures |
| WHATSAPP_REMINDER_TEMPLATE | For live reminders | Exact approved template name |
| WHATSAPP_API_VERSION | No | Defaults to v26.0; update deliberately as Meta releases versions |
| COACH_DB | No | SQLite path; defaults to data/coaching.db |
| COACH_OUTBOX | No | Dry-run outbound payload log |
| BOT_HOST | For public Compose deploy | DNS hostname handled by Caddy |

For all supported values, see .env.example.

## Development checks

Run the exact checks used by CI:

    python -m compileall -q coaching_bot tests
    python -m unittest discover -s tests -v

The smoke-test script is scripts/smoke_test.sh. GitHub Actions runs compilation and unit tests on pushes and pull requests.

## Data and security

- Use a private repository for a real institute's deployment.
- Never commit .env, SQLite files, outbox logs, real student records, or access tokens.
- Keep a persistent, encrypted, access-controlled backup of the database. Verify restoration before relying on it.
- Admin data endpoints are protected by X-Admin-Token, but access tokens must still be kept out of browser URLs and logs.
- Demo mode is not production consent or compliance. Confirm WhatsApp opt-in, opt-out, approved template, retention, and applicable Indian data-protection requirements with the institute.
- This project is not legal advice and does not certify DPDP, TRAI, or Meta policy compliance.
- Use a persistent Docker volume or durable mounted storage. Ephemeral container storage is unsuitable for real student data.

## License

No open-source license has been added. Until the owner adds one, do not assume the code is licensed for redistribution or commercial reuse.
