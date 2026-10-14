# Coaching Institute WhatsApp Automation

**Status: repository audit in progress.** The current public branch contains deployment metadata but does not contain the application directories referenced by the Dockerfile and README. Do not deploy this snapshot until those source files are restored.

A WhatsApp automation starter for coaching institutes, designed for demo-first operation. It is intended to support admission enquiries, fee reminders, and student performance updates after the application source and environment have been supplied and verified.

## Repository status

The deployment configuration expects these paths, but they are absent from the current default branch:

- `coaching_bot/` application package
- `config/institute.json` demo configuration
- `n8n/` workflow definitions
- `deploy/Caddyfile` reverse proxy configuration
- `docs/` and `scripts/` referenced by the documentation and deployment files

As a result, the existing Docker build cannot complete. The README previously described the project as ready to deploy despite these missing files. This branch documents that blocker instead of pretending the repo is deployable.

## Current repository files

- `.env.example`: environment variable template
- `.gitignore` and `.dockerignore`: local-secret and build-context exclusions
- `Dockerfile`: application image definition (requires the missing source directories)
- `docker-compose.yml`: bot, n8n, and Caddy services (requires missing Caddy/workflow files)

## Safe local setup after restoring source

1. Restore the missing application and deployment directories from your original project ZIP or local checkout.
2. Copy the sample environment file:
   ```bash
   cp .env.example .env
   ```
   On PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```
3. Set a long, unique `ADMIN_TOKEN`. Never commit `.env`.
4. Set `N8N_VERSION` to a specific released version, and replace the sample hostnames with real domains whose DNS points to the server.
5. Validate before starting:
   ```bash
   docker compose config --quiet
   docker compose build
   ```
6. Only after both commands pass, start the stack:
   ```bash
   docker compose up -d
   docker compose ps
   docker compose logs --tail=100
   ```

## Required production checks

- Restore the missing source paths before building the image.
- Use a strong admin token and keep all credentials in the hosting provider's secret manager or an untracked `.env`.
- Configure HTTPS domains and verify Meta webhook settings.
- Keep demo mode enabled until the WhatsApp Cloud API credentials and approved templates have been checked.
- Use persistent storage and tested backups for SQLite and n8n.
- Do not use real student data until access control, retention, consent, and applicable privacy obligations have been reviewed.
- Test the `/health` endpoint and run a smoke test after deployment.

## Deployment files

The existing Docker and Compose files are retained for review, but **they are not deployable until the missing directories are restored**. No successful build or live deployment is claimed by this branch.
