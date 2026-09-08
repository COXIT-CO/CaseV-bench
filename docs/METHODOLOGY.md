# CaseV-Bench: Methodology

*OLD VERSION 10.08.26*

*A benchmark for evaluating vision-language models on architectural millwork
drawing detection.*

## Abstract

CaseV-Bench measures how accurately, cheaply, and quickly different
vision-language models (VLMs) can locate five categories of objects —
elevations, floor plans, cabinets, countertops, and reference callouts — on
raster renders of architectural millwork drawing sets. The benchmark
compares three detection strategies against a shared, hand-labeled
ground-truth dataset of real construction-document PDFs, using IoU-based
bounding-box matching and provider-reported cost accounting. **Single-pass
("One-Stage") detection is the method under active development and is
documented here in full detail** — its prompt specification, coordinate
pipeline, rendering pipeline, and the empirical process by which its prompt
was refined against real ground truth. A human-in-the-loop two-pass method
and a grid-cell indirection method are also implemented and were included in
an earlier completed comparison; they are documented at the level of detail
needed to understand what they measure, without One-Stage's full
prompt-engineering history. This document describes the dataset, the object
taxonomy, all three detection methods, model configuration and per-model
input limits, and the evaluation protocol. Measured results from the first
completed comparison are reported separately in `COMPARISON.md`; §7 states
precisely which part of the setup described here that comparison covers,
and which parts of the current dataset and prompts postdate it.

## 1. Introduction

Architectural millwork drawing sets — cabinet/casework elevations, floor
plans, and reflected ceiling plans (RCPs) — are dense, low-redundancy
technical documents. Automating even a first-pass inventory of the casework
objects on such a sheet (which elevations exist, which cabinets and
countertops they contain, which callouts point where) is a plausible
assisted-review task for a VLM, but the objects are small relative to the
sheet, densely packed, and drawn with domain-specific conventions (solid vs.
dashed lines, front-view-only annotation rules, section vs. elevation
distinctions) that a general-purpose model has no special training for.

CaseV-Bench was built to answer three practical questions before committing
to a production pipeline: (1) can a VLM localize these objects accurately
enough to be useful at all, (2) does *how* the detection task is decomposed
(one pass vs. two, raw coordinates vs. a grid indirection) matter more than
*which* model is used, and (3) what does an accurate configuration cost per
document in dollars and latency. The tool itself is a small internal web
application (FastAPI backend, vanilla-JS frontend) that renders uploaded PDF
sheets, sends them to one or more models via OpenRouter, overlays and scores
the returned boxes against pasted ground truth, and stores every run's exact
inputs and outputs for later inspection.

Current project effort is concentrated on the single-pass method (§3.1):
it is the cheapest and fastest of the three by a wide margin (§7), and the
open question is whether careful prompt engineering and per-model resolution
tuning alone can close its accuracy gap against the more expensive two-pass
method, without changing its one-request-per-page architecture. That is the
work this document's §3.1 and §4.3 describe in the most detail.

## 2. Dataset

### 2.1 Source documents

The dataset consists of five real millwork/casework drawing sets, submitted
as multi-page PDFs and stored under `drafts/expected/project-000{1..5}/`:

| Project | Pages | Sheet size (native PDF points) | Approx. physical size |
|---|---:|---|---|
| `prj0001` | 4 | 2160 × 3024 pt | ANSI E, ~30 × 42 in |
| `prj0002` | 2 | 2160 × 3024 pt | ANSI E, ~30 × 42 in |
| `prj0003` | 9 | 1224 × 792 pt | ANSI B, ~17 × 11 in |
| `prj0004` | 28 | 3024 × 2160 pt | ANSI E, ~42 × 30 in |
| `prj0005` | 23 | 3024 × 2160 pt | ANSI E, ~42 × 30 in |

`prj0003`'s smaller page size and the size difference between it and the
other four projects is a deliberate part of the dataset, not an artifact —
it is the only document where a uniform grid or a fixed render DPI is not
disadvantaged by physical sheet size the way it is on the ANSI-E sheets.

`prj0004` and `prj0005` are recent, substantially larger additions (28 and
23 pages respectively, against 2–9 for the original three) and were the
documents used to find and fix a page-rendering out-of-memory failure in the
single-pass flow (§6.3). **They currently have no ground-truth annotations
for `cabinet` or `countertop` at all** — their existing ground-truth files
cover `elevation`, `floor plan`, and `callout` only. This is a confirmed
state of the dataset, not a data-quality question: annotation of the
per-cabinet/per-countertop detail level for these two documents has simply
not been done yet. Any evaluation of `cabinet`/`countertop` detection is
therefore still scoped to `prj0001`–`prj0003` only; `prj0004`/`prj0005` can
be used today only to evaluate `elevation`, `floor plan`, and `callout`
detection.

All rendering is done at request time by the application itself
(`pdf2image`/poppler), never pre-rendered — every run controls its own DPI
and, optionally, a post-render maximum pixel dimension, so the same source
PDF can be re-rendered at whatever resolution a given method or model needs
(§4.3).

### 2.2 Object taxonomy

