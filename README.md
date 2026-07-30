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

## Accuracy: One-Stage vs Two-Stage

A small accuracy check against 3 human-labeled architectural PDF sets
(one `google/gemini-3.1-pro-preview` run per workflow, one-shot each — not
a statistically rigorous benchmark, just a directional read on whether the
Two-Stage workflow's extra review/crop step is worth its cost). Every
predicted box is matched to the closest human-labeled box on the same page
with the same label, greedily, by IoU (Intersection-over-Union); a match
only counts as correct at **IoU ≥ 0.5** — the same threshold standard
object-detection benchmarks use.

**Count accuracy** — how close the raw detected count is to the
human-labeled count, regardless of whether individual boxes line up:

| Label | Expected | One-Stage detected | One-Stage count acc. | Two-Stage detected | Two-Stage count acc. |
|---|---:|---:|---:|---:|---:|
| elevation | 50 | 34 | 68% | 34 | 68% |
| cabinet | 73 | 65 | 89% | 79 | 92% |
| countertop | 17 | 10 | 59% | 14 | 82% |
| elevation_callout | 7 | 25 | -157% | 16 | -29% |
| **total** | **147** | **134** | **91%** | **143** | **97%** |

**Localization accuracy** — precision/recall/F1 at IoU ≥ 0.5, and the mean
IoU of every matched pair (higher = tighter box agreement with the human
label):

| Label | One-Stage P | One-Stage R | One-Stage F1 | One-Stage mean IoU | Two-Stage P | Two-Stage R | Two-Stage F1 | Two-Stage mean IoU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| elevation | 85% | 58% | 69% | 0.90 | 100% | 68% | 81% | 0.93 |
| cabinet | 29% | 26% | 28% | 0.70 | 75% | 81% | 78% | 0.91 |
| countertop | 20% | 12% | 15% | 0.60 | 79% | 65% | 71% | 0.81 |
| elevation_callout | 0% | 0% | — | — | 12% | 29% | 17% | 0.64 |
| **overall** | **37%** | **34%** | **36%** | | **74%** | **72%** | **73%** | |

Takeaways from this sample:

- Two-Stage wins clearly on **cabinet** and **countertop** localization —
  cropping each elevation before asking for details gives the model a much
  larger, less cluttered view of each object, which shows up as both
  higher recall and tighter boxes (higher mean IoU), not just better
  counts.
- `elevation_callout` is the weak point for both workflows — it's a small,
  visually inconsistent multi-part symbol (see the recognition rules in
  `drafts/prompts/System Stage 1`), both workflows over-detect it, and
  IoU ≥ 0.5 is a strict bar for a symbol this size; a few pixels of
  disagreement with the human label on where exactly the box should end is
  enough to fail the threshold even when the model found the right symbol.
- Both workflows predict the same *number* of elevations (34) since
  Two-Stage's elevation boxes come from that identical Stage 1 pass — but
  Two-Stage's are all correct (100% precision, every one matches a human
  label) while One-Stage's 34 include 5 that don't match anything real,
  which is exactly what the Stage 1 human review step is there to catch
  before Stage 2 (and the final saved result) ever sees them.

### Per-file breakdown

The 3 tables above are the 3 files below summed together. Individually,
the gap between workflows is even more pronounced on the busiest file
(project-0001, 70 expected objects across 4 pages) than on the smallest
one (project-0002, 45 expected objects across 2 pages) — more content per
page seems to hurt One-Stage's single-pass detection more than it hurts
Two-Stage's cropped, per-elevation one.

<details>
<summary>project-0001 (prj0001.pdf.pdf, 4 pages)</summary>

**Count comparison**

| Label | Expected | One-Stage detected | One-Stage count acc. | Two-Stage detected | Two-Stage count acc. |
|---|---:|---:|---:|---:|---:|
| elevation | 27 | 21 | 78% | 21 | 78% |
| cabinet | 31 | 16 | 52% | 40 | 71% |
| countertop | 7 | 3 | 43% | 6 | 86% |
| elevation_callout | 5 | 11 | -20% | 5 | 100% |
| **total** | **70** | **51** | **73%** | **72** | **97%** |

