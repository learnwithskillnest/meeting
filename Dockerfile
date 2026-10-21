# HeartSync production image
FROM python:3.13-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DATABASE_PATH=/data/heartsync.db \
    MEDIA_ROOT=/data/media

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY heartsync ./heartsync
COPY scripts ./scripts

# Run as an unprivileged user; /data is the persistent volume (DB + private media).
RUN useradd --create-home --uid 10001 heartsync && mkdir -p /data && chown heartsync:heartsync /data
USER heartsync
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

# Single worker: realtime chat uses an in-process event hub (see docs/DEPLOYMENT.md).
CMD ["uvicorn", "heartsync.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1"]