Ground truth and every detection method target the same five object types,
formalized from a written annotation-rules reference document (internally
`drafts/new_annotation/Annotation rules.pdf`, authored by the project's
domain reviewers) into an explicit prompt specification:

| Label | Definition (condensed) |
|---|---|
| `elevation` | The whole framed orthographic view of a wall/casework run standing on a floor line — front, side, back, or a detail's own top view. Judged by geometry (frame + floor line + a casework run), not by caption wording: many elevations are never captioned "elevation". Excludes a section (a wide cross-sectional building/multi-floor cutaway) and a top-down room plan. |
| `floor plan` | A top-down view of a *room* or overall space — walls, room boundaries, furniture layout — or the same room's reflected ceiling plan (RCP). A top-down view of one object/assembly is still `elevation`, not `floor plan`; the deciding axis is scope (room vs. single assembly), not view direction. |
| `cabinet` | One individual built-in or freestanding storage unit, bounded by its own solid carcass lines, annotated only where it appears in **front view** (never side/profile, top, or back). Open/floating shelving with no enclosing carcass is excluded; a cabinet visible only inside a section is excluded (it is annotated on its own separate elevation instead). |
| `countertop` | A thin horizontal work-surface band, usually above a base-cabinet run but sometimes carried on its own legs/brackets, optionally with an integrated backsplash counted as the same object. Annotated only in **front view**, traced to exactly where its own drawn line stops. |
| `callout` | A small reference symbol on a floor plan/RCP with at least one solid, filled arrowhead pointing at a wall, referencing an elevation drawn elsewhere in the set. Two valid graphic shapes (a container with attached arrow(s), or arrowheads fused into one diamond/star with no container). Never appears inside an elevation frame; a bare identifier bubble with no filled arrowhead is not a callout. |

A sixth category, `section` (a wide cross-sectional cutaway), is explicitly
**excluded from annotation** per the reference document and exists in the
taxonomy only as a negative example used to keep `elevation` and `cabinet`
from over-triggering on it. §3.1.1 gives the full, prompt-level definition
of each type, including the disambiguation rules the model is given for
every pair of types that is commonly confused.

This is a revision of an earlier, four-type taxonomy
(`elevation`, `cabinet`, `countertop`, `elevation_callout`, no `floor plan`)
used for the dataset's first version and for the comparison reported in
`COMPARISON.md`. The revision renamed `elevation_callout` → `callout`,
introduced `floor plan` (partly reclassified out of what had been counted as
`elevation`, partly newly annotated), and left `cabinet`/`countertop`
untouched. `floor plan` is written with a literal space, and that spelling is
authoritative end-to-end — the prompt text, the backend's label whitelist,
and every ground-truth `category` string agree on it exactly, so scoring can
match it as a plain string without a normalization step. As of this writing
the five-type taxonomy has been rolled out to the **One-Stage prompt only**
(§7) — the Two-Stage and Grid prompts still target the original four types.

Current per-document object counts, from the ground-truth files as they
stand today:

| Project | `elevation` | `floor plan` | `cabinet` | `countertop` | `callout` | Total |
|---|---:|---:|---:|---:|---:|---:|
| `prj0001` | 26 | 3 | 31 | 7 | 12 | 79 |
| `prj0002` | 7 | 3 | 27 | 7 | 4 | 48 |
| `prj0003` | 7 | 7 | 15 | 3 | 5 | 37 |
| `prj0004` | 43 | 96 | — | — | 239 | 378 |
| `prj0005` | 238 | 3 | — | — | 13 | 254 |

### 2.3 Ground-truth format and coordinate convention

Each project ships two files: a per-type count summary
(`prj{N}-obj-count.json`) and the actual located objects
(`prj{N}-obj-location.json`), the latter shaped as
`{"project_id": ..., "objects": [{"id", "category", "page", "bbox"}, ...]}`.
A `bbox` is given as `{"x", "y", "width", "height"}` (top-left origin);
absolute-corner arrays `[x_min, y_min, x_max, y_max]` are also accepted by
the scoring pipeline for compatibility.

**Ground-truth boxes are measured in PDF points — the page's own coordinate
space at 72 points per inch — not in pixels of whatever DPI a given run
happens to render at.** This is the single most consequential convention in
the dataset: normalizing a ground-truth box against a render's *pixel* size
instead of the PDF's *native point* size silently shrinks every box by a
factor of `(dpi / 72)`, which is large enough (rendering commonly happens at
180–500 DPI) to drop true-positive counts to effectively zero while
producing no error — the boxes are simply drawn wildly undersized. Every
scoring path in the application derives each page's native point dimensions
independently of the DPI used to render it for a particular model call, and
normalizes ground truth against that, never against render pixels.

Detected-object coordinates, by contrast, are requested from models on a
**0–1000 integer scale** relative to the *image actually sent* (`[0,0]` =
top-left, `[1000,1000]` = bottom-right) for the One-Stage and Two-Stage
flows, and as **named grid-cell labels** for the Grid flow (§3.3), converted
to a 0–1 fraction in code. An earlier version of the One-Stage prompt used a
plain 0.0–1.0 fraction instead of 0–1000; this was reverted after an
empirical regression — 0–1000 integers measurably outperformed fractions on
the same task, for reasons not further diagnosed. 0–1000 is the current
default for every new prompt in this project.