**Localization accuracy**

| Label | One-Stage P | One-Stage R | One-Stage F1 | One-Stage mean IoU | Two-Stage P | Two-Stage R | Two-Stage F1 | Two-Stage mean IoU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| elevation | 76% | 59% | 67% | 0.92 | 100% | 78% | 88% | 0.95 |
| cabinet | 0% | 0% | — | — | 55% | 71% | 62% | 0.84 |
| countertop | 0% | 0% | — | — | 50% | 43% | 46% | 0.77 |
| elevation_callout | 0% | 0% | — | — | 20% | 20% | 20% | 0.70 |
| **overall** | **31%** | **23%** | **26%** | | **65%** | **67%** | **66%** | |

</details>

<details>
<summary>project-0002 (01+3T+MRI+Cabinets+Drawings.pdf, 2 pages)</summary>

**Count comparison**

| Label | Expected | One-Stage detected | One-Stage count acc. | Two-Stage detected | Two-Stage count acc. |
|---|---:|---:|---:|---:|---:|
| elevation | 9 | 7 | 78% | 7 | 78% |
| cabinet | 27 | 21 | 78% | 24 | 89% |
| countertop | 7 | 4 | 57% | 5 | 71% |
| elevation_callout | 2 | 9 | -250% | 6 | -100% |
| **total** | **45** | **41** | **91%** | **42** | **93%** |

**Localization accuracy**

| Label | One-Stage P | One-Stage R | One-Stage F1 | One-Stage mean IoU | Two-Stage P | Two-Stage R | Two-Stage F1 | Two-Stage mean IoU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| elevation | 100% | 78% | 88% | 0.93 | 100% | 78% | 88% | 0.95 |
| cabinet | 48% | 37% | 42% | 0.59 | 92% | 81% | 86% | 0.96 |
| countertop | 25% | 14% | 18% | 0.63 | 100% | 71% | 83% | 0.83 |
| elevation_callout | 0% | 0% | — | — | 17% | 50% | 25% | 0.57 |
| **overall** | **44%** | **40%** | **42%** | | **83%** | **78%** | **80%** | |

</details>

<details>
<summary>project-0003 (Attachment+5+Drawings+Cabinetry.pdf, 9 pages)</summary>

**Count comparison**

| Label | Expected | One-Stage detected | One-Stage count acc. | Two-Stage detected | Two-Stage count acc. |
|---|---:|---:|---:|---:|---:|
| elevation | 14 | 6 | 43% | 6 | 43% |
| cabinet | 15 | 28 | 13% | 15 | 100% |
| countertop | 3 | 3 | 100% | 3 | 100% |
| elevation_callout | 0 | 5 | — | 5 | — |
| **total** | **32** | **42** | **69%** | **29** | **91%** |

**Localization accuracy**

| Label | One-Stage P | One-Stage R | One-Stage F1 | One-Stage mean IoU | Two-Stage P | Two-Stage R | Two-Stage F1 | Two-Stage mean IoU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| elevation | 100% | 43% | 60% | 0.81 | 100% | 43% | 60% | 0.85 |
| cabinet | 32% | 60% | 42% | 0.83 | 100% | 100% | 100% | 0.95 |
| countertop | 33% | 33% | 33% | 0.57 | 100% | 100% | 100% | 0.82 |
| elevation_callout | 0% | — | — | — | 0% | — | — | — |
| **overall** | **38%** | **50%** | **43%** | | **83%** | **75%** | **79%** | |

</details>

Only project-0003 has zero expected callouts, so its callout row has no
meaningful count-accuracy % (division by zero) — shown as `—`; both
workflows still detected 5 there that don't match any real callout (0%
precision on that file specifically).

This isn't reproducible from a fresh clone as-is — the human-labeled
ground truth and the raw per-run JSON it's compared against live in the
(gitignored, local-only) `drafts/expected/` and `drafts/results/`
directories, not in the repo.