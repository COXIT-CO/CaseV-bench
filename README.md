# CaseV-Bench

An educational benchmark for evaluating multimodal LLMs on AEC (architecture / engineering /
construction) drawings, with a **Prompt & Config Lab** for authoring prompts, launching scored
runs against a model catalog, and inspecting results and location overlays.

The app is a single deployable unit: a **FastAPI JSON API** that also serves a built **React
SPA**, with all persistent state (SQLite DB, ingested page images, overlay PNGs) under one data
root. See [`docs/adr/`](docs/adr/) for the decision record and
[`docs/glossary.md`](docs/glossary.md) for domain terms.

## Layout

```
src/
  app/    React + Vite + TypeScript SPA (the Prompt & Config Lab)   → ADR-0010
  api/    FastAPI web layer — routers/ + thin wiring, no business logic
  core/   Domain engine — services, adapters, models, prompts, db, config, cli
data/     Local data root (CASEV_DATA_ROOT); only data/input/ fixtures are tracked
docs/     ADRs, specs, runbooks, glossary
tests/    pytest suite (JSON-API contract + service tests)
```

`core` imports nothing from `api`. Every path under the data root derives from a single
`CASEV_DATA_ROOT` ([ADR-0014](docs/adr/0014-config-pydantic-settings-data-root.md)).

## Prerequisites

- **Python ≥ 3.11** and **[Poetry](https://python-poetry.org/) ≥ 2.1** (2.1 is required — the
  dev tools live in a PEP 735 `[dependency-groups]` table).
- **Node ≥ 22** and npm (for the SPA).
- An **`OPENROUTER_API_KEY`** — only needed to launch real model runs; the app imports and the
  tests run without it.
- **Docker** — only for the container / production-parity path.

## Local setup

**Backend** (from the repo root):

```bash
poetry install --with dev --no-root
```

`--no-root` because the project ships as a `pythonpath` source tree (`pyproject` sets
`pythonpath = ["src"]`), not an installed package.

**Frontend**:

```bash
cd src/app
npm ci
```

**Provider secret** (only for launching runs): create `src/core/.env` (git-ignored) with

```
OPENROUTER_API_KEY=sk-or-...
```

or export it in your shell. A missing key fails only at the moment a run is launched, with a
clear message — importing the app and running the tests never need it.

## Running in dev

Two processes. The Vite dev server proxies `/api/*` to uvicorn, so there is no CORS to
configure.

**Terminal 1 — API** (port 8000):

```bash
PYTHONPATH=src poetry run uvicorn api.app:app --reload --port 8000
```

**Terminal 2 — SPA** (port 5173, proxying to the API):

```bash
cd src/app
npm run dev
```

Open **http://localhost:5173**. (Point the proxy elsewhere with
`VITE_API_TARGET=http://host:port npm run dev`.) State is written under `./data/` by default.

## Running via the container

The exact production image builds the SPA in a Node stage and serves API + built SPA from one
Python process (one uvicorn worker):

```bash
docker build -t casev-bench:local .
docker run --rm -p 8000:8000 \
  -e OPENROUTER_API_KEY=sk-or-... \
  -v "$PWD/.localdata":/data \
  casev-bench:local
# → http://localhost:8000   (health at /api/health)
```

The `-v …:/data` mount is the local stand-in for the Railway Volume: state written there
survives `docker rm` and a re-run, exactly as it survives a redeploy in production. The
container defaults `CASEV_DATA_ROOT=/data`.

## Running the tests

**Backend** — pytest, plus the two formatters CI enforces
([ADR-0017](docs/adr/0017-ci-gate-railway-deploy.md)):

```bash
poetry run pytest
poetry run black --check src tests
poetry run isort --check-only src tests
```

**Frontend** — build (also the production build) and unit tests:

```bash
cd src/app
npm run build     # tsc -b && vite build
npm run test      # vitest run
```

The same two jobs run as the PR gate in [`.github/workflows`](.github/workflows/); the branch
protection wiring is in [`docs/runbook-ci-branch-protection.md`](docs/runbook-ci-branch-protection.md).

## Deploying

The app runs on **Railway** as a **single stateful container** with one persistent **Volume
mounted at `/data`** ([ADR-0013](docs/adr/0013-deploy-railway-single-container.md)). Railway's
native git-deploy owns CD ([ADR-0017](docs/adr/0017-ci-gate-railway-deploy.md)) — a push to the
watched branch builds from the `Dockerfile` and rolls the container. There is no Docker build in
CI.

Two operational constraints are non-negotiable:

- **Single instance, no scale-to-zero.** Runs execute as in-process background threads
  ([ADR-0006](docs/adr/0006-in-process-background-execution.md)), so a second replica or an idle
  scale-down would drop in-flight runs. `railway.json` pins `numReplicas: 1` and
  `sleepApplication: false`, and the Volume also constrains the service to one replica.
- **Volume backups are manual.** Snapshot the `/data` Volume (Railway's Volume backup, or
  `railway ssh` + copy `/data` off-box) **before any risky migration** — there is no automated
  backup yet, and the schema is still `create_all` (no migrations), so a schema-changing
  rollback is not safe.

The full operator's checklist — one-time service/Volume/env setup, verifying a deploy, rollback,
and local parity — is in **[`docs/runbook-deploy-railway.md`](docs/runbook-deploy-railway.md)**.
