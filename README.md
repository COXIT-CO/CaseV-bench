# Prompt Web UI Tool

Flask app for testing vision-model prompts (via OpenRouter) against architectural drawings:
it counts or localizes objects — cabinets, countertops, elevations, elevation callouts — on
PDF/image pages, and scores the result against a human-provided expected value, either by
plain count or by IoU-based precision/recall/f1 (`location-scorer`).

## Requirements

- **Python 3.14** — pinned in `.python-version` and `pyproject.toml` (`requires-python = ">=3.14"`).
- **SQLite** — the only database driver configured (`SQLALCHEMY_DATABASE_URI`, see `.env.example`). Any other SQLAlchemy-supported URL should work too, but only SQLite has been exercised here.

## Installation

### a) venv + pip (primary path)

```bash
git clone git@github.com:COXIT-CO/CaseV-bench.git
cd CaseV-bench
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### b) uv + pyproject.toml (alternative, matches the Dockerfile)

`pyproject.toml`/`uv.lock` list the same pinned dependencies as `requirements.txt` — it's what
the `Dockerfile` builds from:

```bash
uv sync
```

(`pip install -e .` does **not** work here — there's no `[build-system]` table and setuptools'
default package auto-discovery fails with multiple top-level directories in the repo. `uv sync`
or `requirements.txt` are the two working install paths.)

### c) Docker

```bash
docker build -t promt-web-ui-tool .
docker run --rm -p 8000:8000 --env-file .env promt-web-ui-tool
```

The entrypoint runs `flask db upgrade` before starting the server, so the container is
self-migrating — nothing else to do first.

### Environment variables

`.env.example` is already in the repo; copy it and fill in real values (`app/config.py`
calls `load_dotenv()` on import, so a `.env` file in the project root is picked up
automatically — no need to `export` anything by hand):

```bash
cp .env.example .env
```

```dotenv
SQLALCHEMY_DATABASE_URI=sqlite:///sqlite.db
OPENROUTER_API_KEY=sk-or-v1-...
MODELS=google/gemma-4-26b-a4b-it:free,qwen/qwen3-coder:free
```

- `SQLALCHEMY_DATABASE_URI` — required. A relative `sqlite:///name.db` resolves under Flask's
  `instance/` folder (gitignored).
- `OPENROUTER_API_KEY` — required.
- `MODELS` — optional, comma-separated list that populates the model dropdown. If unset, it
  falls back to a fixed list of free OpenRouter models (`app/enums.py::DefaultModels`).

### d) Database — Alembic only

Schema is created and updated exclusively through Flask-Migrate/Alembic — there's no
`db.create_all()` fallback, so a fresh database has no tables until this runs:

```bash
flask db upgrade
```

No `FLASK_APP` variable is needed — Flask's CLI auto-discovers `create_app()` from the `app/`
package (verified: `flask db upgrade`/`flask db current`/`flask run` all work with nothing set).
If your shell setup ever needs it spelled out explicitly, it's `FLASK_APP=run:flask_app`.

## Run

```bash
python run.py
```

Starts on **`0.0.0.0:8000`** (hardcoded in `run.py`) — open `http://127.0.0.1:8000/`.

Alternative — the Flask dev server:

```bash
flask run --port 5050
```

`flask run` defaults to port **5000**, which is worth overriding on macOS: Control
Center's AirPlay Receiver listens on 5000 by default on recent macOS versions and will
answer with an unrelated `403` instead of your app ever starting (`lsof -i :5000` will show
`ControlCenter` if this bites you). `python run.py` sidesteps this entirely by using 8000.

## Getting started

1. Open the app (`http://127.0.0.1:8000/`) — you land on the **Projects** list with a **New
   run** composer above it.
