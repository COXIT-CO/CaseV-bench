FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY vendor ./vendor
COPY core ./core

RUN uv sync --frozen --no-dev

# The documented invocation mounts the host's dataset at /dataset and its results/
# directory at /results (--dataset-dir /dataset); this symlink makes the CLI's
# relative "results" default land on that mount instead of /app/results.
RUN mkdir -p /results && ln -s /results /app/results

ENTRYPOINT ["/app/.venv/bin/casev"]
