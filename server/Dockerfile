# AISignXV2 — minimal single-server image (spec §4.1).
#
# Runs the Flask app under gunicorn with eventlet workers so SSE keep-alive
# connections (one per display) don't block worker threads. ffmpeg + a
# headless Chromium (for plugin webpage thumbnails) are baked in.
#
# Build:   docker build -t aisignx:latest .
# Run:     see docker-compose.yml — the compose file is the supported path.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

# System deps:
#   - ffmpeg: video transcode + thumbnail extraction
#   - libs needed by Playwright's bundled Chromium (headless thumbnails)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates curl \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
        libcairo2 libasound2 libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install gunicorn eventlet \
    && python -m playwright install --with-deps chromium

# App code.
COPY . .

# Persistent volumes (created at runtime by docker-compose):
#   /data/uploads   tenant media + thumbnails
#   /data/db        sqlite database file
#   /data/logs      app logs
RUN mkdir -p /data/uploads /data/db /data/logs

ENV UPLOAD_FOLDER=/data/uploads \
    AISIGNX_DB_PATH=/data/db/signage.sqlite \
    AISIGNX_LOG_DIR=/data/logs

EXPOSE 5000

# eventlet worker is required for SSE; one worker is enough for ~1,000
# displays per the spec (§9, §12). Bump --workers if you scale further.
CMD ["gunicorn", \
     "--worker-class", "eventlet", \
     "--workers", "1", \
     "--threads", "1", \
     "--timeout", "120", \
     "--bind", "0.0.0.0:5000", \
     "app:app"]
