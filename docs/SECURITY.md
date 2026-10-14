# Security and privacy checklist

This project can hold information about minors, parent phone numbers, attendance-adjacent educational data, fees, and assessment scores. Treat it as sensitive personal information.

## Required practices

- Run behind HTTPS. The supplied Compose configuration uses Caddy and does not publish the bot's port on the host.
- Use a unique ADMIN_TOKEN and keep it in a secret manager or a permission-restricted .env file.
- Set META_APP_SECRET for live use so webhook POST bodies are authenticated using Meta's signature header.
- Keep COACH_DEMO_MODE enabled until a deliberate production cutover.
- Store the database and outbox logs outside the Git working tree, restrict access, and define a retention period.
- Restrict admin API access at the network or reverse-proxy layer where feasible.
- Back up SQLite data and test restores.
- Use approved WhatsApp message templates for scheduled reminders outside the customer-service window.
- Record parent consent, handle STOP immediately, and process START only as a fresh opt-in.
- Rotate secrets if a token, .env file, log, or backup may have been exposed.

## Known limits

The admin API uses one shared bearer-like header token rather than user accounts or role-based permissions. There is no built-in audit UI, database encryption, rate limiting, school-specific tenancy isolation, or automatic backup scheduler. Add these controls before selling this as a multi-tenant hosted service. Do not expose the admin API broadly to untrusted clients.

Dry-run payloads can include recipient numbers and message content. Keep data/outbox.jsonl private, limit retention, and never upload it to issue trackers.