## 3. Detection methods

All three methods share the same downstream pipeline once a raw model
response is parsed: labels are filtered against a fixed whitelist, boxes are
resolved from whichever field shape the model actually returned, and any
model-reported count/summary block is discarded — object counts are always
recomputed in code from the final object list, never taken from the model.

### 3.1 One-Stage detection (primary method)

The direct approach: the model is asked, in a single request, to find every
object of every type on one sheet image and report each one's label and
box. This is the cheapest and fastest method per document (fewest requests,
one full-page image per page) and is currently the method receiving active
prompt-engineering and configuration work, described in full below.

#### 3.1.1 Prompt specification

The live prompt (`../drafts/new_annotation/system_prompt_single_stage_v2.txt` +
`user_prompt_single_stage_v2.txt`) frames the model as "a senior
construction-documents specialist" reviewing "ONE single sheet image per
request," and is structured as a fixed sequence of sections, in this order:

1. **WHAT TO DETECT** — the five object definitions from §2.2, written out
   in full prose detail (each with its own worked visual description, not
   just a category name) plus an explicit ignore-list: finish schedules,
   door/hardware schedules, partition-type details, general notes, title
   blocks, north arrows, dimension strings, un-cabineted room tags, site
   maps, sections, and any non-built-in furniture/plumbing/equipment
   (including freestanding lockers or benches, even ones with a door-swing
   diagonal).
2. **FRONT VIEW vs SIDE VIEW** — a standalone decision rule that applies
   only to `cabinet` and `countertop`: front view shows the primary
   face/doors/drawers/full width and is annotated; side/profile view shows
   depth or an end panel and is never annotated, even for an object that
   also appears correctly in front view elsewhere on the same sheet.
3. **SKIP AMBIGUOUS OR NON-STANDARD CASES** — an explicit
   low-confidence-abstention policy: a candidate that does not cleanly match
   any of the five types, or (for `elevation` specifically) lacks both the
   expected framing *and* any reference marker/scale, is left unannotated
   rather than force-fit. The same applies to a cluster of small detail
   frames packed together whose individual boundaries cannot be confidently
   separated — the whole group is skipped rather than guessing where to
   split it.
4. **WORK IN THIS ORDER** — a fixed four-step procedure the model is asked
   to follow: (1) one full left-to-right, top-to-bottom scan of the whole
   image *before* reporting anything, specifically to avoid a shallow scan
   that misses objects sitting elsewhere on a large sheet; (2) classify each
   candidate using the checklist below; (3) read each object's four edges
   directly off the image and convert to the 0–1000 scale; (4) re-check the
   full list against the FINAL CHECK section and fix anything that fails.
5. **DISTINGUISHING CHECKLIST** — one bullet per commonly-confused pair or
   failure mode, each phrased as a concrete rule rather than a restatement
   of the definition: `elevation` vs. `cabinet` (an elevation almost always
   contains more than one cabinet — the same rectangle for both is a sign
   one is wrong), `elevation` vs. `section` (never annotate a section itself
   or a cabinet visible only inside one — find that cabinet's own separate
   elevation instead), `elevation` vs. `floor plan` (room/space scope vs.
   single-object/assembly scope, regardless of view direction), `cabinet`
   vs. `countertop` (door/drawer marks vs. a thin band with none), one
   cabinet vs. two (only a *solid* vertical line divides cabinets — dashed
   diagonals are door swings, dashed horizontals are shelf lines, neither is
   a divider), a long run of similar bays (read each bay's own edges
   directly; do not extrapolate even spacing from the first one or two bays
   — a documented, specific failure mode, see §3.1.3), front view vs. side
   view (checked *before* boxing anything), open shelving vs. cabinet (a
   shelf-diamond pattern inside an otherwise enclosed, divider-bounded run
   is still a cabinet; floating shelving with no enclosing carcass is not),
   callout vs. everything else (only on a floor plan/RCP, only with a
   visible solid filled arrowhead), the callout symbol vs. its own nearby
   reference text (box the arrowhead shape itself, not just the label text
   sitting beside it), and countertop full span (trace the drawn line to
   where it actually stops in each direction — stopping short and
   over-tracing past the line's real end are treated as equally common
   mistakes, see §3.1.3).
6. **COORDINATE FORMAT** — four independently-named fields (`left`, `top`,
   `right`, `bottom`) on the 0–1000 scale described in §2.3, with an
   explicit **field-order warning**: the model is told to read each edge
   directly off the object and assign it to its named field, never to
   assemble a coordinate array first and then split it into named fields —
   this exists specifically to prevent format habits from other
   coordinate-array conventions (e.g. `[y_min, x_min, y_max, x_max]`) from
   silently swapping which value lands in which named field.
7. **FINAL CHECK** — a nine-item pre-submission checklist mirroring the
   distinguishing checklist's failure modes one more time in yes/no form,
   ending on a purely structural check: every `left`/`top`/`right`/`bottom`
   is a whole number 0–1000 with `left < right` and `top < bottom`.
