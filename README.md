# CaseV-Bench

Benchmark tool for comparing multiple vision-language models on architectural
millwork drawing detection — cabinets, countertops, elevations, and
elevation callouts — extracted from uploaded PDF drawing sets.

It has two workflows, in two tabs of the same app:

- **Benchmark** — send one or more full PDF drawing sets to up to 3 models
  at once and compare their raw detections side by side.
- **Two-Stage Detection** — a slower, human-in-the-loop workflow for a
  single model: find `elevation` frames (and `elevation_callout` symbols)
  first, let a person review/approve the elevations, then crop and
  re-detect the details inside each approved elevation. Meant for
  higher-precision runs where a blind single-pass detection isn't accurate
  enough.

## Requirements

- Docker + Docker Compose
- An [OpenRouter](https://openrouter.ai) API key

## Setup

1. Copy the example env file and fill in your key:

   ```bash
   cp .env.example .env
   ```

   ```
   OPENROUTER_API_KEY=your-key-here
   ```

2. Build and start the app (first run only):

   ```bash
   docker-compose up --build
   ```

3. On every run after that, you don't need `--build` again:

   ```bash
   docker-compose up
   ```

4. Open the app at **http://localhost:8000**

`docker-compose.yml` mounts `backend/`, `frontend/`, and `history/` straight
into the container, so editing those files on the host is picked up without
rebuilding — restart the container to pick up backend changes, refresh the
browser for frontend-only ones.

## Using it — Benchmark tab

- Drag & drop one or more PDF drawing sets into the viewer.
- Configure 1-3 models (model ID + prompt each), the shared **System
  Prompt**, and the render **DPI**.
- Open **Execution Settings** to control how requests are batched and
  ordered:
  - **Models** — query configured models one after another, or all at once.
  - **Files** — send every uploaded PDF in a single request per model, or
    split into one request per file (then choose sequential/parallel).
  - **Pages** — within a file (or file group), send all its pages together
    in one request, or split into one request per page (then choose
    sequential/parallel).
- Hit **Run Benchmark** to send the drawings to every configured model and
  compare results side by side, with bounding-box overlays on the original
  pages. Use the bbox selector and "outline only" toggle to compare models
  visually; open **Full Page View** to pan/zoom/crop-to-zoom a page at high
  resolution.
- Every run is saved to the **History** tab automatically.

## Using it — Two-Stage Detection tab

A separate, single-model workflow for cases where asking one model to find
everything in one pass isn't precise enough. It splits detection into two
passes with a human review step in between:

1. **Stage 1 — elevations + callouts.** Upload one or more PDFs. Stage 1
   scans every page of every uploaded file (one full-page image per
   request) and finds `elevation` frames *and* `elevation_callout` symbols
   in the same pass — callouts live on floor plans/RCPs, never inside an
   elevation, so they need no further detail pass and are carried straight
   through to the final result once you continue to Stage 2.
2. **Review.** Uncheck any detected elevation that's wrong before
   continuing — only checked elevations get cropped and sent onward. Use
   the file selector and Prev/Next to review every page; each page keeps
   its own checklist, so you can navigate freely without losing progress.
3. **Stage 2 — details.** Re-renders each reviewed page from the original
   PDF (at its own DPI, independent from Stage 1's) and crops out every
   approved elevation, asking the model for `cabinet` and `countertop`
   within each crop. The final result — elevations, callouts, cabinets,
   countertops, all on the full page — is drawn back and saved to History
   in the same JSON shape a Benchmark run uses.

Every page is still sent to the model as its own independent request in
both stages (never batched together) — the tab's own **Execution
Settings** only controls whether those per-file/per-page requests fire
sequentially or in parallel, the same idea as the Benchmark tab's file/page
execution modes, just without a batching axis.

The **System Prompt** button lets you use one shared system prompt for
both stages, or set separate prompts per stage.

## History

- Every run (Benchmark or Two-Stage) is saved automatically: the exact
  rendered pages, prompts, settings, and each model's raw response.
- Browse past runs, click one to see its full detail, page through its
  images with the detection overlays on.
- **Replay this run's setup** repopulates the form (model(s), prompts, DPI,
  execution settings) from a past run — you re-upload the source PDF(s)
  yourself, nothing else needs retyping.
- Paste an expected/ground-truth summary (`{"cabinets": N, ...}`) against
  any model's result to see a pass/fail comparison against what was
  actually detected.

## Project structure

```
backend/     FastAPI service — PDF rendering, model calls, history storage
frontend/    Static UI (vanilla HTML/JS/CSS) — Benchmark + Two-Stage tabs
history/     Runtime-generated: saved runs (rendered pages + metadata),
             pruned automatically once HISTORY_MAX_RUNS is exceeded
```