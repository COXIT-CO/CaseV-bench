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
`*-obj-location.json` ground-truth file:

```
dataset/
  prj1/
    prj1.pdf
    prj1-obj-location.json
  prj2/
    prj2.pdf
    prj2-obj-location.json
```

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
| `--max-px` | `2576` | Target long edge in pixels, if the model isn't in `MODEL_ROSTER` (`core/config.py`) |
| `--threads` | `1` | Pages to send to the model concurrently |

A run that's already partially written (matching `run-id`) resumes: pages already scored
successfully are skipped, and only the rest are sent to the model.

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
