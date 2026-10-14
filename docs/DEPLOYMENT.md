# Deployment guide

## Before you deploy

- Use a VPS with Docker Engine and Docker Compose, or another Docker-compatible host.
- Point a DNS A/AAAA record for the service hostname to the server.
- Open inbound TCP 80 and 443 for Caddy. Do not expose the bot container directly to the public internet.
- Create a unique ADMIN_TOKEN with at least 24 characters and store it in .env or the platform's secret manager.
- Keep COACH_DEMO_MODE=true until admin calls, webhook verification, and dry-run reminders have been checked.
- Use a persistent volume and a tested backup process before importing any real learner records.

## Docker Compose

1. Copy .env.example to .env.
2. Add ADMIN_TOKEN and set BOT_HOST to the public DNS hostname.
3. Keep COACH_DEMO_MODE=true for the first launch.
4. Start: docker compose up --build -d
5. Inspect: docker compose ps and docker compose logs --tail=100 bot caddy
6. Check https://YOUR_HOST/health. It should return JSON with status set to ok.
7. Run unit tests before enabling live messaging: python -m unittest discover -s tests -v

Caddy terminates HTTPS and forwards requests to the private bot service. The database is stored in the named bot-data volume. The .env file is not copied into the Docker image.

## Live WhatsApp checklist

- The phone number and WhatsApp Business Account are approved for production use.
- WHATSAPP_TOKEN is a securely stored system-user token with necessary permissions.
- WHATSAPP_PHONE_NUMBER_ID is correct.
- WHATSAPP_VERIFY_TOKEN matches the callback registration.
- META_APP_SECRET is configured; unsigned webhook POST requests are rejected in non-demo mode.
- A fee-reminder template is approved and its five body variables match the application's order.
- Consent and opt-out handling has been tested with a controlled number.
- COACH_DEMO_MODE is set to false only after these checks.
- A non-production test verifies an inbound message, an admin API call, one fee reminder, opt-out, and restoration from backup.

## Updating

1. Back up the database before changing the image.
2. Pull the intended branch or release.
3. Rebuild and replace the service: docker compose up --build -d.
4. Check docker compose ps, docker compose logs --tail=100 bot, and /health.
5. Keep the prior image or a tested backup available until smoke checks pass.

## Backups

The database file in the container is /data/coaching.db. Use SQLite's online backup API or stop writes during a filesystem-level copy. A simple maintenance window example is:

    docker compose stop bot
    docker cp "$(docker compose ps -q bot):/data/coaching.db" ./coaching-backup.db
    docker compose start bot

Store backups encrypted outside the source repository. Periodically restore a backup into an isolated test environment.
