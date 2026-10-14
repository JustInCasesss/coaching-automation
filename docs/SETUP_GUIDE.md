# Deployment setup guide

## Local demo
1. Install Python 3.11 or later.
2. Run `python -m coaching_bot.cli demo --reseed`.
3. Open `out/demo_transcript.html`.
4. Run `python -m unittest discover -s tests -v`.

## Docker Compose
1. Copy `.env.example` to `.env`.
2. Set a long random `ADMIN_TOKEN`; never put it in source control.
3. Pin `N8N_VERSION` to a released version. Set `BOT_HOST` and `N8N_HOST` to real DNS names that point to the server.
4. Validate with `docker compose config --quiet`.
5. Build and start with `docker compose up -d --build`.
6. Verify `docker compose ps`, inspect `docker compose logs --tail=100`, and query `https://$BOT_HOST/health`.
7. Import workflows from `n8n/`, review all variables, and test manually with demo mode enabled before activation.

Caddy provisions HTTPS when public DNS is correct and ports 80/443 are reachable. Do not publish the bot's port directly to the internet.

## WhatsApp Cloud API
Configure the public `/webhook` callback and matching verify token in Meta. Keep demo mode enabled while validating the handshake and inbound event flow. Store credentials only in your host's secret manager or untracked `.env`. Use approved templates when required by Meta's messaging rules.

## Data durability
Compose stores app data in `bot-data` and n8n state in `n8n-data`. Back up both, restrict backup access, and test restoration before onboarding a real institute. Render's free filesystem is ephemeral; use it for demos only.
