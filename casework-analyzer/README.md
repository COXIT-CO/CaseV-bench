# Casework Drawing Analyzer

A tool for testing how well LLMs detect and count objects — cabinets,
elevations, countertops, and elevation callouts — in architectural casework
drawing PDFs. Every model is routed through [OpenRouter](https://openrouter.ai),
so switching between vendors (Anthropic, OpenAI, Google, ...) is just a
`config.yaml` edit, not a code change. It's the "run the benchmark" companion
to the ground-truth data in [`../dataset`](../dataset) and the annotation tool
in [`../annotation_tool`](../annotation_tool): upload a drawing, pick pages,
iterate on a prompt against the model of your choice, export results in the
same `obj-count.json` / `obj-location.json` shapes used by the rest of this
repo, and score a run directly against ground truth right in the UI.

## Quick start (Docker)

```bash
cp .env.example .env
# edit .env and set OPENROUTER_API_KEY

docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000 (docs at http://localhost:8000/docs)

Uploaded PDFs and rendered page images are written to `./data` (gitignored),
which is mounted into the backend container so they survive restarts.

**Both Dockerfiles bake source in at build time — `backend/app` and
`frontend/src` are NOT live-mounted.** Only `config/`, `prompts/`, and `data/`
are volume-mounted. After editing backend or frontend code you must rebuild:

```bash
docker compose up --build -d backend   # or frontend, or both
```

Editing `config/` or `prompts/` alone only needs a restart, no rebuild:

```bash
docker compose restart backend
```

## Quick start (local dev, no Docker)

Backend (Python 3.11+):

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # if you haven't already, then fill in OPENROUTER_API_KEY
uvicorn app.main:app --reload --port 8000
```

Frontend (Node 20+):

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000/api npm run dev   # or: npm run build / npm run preview
```

There is no test suite and no lint script configured for either side.

## How to use the app

1. **Upload a PDF.** Drag/drop or pick a file. It's validated and rendered
   page-by-page to PNG at the configured DPI (see the DPI toolbar button
   below); you'll see a thumbnail grid of every page on the right.
2. **Pick which pages to analyze** by checking/unchecking them in the page
   grid ("Select all" / "Deselect all" are shortcuts).
3. **Configure the toolbar** (each setting lives behind its own popover
   button):
   - **Model** — pick from `config.yaml`'s list, or add any OpenRouter slug
     on the fly via the "Add model" field in the same popover (no restart
     needed; it's saved to `data/custom_models.json`).
   - **Temp** — sampling temperature (default `0.0`, deterministic).
   - **DPI** — PDF render resolution for this upload (default from
     `config.yaml`'s `pdf.dpi`, currently `150`). Higher DPI = sharper
     detail but bigger images, more tokens, slower requests.
   - **Cutting** — off by default. Turn on to split each page into an
     overlapping grid of crops sent as one request instead of one whole-page
     image (see "Cutting mode" below) — good for pages with lots of small
     objects that a single downscaled image would blur together. Mutually
     exclusive with AI-crop.
   - **AI-crop** — off by default. Turn on to have the model first find the
     page's own tagged regions, then detect only within those regions (see
     "AI-crop mode" below). Mutually exclusive with Cutting.
   - **Max tokens** — response budget per request (default `5000`). If a
     result comes back truncated, raise this first — see "Why responses get
     truncated" below before assuming it's a parsing bug.
4. **Edit the prompt.** System prompt (persona, rules, output JSON schema)
   and user prompt (which object(s) to find, how to recognize them) are two
   separate, independently collapsible text areas. Edit either freely per
   request; **"Save as default"** persists it (writes to
   `data/custom_system_prompt.txt` / `data/custom_user_prompt.txt`, which
   always wins over `config.yaml`'s referenced files on future runs) —
   **"Reset to default"** discards that override and goes back to
   `config.yaml`'s files. Optional reference images can be attached alongside
   the prompt for models that support them.
   - **Multi-Prompting** toggles the user-prompt textarea into 4 separate
     boxes, one per category (cabinets, elevations, countertops, elevation
     callouts) — each fires as its own independent request per page, and the
     4 results are merged into one page result (see "Multi-Prompting mode"
     below).
5. **Click "Send selected (N)"** to analyze the checked pages. Each page gets
   its own status (queued/running/done/error) shown on its thumbnail.
6. **Review results** in the "Page N result" panel:
   - **Annotated image** — the page with detected boxes drawn on top,
     color-coded by category, with a legend and a PNG download button.
     (Replaced by a **Crops** carousel for AI-crop results — see below.)
   - **Raw JSON** — the model's parsed response, for debugging a prompt.
   - A truncation warning and/or per-category error banner (Multi-Prompting)
     show inline when relevant, alongside whatever did parse successfully.
7. **Export** via the Results panel's **"Download obj-count.json"** /
   **"Download obj-location.json"** buttons — aggregated across every
   analyzed page in the session, in the same shapes used elsewhere in this
   repo (see "Output formats" below).
8. **Score against ground truth** (the "Score against ground truth" panel,
   below the Results panel) — upload a ground-truth JSON file (either a
   benchmark project's own `prj*-obj-location.json`, or one of this app's own
   `obj-location.json` exports as a self-check), pick which coordinate space
   it's in, set an IoU threshold, and click **Score** to get
   precision/recall/F1 (overall and per-category) via the bundled
   `location-scorer` library. See "Scoring against ground truth" below —
   picking the wrong coordinate space silently produces a wrong score, so
   read that section before relying on the numbers.
9. **History** (toolbar button) lists every past `POST /analyze` call (model,
   prompt, DPI, timestamp) so you can see what was already tried without
   re-deriving it from memory.

Session state (uploads, results) is **in-memory only** and does not survive a
backend restart — re-upload after restarting the backend. Rendered page PNGs
on disk under `data/sessions/<id>/` do persist, but the app won't know about
that session anymore once its process restarts.

## How it works

### Request flow

1. **Upload** (`POST /api/upload`) validates the PDF (magic bytes + size
   limit) and renders it page-by-page to PNG via PyMuPDF (`fitz`) at the
   chosen DPI (`zoom = dpi / 72.0`), saved under `data/sessions/<session_id>/`.
   A downscaled `display` copy of each page is also precomputed (see "The
   'source image cannot be decoded' pitfall" below).
2. **Analyze** (`POST /api/analyze`) sends each selected page (or, in Cutting
   /AI-crop mode, a set of crops from that page) to the selected model via
   `OpenRouterProvider`, bounded by an `asyncio.Semaphore`
   (`analysis.concurrency_limit`), with retry/backoff on transient errors.
3. **Parse** (`services/parser.py`) turns the model's raw text into
   structured detections, tolerating common near-misses (see "Response
   parsing is deliberately lenient" below).
4. **Export** (`GET /api/sessions/{id}/export/{count,locations}`) aggregates
   every analyzed page's parsed results into the benchmark's two output
   formats.
5. **Score** (`POST /api/sessions/{id}/score`) compares those same parsed
   results against an uploaded ground-truth file via the `location-scorer`
   package.

### Coordinate system

The model's raw response is a JSON object with one key, `"bounding_box"`,
holding detections shaped `{"y_min", "x_min", "y_max", "x_max", "label",
"confidence"}` (flattened directly, no nested box object), normalized as
**0-1000 integers** relative to the image actually sent to the model — not
0-1 fractions, not raw pixels. `(x_min, y_min)` is top-left, `(x_max, y_max)`
bottom-right. This gets rescaled to real pixels (`(value / 1000) * dimension`)
independently in three places kept in sync by hand: the annotated-image
canvas, the results table, and the benchmark export. The model does not count
objects itself — per-category counts are computed downstream by tallying
`label` values.

This shape has changed twice already because a real model's output didn't
match what was asked (a bare top-level array; a nested `bounding_box`/`bbox`
sub-object). The parser stays lenient about both older shapes for exactly
this reason — don't assume the current shape is the last one it'll ever need
to handle.

### Response parsing is deliberately lenient

Real models reliably produce *almost* the requested schema: a misnamed key
(`y.min`, bare `y`), a nested box object instead of flattened fields, or
outright broken JSON syntax partway through an otherwise-fine array.
`parser.parse_model_response` falls back to the `json_repair` library when
plain `json.loads` fails, flattens known nested-box shapes, rewrites known key
aliases, and validates each detection independently so one bad entry doesn't
sink the whole page. It does **not** guess at semantically-wrong values — a
coordinate outside the documented 0-1000 range is dropped, not silently
rescaled on an assumption about what scale was intended.
`parse_error: true` only when *zero* detections survive.

### Why responses get truncated

Two independent causes, both worth checking before assuming a bug:

- **Not enough `max_tokens`** for how many objects are actually on the page —
  raise the Max tokens toolbar setting.
- **Reasoning/"thinking" models spend tokens on invisible reasoning before
  writing any visible output**, drawn from the *same* `max_tokens` budget.
  This app disables reasoning by default for models where that's optional
  (confirmed to eliminate truncation entirely for some models); a few models
  (seen: GPT-5, Gemini 2.5/3.1/3.5 Pro-tier) reject disabling it outright and
  fall back to hiding the reasoning trace instead, which stops it from
  cluttering the response but doesn't stop the token spend — for those,
  raising `max_tokens` a lot (or picking a different model) is the only lever.

A truncated response still shows whatever detections were successfully
parsed from the valid prefix, rather than hiding everything.

### Cutting mode (overlapping-tile detection)

Splits one page into a grid of overlapping crops (default 3x3, configurable
1-6 rows/cols, configurable overlap %) sent as **one combined request**
instead of one whole-page image — useful when a page has many small objects
a single downscaled whole-page image would blur together. Each tile is
downscaled to a vision-endpoint-friendly max edge if needed; the model is
asked to tag each detection with a `tile_id` (e.g. `r0c1`); detections are
then remapped from tile-local back to full-page 0-1000 coordinates using the
recorded crop offsets, and same-label detections seen from two overlapping
tiles are merged (by IoU) into one so an object isn't double-counted. The
result is an ordinary parsed result — no downstream code needs to know
Cutting was involved. `max_tokens` is **not** auto-scaled by tile count (the
same budget now covers every tile's detections combined) — the UI shows an
inline nudge if the current budget looks too low for the chosen grid size,
but never blocks sending.

### AI-crop mode (two-pass region-then-detect)

An alternative to Cutting, mutually exclusive with it. Instead of a uniform
grid, it asks the model to first find the page's own tagged regions, then
detects only within those:

- **Pass 1** sends the whole page with an editable "Crop prompt" asking the
  model to bound every tagged region/cell and extract its label text.
- Each surviving region is cropped (with small clip-avoidance padding) into
  its own image.
- **Pass 2** batches every crop into one request (reusing Cutting's
  multi-image request mechanism) using your *real* system/user prompt, with
  an appended instruction asking for one JSON array entry per crop, in order,
  each with its own crop-local detections (0-1000 relative to that crop only —
  **not** remapped back to full-page coordinates, since an earlier version's
  full-page remap step proved unreliable and was removed).
- Because detections stay crop-local, results render in a **Crops carousel**
  (prev/next through each crop with its own detections drawn on it) instead
  of one annotated full-page image, and `obj-location.json`'s export has no
  entries for AI-crop pages (a known, deliberate consequence — `obj-count.json`
  still gets real numbers, tallied from every crop's detections).
- Pass 2's response length is checked against the number of crops sent — a
  mismatch (or a crop whose echoed id doesn't match what was expected) marks
  that crop `verified: false` in the UI rather than silently trusting a
  misaligned response; detections are still kept where Pass 2 did return
  something, just flagged.
- If Pass 1 finds zero regions, the page finishes as a successful, empty
  result with an explanatory message — Pass 2 never runs.

### Multi-Prompting mode

Fires one independent request per category (cabinets, elevations,
countertops, elevation callouts) instead of one request for the whole page,
using each category's own prompt text. Results are merged into one page
result per page (backend and frontend each do this merge independently — the
backend's merge is authoritative for storage/exports, the frontend's lets the
UI render immediately without an extra round-trip). One category failing
doesn't fail the others — a per-category error is recorded and shown inline
while the page's overall status stays "done" as long as at least one category
succeeded. Composes with both Cutting and AI-crop (each category runs its own
independent pipeline against the same page).

### The "source image cannot be decoded" pitfall

This exact error string has two unrelated causes:

1. Some vision models reject an image above a certain size outright — the
   backend downscales the copy sent to the model for this (rescaling math for
   detections is unaffected, since normalization is relative to whatever
   image the model actually received).
2. The **browser's own** image decoder can reject a very large original PNG
   with the identical error message while rendering the preview canvas — this
   is why the backend also precomputes and serves a separate downscaled
   `display` copy for the browser, while keeping the true full-resolution
   dimensions for all pixel-space math.

If this error resurfaces, check whether the analyze call actually returned a
result first (cause 2 — frontend/canvas) before assuming it's an API/model
problem (cause 1).

### Scoring against ground truth

`POST /api/sessions/{id}/score` bridges this app's own parsed predictions and
an uploaded ground-truth file through the bundled
[`location-scorer`](../packages/location-scorer) package (see
`backend/app/services/scoring.py`) — a small, dependency-free, IoU-based
localization scorer (greedy one-to-one matching per page/category, IoU
threshold with no built-in default, micro-averaged precision/recall/F1).

The one thing this bridge cannot infer on its own is **which coordinate space
your ground-truth file's boxes are in** — that's the `ground_truth_space`
choice in the Score panel (and `ScoreRequest.ground_truth_space` in the API):

- **`pdf_points`** (default) — a benchmark project's own
  `prj*-obj-location.json`. Its boxes are PDF point space that already
  reflects the page's own rotation, just not yet scaled to this session's
  render DPI — the backend scales them by `dpi / 72` before comparing. This
  was verified empirically by overlaying scaled ground-truth boxes directly
  onto this app's actual rendered page and confirming pixel-level alignment,
  down to individual small symbols — no page-rotation-matrix step is needed
  on top of the DPI scale.
- **`render_pixels`** — one of this app's own `GET .../export/locations`
  exports, re-uploaded as a self-check. It's already in this session's render
  pixel space, so it's used as-is with no scaling.

Picking the wrong one silently over- or under-scales every box and produces a
misleadingly low (or occasionally accidentally-plausible) score — there's no
reliable way to detect which space an arbitrary file is in from its contents
alone, so this stays an explicit, visible choice rather than a guess.

### Output formats

`obj-count.json`:

```json
{ "cabinets": 10, "countertops": 15, "elevations": 8, "elevation_callouts": 2 }
```

`obj-location.json`:

```json
{
  "project_id": "prj0001",
  "objects": [
    {
      "id": "cab-001",
      "category": "cabinet",
      "page": 2,
      "bbox": { "x": 112.5, "y": 430.0, "width": 96.0, "height": 48.0 }
    }
  ]
}
```

`project_id` is derived from the uploaded filename (its stem, e.g.
`prj0001.pdf` → `prj0001`). Note this app's internal schema
(`ParsedResult`/`DetectedObject`, 0-1000-normalized) is *not* this on-disk
schema — conversion only happens at the export/score boundary.

## Configuring without touching code

- **`config/config.yaml`** — the model dropdown's contents, default
  model/temperature/max_tokens, PDF render DPI, upload size limit, the
  per-page analysis concurrency limit, and which prompt files are the
  defaults (`system_prompt_file`, `user_prompt_file`,
  `ai_crop_cell_prompt_file`). Add or remove a model by adding/removing an
  entry under `models:`; nothing else needs to change.
- **`prompts/*.txt`** — prompt files referenced by `config.yaml`. Several
  older/unused pairs are kept around for reference (different schema
  generations) — check which pair `config.yaml` and any saved
  `data/custom_*_prompt.txt` override actually point to before editing one
  and wondering why nothing changed; a saved custom prompt (via "Save as
  default" in the UI) always wins over whatever `config.yaml` references.
- **`.env`** — secrets only (`OPENROUTER_API_KEY`, plus optional
  `OPENROUTER_SITE_URL`/`OPENROUTER_APP_NAME` attribution headers). Never put
  this in `config.yaml` or commit it.

Both `config/` and `prompts/` are mounted read-only into the backend
container by `docker-compose.yml`, so editing them on the host and running
`docker compose restart backend` is enough — no image rebuild required.

## Adding another LLM provider

`backend/app/services/llm_client.py` defines an `LLMProvider` abstract class
with `analyze_image(...)`/`analyze_tiles(...)`. `OpenRouterProvider` is the
only implementation today, and it already covers most vendors (Anthropic,
OpenAI, Google, Meta, Qwen, ...) since OpenRouter proxies them behind one
OpenAI-compatible API — adding a new *model* is just a `config.yaml` entry
with a valid slug from [openrouter.ai/models](https://openrouter.ai/models),
no code change needed. To add a provider that talks to a vendor's API
directly instead of going through OpenRouter:

1. Implement `LLMProvider` for it (e.g. `OpenAIProvider`).
2. Register a factory in `_PROVIDER_FACTORIES` in the same file.
3. Add model entries to `config/config.yaml` with `provider: <your-key>`.

No other backend or frontend code needs to change — the analyze route
resolves the provider by name at request time.

## Project structure

```
casework-analyzer/
├── docker-compose.yml
├── .env.example
├── config/
│   └── config.yaml            # models, defaults, DPI, concurrency — no code changes needed
├── prompts/
│   ├── detector_system.txt    # current default system prompt (output schema, rules)
│   ├── cabinet_user.txt       # current default user prompt (cabinet-only)
│   ├── elevations_unfiltered.txt  # AI-crop Pass 1's fixed cell-detection prompt
│   └── ...                    # older/unused prompt pairs kept for reference
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt       # includes an editable install of ../../packages/location-scorer
│   └── app/
│       ├── main.py            # FastAPI app, CORS, router wiring, error handler
│       ├── api/                # upload, analyze, export, meta, history, scoring (routes)
│       ├── core/config.py      # env vars + config.yaml merged into one Settings object
│       ├── services/
│       │   ├── pdf_processor.py   # PDF -> per-page PNG rendering
│       │   ├── llm_client.py      # OpenRouterProvider, single/tiled request paths
│       │   ├── parser.py          # lenient model-response parsing + export builders
│       │   ├── tiling.py          # Cutting mode's grid/crop/remap/merge logic
│       │   ├── ai_crop.py         # AI-crop mode's two-pass pipeline
│       │   ├── crop_utils.py      # shared crop/resize helper (Cutting + AI-crop)
│       │   ├── scoring.py         # bridge to location-scorer for benchmark scoring
│       │   ├── session_store.py   # in-memory session/result state
│       │   └── history_store.py   # one JSON file per past analyze run
│       └── models/schemas.py  # pydantic request/response models
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── App.jsx             # top-level state + layout, threaded down via props
│       ├── api.js              # fetch wrapper for the backend
│       ├── mergeCategoryResults.js   # Multi-Prompting's client-side result merge
│       ├── multiPromptCategories.js  # the 4 Multi-Prompting category definitions
│       └── components/
│           ├── Toolbar.jsx           # model/temp/DPI/Cutting/AI-crop/max-tokens popovers
│           ├── PromptEditor.jsx      # system/user prompt editing, Multi-Prompting toggle
│           ├── PageGrid.jsx          # page thumbnails + selection
│           ├── ResultsSummary.jsx    # aggregate count tiles + export buttons
│           ├── PageResultViewer.jsx  # per-page Annotated image/Crops/Raw JSON views
│           ├── BBoxCanvas.jsx        # draws detections on a full page image
│           ├── CropsCarousel.jsx     # draws detections on AI-crop's individual crops
│           ├── ScorePanel.jsx        # ground-truth upload + IoU scoring UI
│           ├── ImagePreviewModal.jsx
│           └── HistoryModal.jsx
└── data/                        # uploads + rendered page images (gitignored, docker volume)
```

## Error handling notes

- Oversized uploads (over `pdf.max_file_size_mb` in `config.yaml`) return
  `413`; non-PDF files return `400` before any processing happens.
- Malformed/unopenable PDFs return `400` with the underlying PyMuPDF error.
- OpenRouter API failures (auth, rate limit, overloaded, connection) are
  retried with exponential backoff (`analysis.max_retries` /
  `analysis.retry_base_delay_seconds` in `config.yaml`); if retries are
  exhausted, that page's result is marked `status: "error"` with a message,
  and other pages in the same batch are unaffected.
- Any other uncaught backend error returns a generic `500` and is logged
  server-side rather than leaking internals to the client.

## Development

Recent additions worth knowing about before touching related code:

- **Benchmark scoring via `location-scorer`.** A teammate's standalone,
  dependency-free IoU-scoring package (`../packages/location-scorer`) is now
  wired in end-to-end: `backend/requirements.txt` installs it as an editable
  path dependency (`-e ../../packages/location-scorer`, since no
  `location-scorer-v*` git tag has been pushed yet — switch to the package's
  documented `pip install git+...@<tag>` form once one exists), the Docker
  build context was widened from `./backend` to the monorepo root so the
  Dockerfile can reach that sibling package (see the root `.dockerignore` for
  what's excluded from that wider context), and a new
  `POST /api/sessions/{id}/score` endpoint (`backend/app/services/scoring.py`
  + `api/scoring.py`) bridges this app's own predictions and an uploaded
  ground-truth file into the shapes `location_scorer.score()` expects. The
  frontend's `ScorePanel.jsx` is a new, self-contained panel — file upload,
  coordinate-space choice, IoU threshold, and a results table — that needs no
  server-side storage of the ground-truth file (parsed and sent client-side).
- **Cutting mode's toolbar toggle** (the "Cutting Off"/"Cutting On" button in
  `Toolbar.jsx`, off by default) is the entry point for the overlapping-tile
  detection pipeline described above — when adding new per-page UI, check
  whether it needs to account for Cutting (and AI-crop) being active, since
  both change what a "page result" contains (tile-merged detections for
  Cutting; crop-local detections with no full-page `objects` for AI-crop).

There is no test suite and no lint script configured for either the backend
or frontend — verification for changes in this repo happens by running the
app against `../dataset/project-0001/prj0001.pdf.pdf` (page 1 only, per the
root `CLAUDE.md`) and checking results in the browser.
