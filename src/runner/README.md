# CaseV runner

A CLI that sends AEC drawing sheets to a multimodal model over OpenRouter, asks it to locate
cabinets, countertops, elevations, floor plans, and callouts, and scores the predictions
against ground truth using [`location-scorer`](vendor/location_scorer-0.1.0.tar.gz).

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- An [OpenRouter](https://openrouter.ai/) API key (only needed for `casev run`)

## Install

```bash
uv sync
```

## Dataset layout

Each drawing lives in its own directory containing exactly one PDF and one
`*obj-location.json` ground-truth file (`obj-location.json` or `<name>-obj-location.json`).

```
dataset/
  public/
    drawing.pdf
    obj-location.json
```

The drawing is named after `project_id` in the JSON, falling back to the directory name.

Point the CLI at the dataset root (a directory of drawing directories) or at a single drawing
directory, either via `--dataset-dir` or the `CASEV_DATASET_DIR` environment variable.

## Usage

```bash
export OPENROUTER_API_KEY=...       # only needed for `run`
export CASEV_DATASET_DIR=/path/to/dataset

# Sanity-check the dataset — no network calls.
uv run casev validate

# Render each page, call the model, parse and score the predictions.
uv run casev run --model google/gemini-3.5-flash

# Re-score an existing run directory without calling the model again.
uv run casev score --run-dir results/google-gemini-3.5-flash__20260913
```

Useful `run` flags:

| Flag | Default | Purpose |
|---|---|---|
| `--model` | *(required)* | OpenRouter model slug |
| `--run-id` | `<model>__<date>` | Explicit run id, for deterministic resume/reruns |
| `--out-dir` | `results` | Where run directories are written |
| `--max-px` | `5000` | Target long edge in pixels, if the model isn't in `MODEL_ROSTER` (`core/config.py`) |
| `--threads` | `1` | Pages to send to the model concurrently |

`casev run --model <slug>` accepts any OpenRouter model, roster or not — a model missing from
the roster just prints a warning and falls back to `--max-px`. The model roster (`core/config.py`)
only controls (1) the max-px cap applied automatically to a *listed* model, so you don't have to
pass `--max-px` yourself, and (2) which models the weekly benchmark workflow runs, since its CI
job builds its matrix from the roster's keys. Override it without a PR by setting
`CASEV_MODEL_ROSTER` to a JSON object of the same shape, e.g.:

```bash
export CASEV_MODEL_ROSTER='{"vendor/model-x": 5000}'
```

Set it this way (or in a `.env` file — see [`.env.example`](../../.env.example) at the repo
root) to change a local run's max-px cap, or as a GitHub Actions repo Variable (Settings >
Secrets and variables > Actions > Variables) to change what the weekly benchmark runs.

A run that's already partially written (matching `run-id`) resumes: pages already scored
successfully are skipped, and only the rest are sent to the model.

### Prompt versioning

The prompt sent to the model lives in `core/prompts/` as a plain `.md` file named
`<name>_v<N>.md` (currently `object_location_v1.md`, pinned by `PROMPT_PATH` in
`core/config.py`). A prompt file is never edited once a run has used it — a change to the
wording ships as a new `_v<N+1>.md` file and a new `PROMPT_PATH`, so old runs stay
reproducible. Every run also records a sha256 hash of the exact prompt text in `run.json`
(`prompt_hash`), which feeds the run's `compatibility_key` alongside dataset version and
render settings, so runs that used a different prompt are never silently compared as if they
matched.

### Output

Each run writes a directory under `--out-dir`:

```
results/<run-id>/
  run.json              # config, prompt hash, dataset version, aggregate status
  scores.json           # scores across the whole run, at a sweep of IoU thresholds
  <drawing>/
    p0001.json           # raw model call record for that page
    predictions.jsonl     # parsed boxes for the drawing
    scores.json           # per-drawing scores
```

Rendered pages are cached under `results/.render-cache/`, keyed by drawing and pixel size, and
reused across runs and models that share a render size.

## Docker

```bash
docker build -t casev-runner .
docker run --rm \
  -e OPENROUTER_API_KEY=... \
  -v /path/to/dataset:/dataset \
  -v /path/to/results:/results \
  casev-runner run --model google/gemini-3.5-flash --dataset-dir /dataset
```

The image's `results/` is symlinked to `/results`, so mounting a host directory there persists
run output without needing `--out-dir` on every invocation.

## Development

```bash
make check      # lint + typecheck + test
make lint
make typecheck
make test
```
