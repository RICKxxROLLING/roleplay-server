# syntax=docker/dockerfile:1

# ---------- stage 1: build the React UI ----------
FROM node:20-alpine AS web

WORKDIR /web
# Copy the manifest first so `npm install` is cached independently of source edits.
COPY web/package.json ./
RUN npm install --no-audit --no-fund

COPY web/ ./
RUN npm run build


# ---------- stage 2: runtime ----------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PUID=10001 \
    PGID=10001

# gosu drops privileges cleanly after the entrypoint fixes up ownership.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies + the app package. Kept as one step because pyproject.toml
# is the single source of truth for deps -- duplicating them here would drift.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# Built UI, served by FastAPI at / (see app/main.py)
COPY --from=web /web/dist ./static

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The container starts as root purely so the entrypoint can align uid/gid with
# the host's appdata ownership; it then execs the server as `appuser`.
RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app

VOLUME ["/data"]
EXPOSE 8000

# 200 means the app is serving. Whether the *model* is reachable is reported
# inside the payload, and shouldn't mark the container unhealthy on its own.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
