# CaseV-Bench demo

A single-window web app: upload a drawing sheet PDF, run detection against a model over
OpenRouter, and look at the results — original and annotated pages side by side, a
per-label/per-page summary table, and the full list of detections. Model and prompt are
tucked behind a settings icon rather than in the main flow; past runs are kept in a
history drawer you can revisit or delete.

It reuses the same rendering/model-call/parsing building blocks as the [`casev`
CLI](../src/runner) and [`location-overlay`](../src/packages/location-overlay) for
drawing boxes — this is an interface on top of that, not a separate pipeline.

## Run locally

```bash
cd demo-app
uv sync
export OPENROUTER_API_KEY=...
uv run uvicorn app.main:app --reload
```

Open `http://localhost:8000`. Without `DEMO_APP_DATABASE_URL` set, everything works
except history — detection still runs and displays normally, the history drawer is just
empty (see [History](#history) below).

## Run locally with Docker

The Dockerfile depends on `src/runner` and `src/packages/location-overlay` by local
path, so the build context has to be the **repo root**, not `demo-app/`:

```bash
# from the repo root
docker build -f demo-app/Dockerfile -t casev-demo .
docker run --rm -p 8000:8000 -e OPENROUTER_API_KEY=your-key-here casev-demo
```

To also get history working locally, run Postgres alongside it on a shared network,
with its data on your own disk so it survives container restarts:

```bash
# one-time: a network so the two containers can reach each other by name
docker network create casev-demo-net

# Postgres, dedicated to this — not the results_store used elsewhere in this repo
docker run -d --name casev-demo-pg --network casev-demo-net \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  -v "$(pwd)/volume/pgdata:/var/lib/postgresql/data" \
  postgres:16

docker run --rm --network casev-demo-net -p 8000:8000 \
  -e OPENROUTER_API_KEY=your-key-here \
  -e DEMO_APP_DATABASE_URL="postgresql://postgres:postgres@casev-demo-pg:5432/postgres" \
  casev-demo
```

`casev-demo-pg` isn't started with `--rm`, so it survives across runs — restart it with
`docker start casev-demo-pg` rather than recreating it, or history disappears each time.
To wipe history entirely, stop it and delete `volume/pgdata`.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | Calls the model. Missing/invalid surfaces as a normal error in the UI, not a crash. |
| `DEMO_APP_DATABASE_URL` | no | Enables the history drawer. A Postgres connection string, entirely separate from `src/results_store`'s shared research store — see [History](#history). |
| `PORT` | no (default `8000`) | What the Dockerfile's `uvicorn` binds to; Railway injects this itself. |
| `CASEV_MODEL_ROSTER` | no | Read by `core.config`, not demo-app itself — overrides the model dropdown's options (JSON object, slug → max render px). |

## Limits

| | |
|---|---|
| Upload size | 10MB |
| Pages per PDF | 25 |
| Model call timeout | 150s per attempt, 3 attempts |

The page and size caps exist because a large run ties up real wall-clock time (rendering
+ however many model calls, each with its own retries) — bounding it keeps a single run
from running past whatever timeout a reverse proxy in front of this enforces. See
[How a run actually works](#how-a-run-actually-works) for how that's handled even within
the cap.

## How a run actually works

`POST /api/detect` returns almost immediately with a job id — it does **not** hold the
HTTP connection open for the whole run. Detection continues on a background thread; the
frontend polls `GET /api/jobs/{id}` every ~1.5s for progress and completed pages, and
deletes the job once it has the final result. This matters for two things:

- **No single request is ever long-lived**, however many pages there are or however slow
  a model call is — nothing depends on a proxy's timeout being generous.
- **A page refresh doesn't lose the run.** The active job id is kept in the browser's
  `localStorage`; reloading (or reopening the tab later) picks the same job back up and
  resumes showing progress, rather than leaving no sign a run was ever started.

Each page streams out (rendered image, annotated image, detections) the moment that page
finishes, rather than the server collecting every page before responding — a large run's
memory is bounded by how many pages are being worked on concurrently, not by total page
count.

## History

Saved automatically after every completed run (even ones where every page failed — that's
still useful to see). What's stored is deliberately **not** the rendered images: just the
original PDF, the prompt and model used, and the parsed detection boxes (tiny JSON).
Opening a history entry re-renders the PDF and redraws the overlay from those stored boxes
— the same local, no-network steps a live run uses, just skipping the model call. That
replay also renders at a lower resolution (1800px vs. whatever the model needed, up to
5000px) since it's for looking at, not re-analyzing.

## Testing

```bash
cd demo-app
uv sync
uv run pytest
```

No network, no real Postgres, no `OPENROUTER_API_KEY` needed — model calls are replaced
with a stub client (see `tests/helpers.py`), and the history tests explicitly unset
`DEMO_APP_DATABASE_URL` to exercise the no-database path everything else depends on.

## Layout

| Path | |
|---|---|
| `app/main.py` | FastAPI app: routes, job orchestration, the `Cache-Control: no-cache` middleware |
| `app/detect.py` | The actual pipeline: render → call model → parse → annotate, plus history replay |
| `app/jobs.py` | In-memory job registry the frontend polls (see [How a run actually works](#how-a-run-actually-works)) |
| `app/history.py` | Postgres persistence for past runs — a no-op wherever `DEMO_APP_DATABASE_URL` isn't set |
| `static/` | The single-page frontend: plain HTML/CSS/JS, Tailwind via CDN, no build step |
| `tests/` | pytest suite — see [Testing](#testing) |

## Deploying

Railway config lives at the repo root (`railway.json`), pointed at
`demo-app/Dockerfile` with the repo root as build context. Needed on the Railway
service: `OPENROUTER_API_KEY`, and — for history — a **dedicated** PostgreSQL plugin
(not the one `src/results_store` might use elsewhere in this project) with its
connection string wired up as `DEMO_APP_DATABASE_URL`.