8. **Output contract** — return only JSON, no markdown or commentary; the
   top-level object has exactly one key, `"objects"`, a list of
   `{"label", "left", "top", "right", "bottom", "image_index"}` entries; no
   `"summary"` or count field is requested, and any that appears anyway is
   discarded downstream, never read. `image_index` is always `0` for the
   current one-image-per-request usage (see §3.1.4). The five valid label
   strings are stated verbatim in the closing rules, including the literal
   space in `"floor plan"`.

The user-turn prompt is a short, separate instruction (11 lines) that
restates the coordinate convention in one sentence and hands over the image;
the bulk of the specification lives in the system prompt above.

#### 3.1.2 Response parsing and validation

The raw model response is stripped of any Markdown code-fence wrapping and
parsed as JSON; a malformed response is retried through `json_repair` before
being treated as a hard failure. Once parsed:

- **Box resolution prefers the four independently-named fields** (`left`,
  `top`, `right`, `bottom`) over any self-assembled `box` array the model
  might also emit, and only accepts the named fields when they are
  internally consistent (`right > left`, `bottom > top`) — the entire point
  of requesting named fields instead of an array is to not have to trust
  the model's own assembly step. A `box` array is used only as a fallback
  for models/prompts that never produce the named fields.
- **Labels are filtered against a fixed whitelist** — exactly the five
  taxonomy strings from §2.2 (including the literal space in `"floor
  plan"`) — any other label string is dropped rather than passed through or
  aliased. An earlier draft accepted both `"elevation_callout"` and
  `"callout"` "to be safe"; this alias was deliberately removed — the
  intent is that the label surface the app accepts is exactly and only what
  is currently asked for, not a superset kept for legacy tolerance.
- **A three-way coordinate-scale safety net** auto-detects, per object,
  whether the returned box is actually a 0–1 fraction (`max(box) ≤ 1.0` →
  multiply by 1000), already 0–1000 (`max(box) ≤ 1000` → used as-is), or a
  raw pixel coordinate (`max(box) > 1000` → rescaled using the page's real
  rendered pixel dimensions as the denominator). This exists because a
  model occasionally ignores the requested scale outright — most notably
  the specific, unresolved `claude-sonnet-5` failure mode in §8 — and is
  strictly a safety net: it does not change what scale is *requested*, only
  how a response that disobeys the request is still salvaged rather than
  silently mis-scored.
- **Any model-reported `"summary"` or count field is parsed but never
  read.** Object counts shown in the UI and used for pass/fail comparisons
  against a pasted expected-summary are always recomputed in code from the
  final, validated `"objects"` list.

#### 3.1.3 Iterative prompt refinement (process)

The v2 prompt in §3.1.1 is the product of several rounds of empirical
refinement, each one mined directly from real ground truth rather than from
prose reasoning about the reference PDF alone — rendering the actual
ground-truth boxes over the actual PDF pages and zooming into individual
symbols surfaced several details the reference document's prose never
stated outright, even though its own example images contained them. Fixes
applied through this process, in the order they were found:

- **RCP counts as `floor plan`, not its own thing.** Confirmed by finding a
  ground-truth `floor plan` box drawn around a reflected-ceiling-plan panel
  (ceiling grid + light fixtures) that the prompt's `floor plan` bullet
  never explicitly named as in-scope.
- **`callout` has two valid graphic shapes, not one.** A second real
  ground-truth callout turned out to be a solid fused diamond/star made of
  2–4 filled triangular arrowheads with no separate round/pentagon
  container at all — a shape the prompt's original "round or five-sided
  container with an attached arrow" wording did not cover.
- **Countertop over-extension**, traced to a specific document's reported
  symptom: the original wording only ever warned against stopping *short*
  of a countertop's real extent, creating an asymmetric bias toward
  over-tracing past the drawn line's actual end. Reworded to warn against
  both directions equally.
- **Edge-interpolation drift across repeated bays**, diagnosed as the likely
  cause of one document's broad, sheet-wide under-performance: sheets with
  6+ near-identical cabinet bays in one run invite extrapolating the rest of
  the run's spacing from the first bay or two, an assumption that compounds
  error the further it is carried. The checklist and final-check sections
  now explicitly instruct reading each bay's own edges directly instead.
- **`section` exclusion, added from a later, substantially expanded version
  of the reference PDF** (grown from roughly 2 to 15 pages between checks):
  a wide cross-sectional cutaway is not an `elevation`, and a cabinet
  visible only inside one is not annotated there — it is annotated on its
  own separate elevation view instead, which the source documents were
  confirmed to reliably also contain.
- **Countertop definition broadened** to allow a countertop carried on legs
  or brackets with no cabinet run underneath at all, matching the reference
  document's definition more exactly than the original, cabinet-run-only
  wording.
- **Callout vs. title bubble**, an explicit named exception from the
  reference document: a numbered circle sitting beside a view's own caption
  (labeling that view) is not a callout, even though it superficially
  resembles one.

A separate, explicitly time-boxed attempt targeted `claude-sonnet-5`'s
chronic near-zero accuracy (§8): two rounds of prompt-wording changes
(reframing the coordinate section fraction-first with a worked example, and
removing hallucination-priming language) were tried and did not resolve it.
That problem is currently understood to be structural — a coordinate-space
reporting issue, not a prompt-wording one — and is tracked separately in §8
rather than folded into further prompt iteration.

