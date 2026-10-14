# Zero-dependency image: the app is pure Python stdlib, so there is nothing to
# pip install and nothing to break on a dependency bump.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    COACH_DATA_DIR=/data \
    COACH_OUT_DIR=/data/out \
    COACH_DB=/data/coaching.db \
    COACH_OUTBOX=/data/outbox.jsonl

WORKDIR /app

COPY coaching_bot/ ./coaching_bot/
COPY config/ ./config/
COPY n8n/ ./n8n/
COPY docs/ ./docs/
COPY README.md ./

RUN mkdir -p /data/out \
 && useradd --system --uid 10001 --create-home bot \
 && chown -R bot:bot /app /data
USER bot

EXPOSE 8080

# Liveness only - the webhook is stateless, so /health is a true readiness probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "coaching_bot.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]