2. Fill in the composer:
   - **System prompt** / **User prompt** — the instructions sent with every page image.
   - **Workflow**:
     - `count` — one request per page; trusts whatever counts the model reports directly.
     - `locate` — one request per page; parses object bounding boxes from the response,
       draws them on the page, and derives counts from the parsed objects instead of trusting
       the model's own count.
     - `locate_2pass` — two requests per elevation found: pass 1 finds `elevation`/
       `elevation_callout` boxes on the full sheet, pass 2 crops the original image to each
       elevation and re-detects `cabinet`/`countertop` inside that crop at much higher
       effective resolution. Uses roughly `1 + (one request per elevation found)`.
   - **DPI** (72–600) — resolution the PDF pages are rendered to before sending to the model.
   - **Model** — from `MODELS`, or the built-in free-model fallback.
   - **Expected JSON** (optional) — see [format](#expected-json-format) below.
3. Attach input files (PDF/PNG/JPG), click **Run**.
4. You're redirected to the project's detail page. Runs process in a background thread; the
   status pill polls `/runs/<id>/status` every 2s and the page reloads once it hits
   `COMPLETED` (or shows the error on `FAILED`).
5. On a completed run you'll see, depending on what was provided/selected:
   - **Expected vs actual** — count-based comparison, if `Expected JSON` was a plain counts
     object.
   - **Location score** — IoU-based precision/recall/f1 (overall, per object type, and a
     per-object tp/fp/fn breakdown), if the workflow was `locate`/`locate_2pass` and
     `Expected JSON` was a bbox ground-truth list.
   - **Processed pages** — each rendered page (annotated with boxes for `locate`/
     `locate_2pass`), its local per-page JSON, and — for `locate_2pass` — the per-elevation
     crop gallery.
   - **Full result** — the complete `result.json`, viewable inline, copyable, or downloadable.

Editing the prompt text on the detail page and clicking **Run** again either re-runs the same
project (if the prompt text is unchanged — e.g. just trying a different model or DPI) or
**forks into a new project** (if `system_prompt`/`user_prompt` changed at all), copying the
input files over, so different prompt wordings stay comparable side by side instead of
overwriting each other.

## Expected JSON format

Two shapes are accepted, auto-detected by whether the JSON is a dict or a list
(`app/location_scoring.py::is_bbox_ground_truth`):

**Counts** (for the `count` workflow, or a rough sanity check on `locate*`):

```json
{"cabinets": 23, "countertops": 4, "elevations": 7, "elevation_callouts": 7}
```

**Bbox ground truth** (for `locate`/`locate_2pass`, scored via `location-scorer`):

```json
[
  {"object_type": "cabinet", "bbox": [120, 340, 480, 610], "page": 0}
]
```

## Testing

```bash
venv/bin/pytest
# or, if the venv isn't activated:
source venv/bin/activate && pytest
```

Tests live in `tests/` at the repo root. Currently that's `test_location_scoring.py`: unit
tests for the `label/box/image_index` → `object_type/bbox/page` adapter
(`is_bbox_ground_truth`, `to_score_items`, `score_location`), including a reproduction of
`location-scorer`'s own README worked example end to end. There are no Flask route/integration
tests yet — this covers the scoring logic only.

To sanity-check the location-scorer integration itself beyond the unit tests, run it through
the UI: create a `locate`/`locate_2pass` project with a small, hand-verified bbox ground truth
(mind the 0..1000 / 0-based-page requirements above), run it, and confirm the **Location
score** panel shows plausible (non-zero, when boxes genuinely overlap) precision/recall.

## Project structure

```text
app/
  __init__.py          # Flask app factory
  config.py             # env vars, available-models list, IoU threshold
  extensions.py         # SQLAlchemy / Flask-Migrate singletons
  enums.py               # DefaultModels, PromptStatus
  models.py               # Prompt, PromptRun
  routes.py                 # HTTP endpoints (thin — delegate to services.py)
  services.py                 # create/fork prompts, file handling, process_prompt_run pipeline
  openrouter.py                 # OpenRouter client (OpenAI SDK-compatible)
  pdf_procesing.py                # PDF/image -> PNG page rendering (PyMuPDF)
  crop_utils.py                     # pixel math for locate_2pass pass-2 crops/remapping
  crop_prompts.py                     # fixed system/user prompt text for pass 2
  annotate.py                           # draws detection boxes on rendered pages
  result_parser.py                        # parses model JSON, normalizes counts/objects
  location_scoring.py                       # IoU scoring adapter over location-scorer
  templates/, static/                         # UI
migrations/             # Alembic environment + versioned migrations
tests/                     # pytest suite
data/                         # per-run artifacts (gitignored, created at runtime)
instance/                       # SQLite file (gitignored, created at runtime)
```

## Development

After changing a model in `app/models.py`, generate and apply a migration:

```bash
flask db migrate -m "describe the change"
flask db upgrade
```

Review the autogenerated migration before committing it — Alembic's diff is a starting point,
not a guarantee (see the `server_default` handling in the existing migrations under
`migrations/versions/` for the pattern used on `NOT NULL` columns added to non-empty tables).

## Results

<!-- TODO: заповнити після прогону бенчмарків на реальному наборі креслень.
     Планована структура: таблиця model x workflow x (precision/recall/f1 або
     count accuracy), з посиланням на конкретні run_id для відтворюваності. -->