One general lesson from this process, applied consistently across every
round: **naming the wrong answer in order to warn against it is avoided.**
An early draft of the coordinate section repeated "do not use pixels, do
not use a 0.0–1.0 fraction" three times in slightly different wording; this
was cut down to one clean positive instruction per checkpoint, since
naming the wrong format to warn against it risks priming the model toward
exactly that format (negation priming), on top of being needless
verbosity. The same principle was applied when a sentence describing sheets
as containing "a repetitive grid of similar-looking framed drawings" was
found to correlate with a model inventing repeated, pixel-identical frames
that were not actually on the page — replaced with a neutral instruction
not to stop scanning early, without asserting what the sheet typically
contains.

#### 3.1.4 Rendering and execution pipeline

Each page is rendered from the source PDF at a configurable DPI, one page at
a time (§6.3), and optionally downscaled afterward so its longest side does
not exceed a configurable maximum pixel dimension (`max_dim`) before being
PNG-encoded and base64-embedded in the request — see §4.3 for how DPI and
`max_dim` interact with each model's own internal downsampling. `max_dim` is
**off by default** for One-Stage (an explicit opt-in, not a forced value):
leaving it unset renders and sends the page at the configured DPI with no
further resizing, which was the flow's entire behavior before `max_dim` was
introduced.

