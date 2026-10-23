FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /usr/local/bin/

# Build context is the repo root (see /railway.json): the app depends on `runner` and
# `location-overlay` by local path, so both need to be present at the same relative
# location they have on disk for `uv sync` to resolve them.
WORKDIR /repo
COPY src/runner src/runner
COPY src/packages/location-overlay src/packages/location-overlay
COPY demo-app demo-app

WORKDIR /repo/demo-app
RUN uv sync --frozen --no-dev

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
