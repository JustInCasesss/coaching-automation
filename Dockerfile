FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    COACH_DATA_DIR=/data \
    COACH_OUT_DIR=/data/out \
    COACH_DB=/data/coaching.db \
    COACH_OUTBOX=/data/outbox.jsonl \
    COACH_CONFIG=/app/config/institute.json

WORKDIR /app

COPY coaching_bot/ ./coaching_bot/
COPY config/ ./config/

RUN mkdir -p /data/out \
    && useradd --system --uid 10001 --create-home app \
    && chown -R app:app /app /data

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).read()" || exit 1

CMD ["python", "-m", "coaching_bot.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]
