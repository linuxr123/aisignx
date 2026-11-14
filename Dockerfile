# AiSignX v3 server image.
#   docker build -t aisignx .
#   docker run -d -p 5000:5000 -v aisignx-data:/data aisignx
# All data (database, secret key, uploads, backups, logs) lives in the /data volume.
# There are no default credentials: the setup wizard runs on the first visit.
# See docs/docker.md.
FROM python:3.12-slim

# ffmpeg: video length and thumbnails. chromium: web page previews.
# fonts-dejavu-core: text on title cards and web previews. tini: signal handling as PID 1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg chromium fonts-dejavu-core tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server/ /app/
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
# Strip CR in case the file was checked out with Windows line endings.
RUN sed -i 's/\r$//' /app/docker/entrypoint.sh && chmod 0755 /app/docker/entrypoint.sh

# Run as a non-root user (uid 1000) that owns /data. Bind mounts must be writable by uid 1000.
RUN useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin aisignx \
    && mkdir -p /data \
    && chown aisignx:aisignx /data

ENV AISIGNX_INSTANCE_DIR=/data/instance \
    AISIGNX_UPLOAD_DIR=/data/uploads \
    AISIGNX_BACKUP_DIR=/data/backups \
    AISIGNX_LOG_DIR=/data/logs \
    AISIGNX_BROWSER=/usr/bin/chromium \
    AISIGNX_BROWSER_NO_SANDBOX=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER aisignx
VOLUME /data
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=5)" || exit 1

ENTRYPOINT ["tini", "--", "/app/docker/entrypoint.sh"]
