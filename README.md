# CaseV-Bench

[![Weekly benchmark](https://github.com/COXIT-CO/CaseV-bench/actions/workflows/weekly-benchmark.yml/badge.svg)](https://github.com/COXIT-CO/CaseV-bench/actions/workflows/weekly-benchmark.yml)

A benchmark for measuring how well multimodal LLMs locate objects on architecture and
engineering drawings.

Drawing sheets are a hard input for vision models: a single page mixes large views with symbols a
few millimetres across, the text is small, and annotations overlap the geometry. This benchmark
measures how well a model handles that, with one task. The model receives a full drawing sheet
as an image and a fixed prompt, and returns a bounding box for every object of five types it
finds. The predictions are scored against boxes annotated by hand. Every model gets the same
sheets, the same prompt and the same scoring; the prompt is not tuned per model and the sheet is
not cropped.

## Object types

| Object | Description |
|---|---|
| `floor_plan` | A top-down view of a room or space. A large region of the sheet. |
| `elevation` | A view of one object, assembly or wall from a single direction. Also large. |
| `cabinet` | A single storage unit in front view. Small, usually several in a row. |
| `countertop` | A horizontal work surface, including its backsplash if present. |
| `callout` | A small circle or diamond with text referencing another view or sheet. |

A predicted box is a true positive if it has the right label and overlaps a ground-truth box of
that label with IoU ≥ 0.5. Unmatched predictions are false positives, unmatched ground-truth
boxes are false negatives. Precision, recall and F1 are reported per drawing and for the run as
a whole. Scoring is implemented in [`location-scorer`](src/packages/location-scorer), a
separate package with no dependencies, so a set of predictions can be scored outside this repo.

## Weekly results

A GitHub Actions workflow runs every Monday, sends the whole dataset through each model in the
roster and rewrites the table below. Prompt, dataset, render settings and temperature are fixed
between runs, so a change in the numbers means the model behind the slug changed.

This is a drift check, not a leaderboard. The dataset is small enough to notice that a model
behaves differently from the previous week, but not enough to rank models against each other.

<!-- BENCHMARK_DASHBOARD:START -->
_Last updated 2026-09-18 21:32 UTC by the weekly benchmark workflow._

| Model                        | F1 @ IoU 0.5 | Δ vs. previous run | Last run   |
|------------------------------|--------------|--------------------|------------|
| `anthropic/claude-fable-5.1` | 0.476        | • +0.000           | 2026-09-18 |
| `google/gemini-3.8-flash`    | 0.454        | • +0.000           | 2026-09-18 |
| `openai/gpt-6-astra`         | 0.651        | • +0.000           | 2026-09-18 |

<!-- BENCHMARK_DASHBOARD:END -->

## Running it

Requires Docker and an [OpenRouter](https://openrouter.ai/) API key. Any OpenRouter model slug
is accepted.

All configuration is environment variables — see [`.env.example`](.env.example) for the full
list. `docker compose` reads a `.env` file in this directory automatically, so copy it and fill
in your key:

```bash
cp .env.example .env
# edit .env, or just: export OPENROUTER_API_KEY=...

docker compose build runner

# Check that the dataset loads. No network calls.
docker compose run --rm runner validate

# Render every page, send it to the model, score the predictions.
docker compose run --rm runner run --model google/gemini-3.5-flash

# Re-score an existing run without calling the model again.
docker compose run --rm runner score --run-dir results/google-gemini-3.5-flash__20260913
```

Output goes to `src/runner/results/<run-id>/`: a `run.json` with the config and prompt hash, a
`scores.json` for the run, and a directory per drawing with raw model responses, parsed boxes
and per-drawing scores. An interrupted run resumes when started again with the same `--run-id`.
`--threads N` sends N pages concurrently.

Flags, output format and running without Docker are documented in
[`src/runner/README.md`](src/runner/README.md), including how prompt versions are named and
pinned ([Prompt versioning](src/runner/README.md#prompt-versioning)).

## Dataset

[`dataset/`](dataset) is the *balanced* subset of the full internal dataset — roughly 10% of it,
picked to be representative rather than exhaustive. It is not a complete set used for internal
evaluation. It doubles as the sample sent through every model in the weekly run, so it needs to
be small enough to run weekly but varied enough that a shift in a model's numbers over time is
meaningful rather than noise.

<!-- TODO: this section describes the dataset once the balanced-dataset selection lands (see the
`balanced-dataset` branch). Until then, drawing count and counts below are placeholders. -->

It contains TBD drawings, each a PDF sheet and a JSON file with the ground-truth boxes.
Annotators used the same object definitions as the prompt, drawing boxes with
[`annotation_tool/`](annotation_tool), a small PDF viewer with annotating functionality.
Current counts:

| Object | Boxes |
|---|---:|
| `cabinet` | TBD |
| `elevation` | TBD |
| `callout` | TBD |
| `countertop` | TBD |
| `floor_plan` | TBD |

## Layout

| Path | |
|---|---|
| [`dataset/`](dataset) | Drawings and ground truth |
| [`annotation_tool/`](annotation_tool) | GUI used to annotate the dataset's ground-truth boxes |
| [`src/runner/`](src/runner) | The `casev` CLI: render, prompt, parse, score |
| [`src/packages/location-scorer/`](src/packages/location-scorer) | Scoring library, versioned and tagged separately |
| [`src/results_store/`](src/results_store) | Schema for the shared Postgres table that weekly scores are published to |
