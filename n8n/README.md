# n8n workflows

Import the workflow JSON files into n8n. They are committed inactive by default. Inspect each schedule, destination URL, and header token before activating.

Required environment variables:
- `COACHING_BOT_URL`, normally `http://bot:8080` on the Compose network.
- `COACHING_ADMIN_TOKEN`, which must match the bot's `ADMIN_TOKEN`.

Test each workflow with demo mode enabled before enabling production schedules. Do not expose n8n publicly without strong account access controls.