Independent of DPI/`max_dim`, three execution axes control how requests are
grouped and scheduled: which configured models run one after another vs.
concurrently; whether an uploaded file's pages are combined into one request
or split one-request-per-file; and, within a file, whether its pages are
sent together or split one-request-per-page. **The v2 prompt in §3.1.1 is
written for, and current prompt-tuning work assumes, exactly one image per
request** ("You are looking at ONE single sheet image per request" is
stated in the prompt's first line) — i.e. the page-splitting axis set to
one-page-per-request. The endpoint's code path does still support combining
several pages into a single multi-image request (the alternate setting on
that same axis), but doing so would contradict the prompt's current
single-image framing and is not how the method is currently being run or
tuned; treat that combined-request mode as a distinct, unevaluated
configuration rather than an interchangeable execution detail.

The model's output token budget is not fixed: it scales with **how many
images are in the request**, not with page count in the overall batch,
under the reasoning that output length grows with object density on a page,
not with how many pages happen to be grouped together — `min(32000,
base_tokens + n × 2000)`, where `base_tokens` is 12 000 for
reasoning-capable models and 8 000 otherwise, and `n` is the image count in
that specific request (almost always 1 under the current one-page-per-
request usage). See §4.2 for the reasoning-effort policy this budget is
paired with.

### 3.2 Two-Stage detection (human-in-the-loop)

A slower, higher-precision alternative for a single model at a time, split
into two model passes with a human review checkpoint between them: **Stage
1** scans every page once for `elevation` frames and `elevation_callout`
symbols only (this flow still targets the original four-type taxonomy, not
the current five-type one — see §2.2/§7); a person then unchecks any
wrongly-detected elevation, and only approved elevations continue. **Stage
2** re-renders each approved page at its own DPI, crops out every approved
elevation, and asks the model for `cabinet` and `countertop` within each
crop independently, merging the results back onto the full page. Every page
or crop is still sent as its own independent request in both stages — the
tab's execution settings control only whether those requests run
sequentially or in parallel, not whether they are batched together.

Because a crop contains far less of the sheet than a full page, this is the
main mechanism by which Two-Stage raises *effective* resolution under a
fixed per-model DPI ceiling (§4.3) without simply rendering at a higher DPI,
which helps only up to the point a model downsamples the image internally.
It was the strongest method in the completed comparison (§7), but is not
currently the focus of active development — see §3.1's introduction.

### 3.3 Grid-cell detection

An indirection strategy motivated by the observation that asking a model to
*compute a numeric box* is a harder and more error-prone task for some
models than asking it to *name a cell in a labeled reference grid*. A grid
(configurable rows × columns, line color/opacity/thickness, and a label
scheme — spreadsheet-style letters, `R#C#`, or letters with alternating-row
shading) is drawn over the rendered page (always first downsized to a
maximum pixel dimension, §4.3) before the request is sent. The model is
asked only to name which grid cell(s) each object occupies; the bounding box
is then **derived entirely in code** from the named cell(s) — the model's
own numeric estimate, if any, is never used.

Three levels of box precision are supported, all still cell-label-based
rather than free coordinates: `cell` (the union of every named cell's own
outer edges — the coarsest, original behavior), `cell_anchor` (the model
additionally names a start/end position within the first/last named cell
from a small fixed vocabulary — `topleft`, `center`, `bottomright`, etc. —
still a category choice rather than an estimate), and `cell_fraction` (the
model reports a raw `[fx, fy]` 0–1 point within the named cell — a
continuous estimate, but scoped to one cell's extent so an estimation error
stays small in absolute terms). An object spanning multiple cells is
reported as one detection listing every occupied cell, not one detection
per cell. Grid still targets the original four-type taxonomy (§2.2/§7).

## 4. Models and configuration

### 4.1 Models evaluated

Four models were evaluated in the completed comparison, all accessed through
OpenRouter: `google/gemini-3.1-pro-preview`, `google/gemini-3.6-flash`,
`openai/gpt-5.6-terra-pro`, and `anthropic/claude-sonnet-5`. The application
itself is not limited to these four — any OpenRouter-served vision model can
be configured per run — but this set spans both a flagship and a cost-tier
model from one provider plus a third and fourth provider's flagship, which
was the intended coverage for the first comparison.

### 4.2 Reasoning-budget equalization

Several of these models support (or require) an internal reasoning/thinking
pass before producing output, and leaving that uncontrolled would confound
"model capability" with "how much the provider's default reasoning budget
happens to allocate to this task." Every model call classifies the model
name by substring match and applies a uniform policy:

| Reasoning class | Matched by (substring) | Policy applied |
|---|---|---|
| `mandatory` | `gemini-3`, `gemini-2.5` | `reasoning: {"effort": "low"}` forced (cannot be fully disabled for this family) |
| `optional` | `claude-sonnet`, `claude-opus`, `claude-haiku`, `gpt-5` | `reasoning: {"effort": "low"}` forced (previously fully disabled — see below) |
| `none` | anything else | no reasoning parameter sent |

Every reasoning-capable model — mandatory or optional — is forced to
`effort: "low"` uniformly, rather than left at its provider default or
disabled outright. "Optional" models were originally run with reasoning
fully off; that was changed because a full-sheet scan (finding every
elevation on a dense, busy drawing) is exactly the kind of task a model does
measurably worse at with zero reasoning, and a fully default (uncapped)
reasoning pass produced its own failure mode — runaway length or, on some
requests, no content at all. "Low" effort was chosen as enough room to scan
systematically without triggering either failure mode. The same low-effort
value is used identically by all three detection methods.

### 4.3 Per-model input limits — DPI and pixel dimensions

This is the constraint that most shapes what any given method/model
combination can actually see, and it is tracked along **two independent
axes** that are easy to conflate: the DPI a page is *rendered* at, and the
pixel dimension a model's own encoder actually *retains* after its internal
downsampling. Raising the first past what the second can use adds nothing
but upload size and cost.

**Render DPI ceiling — the highest DPI at which sending a full page in one
request is still worth its cost, determined empirically per model:**

| Model | Max useful full-page DPI | Basis |
|---|---:|---|
| `gemini-3.1-pro-preview` / `gemini-3.6-flash` | 400–500 | Most tolerant of large page images of the four; accuracy at higher DPI does not clearly improve further. |
| `gpt-5.6-terra-pro` | 300 | Accuracy/cost trade-off point observed for this model specifically. |
| `claude-sonnet-5` | 180 | The lowest of the four — compounded by the internal downsampling below. |

**Internal downsample target — the pixel size a model's own encoder
actually reduces an oversized image to, independent of the DPI it was
rendered at:**

Only `claude-sonnet-5`'s downsample behavior has been directly pinned down:
it reduces an incoming image to roughly **1568 px on the long edge**
regardless of the DPI used to render it. This single number is the reason
Grid's `max_dim` control defaults to exactly 1568 (§3.3) — it was chosen
*because* it matches this model's observed ceiling, not picked
independently. A direct consequence: a 42″×30″ (ANSI E) sheet renders to
roughly 7 560 px on its long edge at 180 DPI alone, already well past 1568
px — so Claude's 180 DPI cap is not actually the binding constraint for this
model on these sheets; the internal downsample to ~1568 px is, and rendering
at any DPI above roughly 65–70 (the DPI at which a 42″ edge first reaches
~1568 px) would already be discarded by the model's own encoder before it
ever sees the extra detail. This is also why Claude's 180 DPI cap costs it
less in upload size than the ceiling number alone suggests, and why it
cannot be worked around by rendering at a higher DPI.

The equivalent downsample target for the other three models has **not been
independently measured** — their materially higher tolerance for full-page
DPI (400–500 for both Gemini models vs. Claude's 180) implies a
substantially higher internal ceiling than 1568 px, but no direct
measurement pins down what it actually is. This should be read as an
inference from indirect evidence (DPI tolerance), not a measured fact, and
is one of the concrete open items in §8.

**How `max_dim` was actually used across methods, and why the two flows
differ:**

- **Grid always applies `max_dim`** (UI default and typical value 1568,
  clamp range 256–8092 px) — the render is downscaled to at most this many
  pixels on its long edge *before* the grid lines are drawn, so that grid
  labels remain a legible fraction of what the model actually receives
  regardless of the sheet's native size. Because this value is fixed at
  Claude's own observed ceiling and applied identically to all four models
  in the completed comparison, it is a plausible under-utilization of the
  two Gemini models' and GPT's higher resolution tolerance in that specific
  comparison — Grid's numbers there are not measured on fully equal footing
  across models, only on equal *settings*.
- **One-Stage's `max_dim` is optional and off by default** (§3.1.4), with
  the same 256–8092 px clamp range when set. It was introduced *after* the
  completed 36-run comparison in §7 — every One-Stage run in that
  comparison used DPI alone, with no post-render pixel cap. Whether a
  `max_dim` cap (and at what value, per model) improves current One-Stage
  accuracy is an open, only partly explored question: an early default of
  1568 px for every model (matching Grid's Claude-derived value) was tried
  and then deliberately reverted to opt-in/off-by-default, specifically
  because 1568 px was never independently validated as correct for the
  non-Claude models — see §8.
- **Two-Stage has no `max_dim` control** — both stages render at a
  configured DPI only (300 DPI, uniform across all four models, in the
  completed comparison), with Stage 2's per-elevation crop being the
  mechanism that keeps the image small enough for detail to survive
  whatever downsampling a given model applies (§3.2), rather than an
  explicit pixel cap.

Two consequences worth keeping in mind when configuring any method:

- **Above a model's own downsample threshold, more DPI or a larger
  `max_dim` buys nothing** — it only increases upload size, token count,
  and cost, and can make image inspection *slower* for the model without
  making it more accurate.
- **What reliably raises effective resolution under a fixed DPI/pixel
  ceiling is sending less of the page per request** (a smaller crop, as in
  Two-Stage Stage 2) rather than raising the resolution of the whole page.

## 5. Evaluation protocol

### 5.1 Matching and metrics

Scoring is performed by a shared internal package (`location-scorer`),
identical across all three detection methods so that a difference in
measured accuracy reflects the detection approach, not a difference in how
it was scored. For each page, predicted and ground-truth boxes are bucketed
by `(page, object_type)` and matched greedily by IoU; a match counts as a
true positive at or above a configured IoU threshold (0.5 in the completed
comparison), with unmatched predictions counted as false positives and
unmatched ground truth as false negatives. Precision, recall, and F1 are
reported as **aggregate ("overall") metrics** — true positives, false
positives, and false negatives summed across every document in a run first,
then divided once (micro-averaged), not averaged per-document — a deliberate
choice so that a run's headline number is not skewed by a small,
easy-to-perfect document counting equally with a large, hard one.

Beyond the pass/fail match outcome, the scorer retains two continuous
signals useful for diagnosing *why* a method scores the way it does: the IoU
of every true-positive match (how tightly a landed box hugs the real
object), and the best available IoU of every false positive against any
ground-truth box on that page (how close a miss actually was). Separating
these lets "the box is loose but roughly in the right place" be
distinguished from "the box is nowhere near any real object" — a
distinction that matters most for the grid method (§3.3), whose cell-derived
boxes are structurally incapable of hugging an object that does not align to
a cell boundary, independent of whether the underlying detection was
correct.

Multi-file runs (several PDFs submitted together) fold `(file_index, page)`
into a single synthetic integer key before matching, so that "page 1" of one
uploaded file cannot spuriously match a ground-truth box on "page 1" of a
different file; single-file runs keep a bare page number for readability.

### 5.2 Cost and latency instrumentation

Every model call, across all three methods, passes through one shared
completion function that records wall-clock latency and requests
`usage.include: true` from OpenRouter. That returns, alongside standard
prompt/completion token counts, the **actual dollar cost charged for that
specific request** as reported by the provider — cost figures in this
benchmark are billing-derived, not estimated from a maintained local price
table, which keeps them correct as provider pricing changes and comparable
across models without the application needing to know anything about
pricing itself. Reasoning-token and cached-token counts are read
defensively where a provider reports them, since not every provider/model
combination includes those fields. A scoring or cost-accounting failure is
isolated from the detection result itself: an unscoreable run (e.g. no
ground truth supplied) or a failed usage lookup never turns an otherwise
successful detection request into a failed one.

For the Two-Stage method specifically, Stage 1's usage is carried forward
into the Stage 2 request so that a single combined cost/latency total is
reported per document — comparable to One-Stage's single-request total
despite spanning two separate HTTP calls and, typically, many more requests
overall (one Stage-1 request per page plus one Stage-2 request per approved
elevation crop).

### 5.3 Execution and batching controls

Independent of the detection method itself, the application exposes how
requests are grouped and scheduled along three axes: which configured
models run one after another vs. concurrently; whether an uploaded file's
pages are sent as one combined request or one request per file; and whether
a file's pages are sent together or split into one request per page (each
independently sequential or parallel). These settings affect cost and
latency (more, smaller requests vs. fewer, larger ones) but not, by
construction, which detection method is being run — they are held constant
within a comparison and are not treated as a fourth method in their own
right (§7).

## 6. Implementation notes

**6.1 Rendering is per-run, not pre-baked.** PDFs are rendered to raster
images at request time at whatever DPI (and, optionally, maximum pixel
dimension) the run specifies, so DPI/resolution experiments do not require
re-preparing the dataset.

**6.2 Ground-truth normalization is DPI-independent by construction (§2.3).**
Every scoring path derives a page's native point dimensions directly from
the source PDF rather than back-computing them from whatever pixel size a
particular render happens to produce, precisely to avoid the
render-resolution-dependent scoring bug described in §2.3. A closely
related bug was found and fixed during One-Stage's `max_dim` rollout: the
page-dimensions value scoring depends on was briefly computed *after* the
optional resize instead of before it, so any page that actually exceeded
`max_dim` silently scored against the wrong (shrunken) denominator — visible
as detections that looked correct when drawn but scored zero. The fix
captures native (pre-resize) dimensions unconditionally, before any
resizing branch runs, fully decoupling the scoring-side denominator from
whatever pixel size was actually sent to the model.

**6.3 Large documents are rendered one page at a time.** An early version of
the single-pass flow rendered an entire PDF into memory as fully-decoded
bitmaps in one call before any per-request logic ran; on a large, physically
big sheet set (28 pages of ANSI-E drawings, i.e. `prj0004`) this exhausted
available memory and crashed the service, independent of any request-level
concurrency setting. The fix renders and encodes one page at a time, so at
most one full-resolution raster is held in memory per file at once — a
pipeline property, not a change to detection behavior or accuracy. The same
bulk-render pattern is present, unfixed, in the Two-Stage and Grid flows'
own page-rendering steps; it was left untouched as out of scope for the
One-Stage-specific fix, not because it is believed safe on a large-enough
PDF.

**6.4 Every run is fully persisted.** Each run — for all three methods — is
saved with the exact rendered images, prompts, execution settings, and each
model's raw response, so a reported number can always be traced back to
literally what was sent to and returned by the model, not just a summary
statistic.

## 7. Scope of the completed comparison vs. current state

The full cross-method, cross-model comparison reported in `COMPARISON.md`
(36 runs = 4 models × 3 methods × 3 documents, 147 ground-truth objects per
model per method) was measured against the **original three-document,
four-type** version of this dataset (`elevation`, `cabinet`, `countertop`,
`elevation_callout`; no `floor plan`), with One-Stage rendering at a
per-model DPI and no pixel cap. Since that comparison was run:

- The taxonomy was revised to five types (§2.2) and `prj0001`–`prj0003`'s
  ground truth was relabeled to match, but **only the One-Stage prompt
  currently targets the five-type taxonomy** — Two-Stage's and Grid's
  prompts still target the original four types.
- Two documents were added (`prj0004`, `prj0005`), currently annotated for
  `elevation`/`floor plan`/`callout` only (§2.1) — not yet usable for
  `cabinet`/`countertop` evaluation, and not part of any completed
  cross-method comparison.
- One-Stage's prompt underwent the multi-round refinement described in
  §3.1.3, and gained an optional `max_dim` pixel cap (§4.3) — neither
  existed at the time of the completed comparison.

**A future benchmark re-run under the current dataset, taxonomy, and prompt
is therefore a distinct, not-yet-conducted experiment.** The results in
`COMPARISON.md` should be read as measuring the methods under the dataset
and prompt versions current as of that run, not the current state described
throughout this document.

One methodological principle is worth naming explicitly, since it shapes how
any of this should be extended: **a method's definition is not changed while
it is being benchmarked.** An accuracy problem discovered mid-comparison
(e.g. a model consistently hallucinating a specific failure pattern, or a
known model-specific coordinate bug) is addressed within that method's own
definition — its prompt, its per-model settings — never by folding in a
structural change (such as tiling the page into overlapping regions) that
would make it a different method mid-measurement. Such changes are legitimate
future work, but are scoped and evaluated as their own new method, not
retrofitted into an existing comparison.

## 8. Known open issues

- **A model-specific coordinate-reporting failure (`claude-sonnet-5`).**
  This model's returned coordinates are internally consistent but placed in
  neither the requested 0–1000 space nor the render's true pixel space,
  producing systematically wrong (not merely imprecise) box placement in
  every method that asks it for a numeric coordinate. It scores its best
  result of any method under Grid detection (§3.3), where it never emits a
  coordinate at all — evidence the failure is in the coordinate-reporting
  contract, not in the model's ability to locate the objects. Unresolved as
  of this writing; two rounds of prompt-wording fixes (§3.1.3) did not
  resolve it. Verifying a spatial claim like this against the actual
  document, rather than trusting that internally self-consistent output
  implies correct placement, was itself a lesson learned during diagnosis —
  an earlier hypothesis ("it's just a unit/scale mismatch") looked
  plausible from the numbers alone and turned out to be wrong once checked
  against the real page.
- **Grid detection's fitness is model-dependent, not universal.** It helps
  models that are unreliable at emitting coordinates and hurts models that
  are already accurate at it, because cell-derived boxes structurally cannot
  hug an object that does not align to a grid boundary. It is treated as a
  fallback for coordinate-unreliable models, not a general-purpose method.
- **Non-Claude internal downsample targets are unmeasured (§4.3).** Only
  Claude's ~1568 px ceiling has been directly pinned down; the other three
  models' equivalent ceilings are inferred, not measured, from their higher
  DPI tolerance.
- **Whether `max_dim` helps One-Stage at all, and at what value per model,
  is untested.** It was added, briefly defaulted to Claude's 1568 px value
  for every model, then made opt-in/off pending real evidence — no scored
  comparison of One-Stage with vs. without a pixel cap, per model, has been
  completed yet.
- **DPI has a per-model ceiling beyond which it buys nothing** (§4.3), which
  means "render everything at the highest DPI available" is not a valid
  accuracy lever once a model's internal downsampling threshold is passed —
  the DPI/method choice has to be made per model, not once globally.
