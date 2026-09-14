# syntax=docker/dockerfile:1
#
# Single stateful image for Railway (ADR-0013): one container serves the JSON API and the
# built React SPA from one Python process, with all persistent state on a Volume mounted at
# CASEV_DATA_ROOT (/data). Two stages:
#   1. `frontend`  — Node builds the SPA (`vite build` → src/app/dist).
#   2. runtime     — Python installs the API deps and serves API + the built SPA.
# Only the built `dist` crosses the stage boundary, so Node and the TS toolchain never ship
# in the final image.

# ---- Stage 1: build the SPA -------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/src/app

# Install deps against the lockfile first so this layer caches across source-only changes.
COPY src/app/package.json src/app/package-lock.json ./
RUN npm ci

# Then the SPA source and the production build (`tsc -b && vite build` → ./dist).
COPY src/app/ ./
RUN npm run build

# ---- Stage 2: API + built SPA runtime ---------------------------------------------------
FROM python:3.12-slim AS runtime

# Keep Python predictable in a container and off the network for pyc caching.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install only the main (runtime) dependencies against the committed lockfile — no dev
# group, and --no-root since the project ships as a pythonpath source tree, not a package.
RUN pip install --no-cache-dir "poetry>=2.0,<3.0"
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

# Application source: the web layer and the domain engine only (no frontend TS source — the
# built SPA arrives from the frontend stage below).
COPY src/api ./src/api
COPY src/core ./src/core

# The built SPA. app.py serves it from src/app/dist (parents[1]/app/dist), so land it there.
COPY --from=frontend /app/src/app/dist ./src/app/dist

# All filesystem state derives from this one root (ADR-0014); the Railway Volume mounts over
# it. Create it so a bare `docker run` without a mount still has somewhere to write.
ENV CASEV_DATA_ROOT=/data
RUN mkdir -p /data

# Railway injects $PORT; default to 8000 for a plain local run. Single uvicorn worker — the
# in-process background runs (ADR-0006) require exactly one instance/worker. Shell form (not
# JSON) so ${PORT} expands; `exec` hands PID 1 to uvicorn so it gets SIGTERM directly and
# shuts down cleanly on a redeploy.
EXPOSE 8000
CMD exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
