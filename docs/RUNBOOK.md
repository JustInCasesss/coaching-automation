# Operations runbook

## Health and logs
- Health: `GET /health`.
- Service status: `docker compose ps`.
- Logs: `docker compose logs --tail=200 bot n8n caddy`.
- Local demo transcript: `out/demo_transcript.html`.

## Safety checks
- Verify `COACH_DEMO_MODE=true` before a demo.
- Confirm `ADMIN_TOKEN` exists and has not been committed.
- Review institute configuration, quiet hours, fee policy, and message templates.
- Check Meta webhook delivery and approved templates before live sends.

## Backups
Use SQLite's online backup mechanism while the app is running, or stop the app before copying the database. Back up n8n's data volume too. Encrypt backups, restrict access, set retention, and test restoration.

## Rollback
Keep the previous release/image available. Take a verified database backup before updates. Roll back application code and container image together if health checks fail. Never overwrite newer live data with an older backup without reviewing schema compatibility.

## Incident response
1. Disable scheduled/live sending and force demo mode where appropriate.
2. Preserve relevant logs without exposing credentials or unnecessary personal data.
3. Rotate exposed credentials immediately.
4. Restore from a verified backup and notify responsible staff.
5. Document the cause and prevention steps.
