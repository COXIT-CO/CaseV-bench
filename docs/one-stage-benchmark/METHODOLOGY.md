# CaseV-Bench: Methodology

*Version of September 17, 2026*

*A benchmark for evaluating vision-language models on architectural millwork
drawing detection.*

## Abstract

CaseV-Bench measures how accurately, cheaply, and quickly different
vision-language models (VLMs) can locate five categories of objects —
`elevation`, `floor_plan`, `cabinet`, `countertop`, and `callout` — on raster
renders of architectural millwork drawing sets. The benchmark compares three
detection strategies against a shared, hand-labeled ground-truth dataset of
real construction-document PDFs, using IoU-based bounding-box matching and
provider-reported cost accounting. **Single-pass ("One-Stage") detection is
the method under active development**, and has now been evaluated
comprehensively: eleven current VLMs from five providers (Google, Anthropic,
OpenAI, xAI, Alibaba/Qwen), on nine annotated document sets (1,353
ground-truth objects), under one fixed prompt and protocol. That evaluation —
its full prompt text, results tables, and discussion of failure modes — is
written up in the accompanying paper,
`docs/one-stage-benchmark/one-stage-benchmark-paper.md`; this document
instead describes the dataset, the
object taxonomy, all three detection methods (including the two, Two-Stage
and Grid, that are outside the paper's current scope), model configuration
and per-model input limits, and the evaluation protocol, at an
implementation level of detail the paper does not attempt.

A human-in-the-loop two-pass method and a grid-cell indirection method are
also implemented and were included in an earlier, smaller completed
comparison; they are documented here at the level of detail needed to
understand what they measure, without One-Stage's full prompt-engineering
history. That earlier comparison (§7) — run on an earlier, four-type
taxonomy and a three-document subset of the current dataset — found
Two-Stage the strongest method by a clear margin. It has not been repeated
under the current five-type taxonomy, the current nine-document set, or
against the current eleven-model roster; doing so, for both Two-Stage and
Grid, is planned future work (see the paper's Conclusion and Future Work)
and has not yet been done — no number from that earlier comparison should be
read as still current.

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

Project effort has been concentrated on the single-pass method (§3.1): it is
the cheapest and fastest of the three by a wide margin (§7), and question
(2) above was, until recently, open only in the direction of "how much of
One-Stage's accuracy gap against the more expensive two-pass method can be
closed by prompt design and per-model configuration alone." The paper now
answers that for One-Stage itself across eleven models — one of them closes
the gap this document used to describe as structural almost entirely — which
shifts the open part of question (2) onto the other two methods: whether
Two-Stage's and Grid's earlier, smaller-scale advantage over One-Stage still
holds now that One-Stage has been pushed this far. That comparison is the
next planned piece of work (§7), not yet run.

## 2. Dataset

### 2.1 Source documents

The dataset's current, active set consists of **nine** real millwork/casework
drawing sets, submitted as multi-page PDFs and stored under
`drafts/trimmed/trim_prj{1..9}.pdf`:

| Document | Pages | GT objects |
|---|---:|---:|
| `trim_prj1` | 3 | 74 |
| `trim_prj2` | 2 | 48 |
| `trim_prj3` | 7 | 34 |
| `trim_prj4` | 9 | 241 |
| `trim_prj5` | 2 | 80 |
| `trim_prj6` | 24 | 323 |
| `trim_prj7` | 34 | 234 |
| `trim_prj8` | 29 | 231 |
| `trim_prj9` | 9 | 94 |
| **Total** | **119** | **1,353** |

Documents vary substantially in length and object density — from a 2-page,
48-object set (`trim_prj2`) to a 34-page, 234-object set (`trim_prj7`) — which
is a deliberate property of the dataset, not an artifact: it lets
per-document difficulty separate "hard because dense" from "hard because
large." This set superseded an earlier, smaller five-project set
(`prj0001`–`prj0005`, still present under `drafts/expected/` for reference)
that the original completed comparison in §7 was run against; the two sets
cover substantially the same kind of real construction documents, but are
not the same documents and are not directly interchangeable for a
before/after comparison.

All rendering is done at request time by the application itself
(`pdf2image`/poppler), never pre-rendered — every run controls its own DPI
and, optionally, a post-render maximum pixel dimension, so the same source
PDF can be re-rendered at whatever resolution a given method or model needs
(§4.3).

### 2.2 Object taxonomy

Ground truth and every detection method target the same five object types,
formalized from a written annotation-rules reference document (internally
`drafts/new_annotation/Annotation rules.pdf`, authored by the project's
domain reviewers) into the current prompt specification (§3.1.1, reproduced
verbatim in the paper's Appendix A):

| Label | Definition (condensed) |
|---|---|
| `elevation` | A detailed orthographic view of one object, assembly, or wall, from a single direction — front, back, side, or top. Judged by geometry and scope (one assembly, one direction), not by caption wording. Has a recognizable elevation reference marker and usually a scale. Excludes sections and floor plans. |
| `floor_plan` | A top-down view of a room or overall space — walls, boundaries, and layout seen from above. A top-down view of a single object/assembly is `elevation`, not `floor_plan`; the deciding axis is scope (room vs. single assembly), not view direction. |
| `cabinet` | One built-in or freestanding storage unit with an enclosed body, annotated only in **front view**. The box covers the cabinet body only — not the countertop/backsplash above it, not the wall or ceiling above a wall cabinet, not adjoining units in the same run (each unit is its own box). |
| `countertop` | A horizontal work surface on cabinets or on its own legs/brackets, annotated only in **front view**; a backsplash is included in the same box where present. Does not require a cabinet beneath it. |
| `callout` | A small circular/diamond symbol, with or without a pointer triangle or leader line, referencing a page/elevation/section elsewhere in the set. Explicitly excludes a *view title bubble* — a circle attached to the left end of a view's own underlined title — even when its contents look identical to a real callout; the distinguishing signal is attachment, not content. |

A sixth category, `section` (a wide cross-sectional cutaway), is explicitly
**excluded from annotation** per the reference document, including any
cabinet, countertop, or callout that is visible only inside one — those are
annotated on their own separate front-view elevation instead. Every box, of
every type, is required to be **tight**: each of its four edges touches the
object's own outermost drawn line, with no padding.

By ground-truth object count across the current nine documents, the
taxonomy is dominated by `callout` (480, 35.5%) and `elevation` (300,
22.2%), with `floor_plan` (166, 12.3%), `cabinet` (317, 23.4%), and
`countertop` (90, 6.7%) making up the rest. `callout`'s share is inflated by
two outlier-dense documents (`trim_prj4`: 181 of 241 objects; `trim_prj8`:
130 of 231) that contain long runs of plan-view reference symbols;
excluding those two documents, the label distribution is closer to even
across the five types.

**This is the current, five-type taxonomy.** It is a revision of an earlier,
four-type taxonomy (`elevation`, `cabinet`, `countertop`,
`elevation_callout`, no `floor_plan`) used for the dataset's first version
and for the original completed comparison reported in `COMPARISON.md` (§7).
The revision renamed `elevation_callout` → `callout`, introduced
`floor_plan` (partly reclassified out of what had been counted as
`elevation`, partly newly annotated), and left `cabinet`/`countertop`
untouched in scope, though the callout/elevation disambiguation rules
themselves were later corrected against direct ground-truth inspection
(§3.1.3). `floor_plan` is written with a literal underscore, and that
spelling is authoritative end-to-end in the current prompt (§3.1.1) and
scoring — the five valid label strings are exactly `elevation`,
`floor_plan`, `cabinet`, `countertop`, `callout`. **The current, five-type
taxonomy and its current prompt apply to One-Stage only** (§7) — the
Two-Stage and Grid prompts, last exercised in the original completed
comparison, still target the original four types and have not been updated
to the five-type taxonomy.

### 2.3 Ground-truth format and coordinate convention

Ground-truth objects for a project are stored as
`{"project_id": ..., "objects": [{"id", "category", "page", "bbox"}, ...]}`,
with a `bbox` given as `{"x", "y", "width", "height"}` (top-left origin);
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

Detected-object coordinates are requested from models as **normalized
fractions in [0, 1]** of the image actually sent, independently on each
axis, origin at the top-left corner (`x_min < x_max`, `y_min < y_max`) for
the One-Stage flow (§3.1.1), and as **named grid-cell labels** for the Grid
flow (§3.3), converted to a 0–1 fraction in code. This is a reversal from an
earlier phase of prompt development, where a 0–1000 integer scale was found
to measurably outperform a plain 0.0–1.0 fraction on an earlier prompt
version, for reasons not further diagnosed at the time. The current prompt
(§3.1.1, reproduced verbatim in the paper's Appendix A) reverted to the 0–1
fraction and is the best-performing version found to date; whether the
earlier integer-scale finding still holds against the *current* prompt and
taxonomy has not been directly re-tested, and should not be assumed to
generalize.

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

The current prompt frames the model as looking at "a full AEC drawing
SHEET" that "may hold several drawing views... at VERY DIFFERENT SCALES,"
and states the task as **multi-class localization**: find and box every
instance of the five types in §2.2, tag each with its label, and report
large regions and small symbols side by side in the same pass. Boxes of
different types are explicitly permitted to overlap (a cabinet sits inside
its elevation, a callout sits inside a floor plan) and the model is told
this is expected, not an error to resolve by merging or dropping one of the
two. It is structured as a fixed sequence of sections:

1. **CONTEXT / TASK** — names the document class and states the multi-class
   localization task as above.
2. **TYPE 1–5** — one block per label (§2.2), each with a worked visual
   description, a unit-of-annotation rule (one view/unit = one box), and an
   explicit negative list of what the type excludes. `cabinet`'s block
   additionally spells out its box-edge rule operationally: the top edge is
   the line directly under the countertop for a base cabinet (never the
   countertop/backsplash itself) or the cabinet's own top line for a wall
   cabinet (never the wall/ceiling above it), with a standing self-check —
   before emitting a box, verify the region between the box's top edge and
   the object's own top line contains nothing belonging to a different
   object, and shrink the box if it does.
3. **IGNORE — sections** — a section (a wide cross-sectional cutaway) is out
   of scope entirely, under any label; a cabinet, countertop, or callout
   visible only inside one is not annotated there, since it is expected to
   also have its own separate front-view elevation elsewhere on the sheet.
4. **DISAMBIGUATION** — a short, restated decision table for the
   view-level classes (`floor_plan` vs. `elevation` vs. `section`), scoped
   by what the view covers rather than by view direction.
5. **BOX TIGHTNESS** — every box must be tight to the object's own
   outermost drawn line, with the same top-edge self-check repeated as a
   general rule for every type, not just `cabinet`.
6. **COORDS** — the 0–1 normalized-fraction contract from §2.3, stated
   explicitly as independent per axis with a top-left origin.
7. **OUTPUT** — a bare JSON array, no prose or markdown fences, one object
   per detection shaped `{"label": ..., "bounding_box": {"x_min", "y_min",
   "x_max", "y_max"}}`, `label` constrained to exactly the five taxonomy
   strings (§2.2), and an explicit empty array `[]` when a page contains
   none of the five types. No count or summary field is requested.

The full prompt text is reproduced verbatim in the paper's Appendix A; this
document does not duplicate it, to avoid two copies drifting apart.

#### 3.1.2 Response parsing and validation

The raw model response is stripped of any Markdown code-fence wrapping and
parsed as JSON; a malformed response is retried through `json_repair` before
being treated as a hard failure. Once parsed:

- **Box resolution reads the `bounding_box` object's four named fields**
  (`x_min`, `y_min`, `x_max`, `y_max`) and only accepts them when internally
  consistent (`x_max > x_min`, `y_max > y_min`) — the entire point of
  requesting named fields instead of an array is to not have to trust the
  model's own assembly step. A bare `box`/coordinate array is used only as a
  fallback for models/prompts that never produce the named object shape.
- **Labels are filtered against a fixed whitelist** — exactly the five
  taxonomy strings from §2.2 (`floor_plan` with the literal underscore) —
  any other label string is dropped rather than passed through or aliased.
  An earlier draft accepted both `"elevation_callout"` and `"callout"` "to
  be safe"; that alias was deliberately removed once the taxonomy revision
  (§2.2) landed — the intent is that the label surface the app accepts is
  exactly and only what is currently asked for, not a superset kept for
  legacy tolerance.
- **A three-way coordinate-scale safety net** auto-detects, per object,
  whether the returned box is actually a 0–1 fraction (`max(box) ≤ 1.0` →
  used as-is, the currently requested scale), a 0–1000 integer scale
  (`max(box) ≤ 1000` → divided by 1000), or a raw pixel coordinate
  (`max(box) > 1000` → rescaled using the page's real rendered pixel
  dimensions as the denominator). This exists because a model occasionally
  ignores the requested scale outright — most notably the specific,
  unresolved `claude-sonnet-5` failure mode in §8 — and is strictly a safety
  net: it does not change what scale is *requested*, only how a response
  that disobeys the request is still salvaged rather than silently
  mis-scored.
- **Any model-reported `"summary"` or count field is parsed but never
  read.** Object counts shown in the UI and used for pass/fail comparisons
  against a pasted expected-summary are always recomputed in code from the
  final, validated object list.

#### 3.1.3 Iterative prompt refinement (process)

The current prompt (§3.1.1) is the product of several rounds of empirical
refinement, mined directly from real ground truth rather than from prose
reasoning about the reference PDF alone — rendering the actual ground-truth
boxes over the actual PDF pages and zooming into individual symbols
surfaced several details the reference document's prose never stated
outright, even though its own example images contained them, and in a few
cases corrected rules the reference document itself implied but the real,
annotated ground truth did not follow. Fixes applied through this process,
in the order they were found:

- **RCP counts as `floor_plan`, not its own thing.** Confirmed by finding a
  ground-truth `floor_plan` box drawn around a reflected-ceiling-plan panel
  (ceiling grid + light fixtures) that an early prompt draft's `floor_plan`
  bullet never explicitly named as in-scope.
- **`callout` has two valid graphic shapes, not one.** A ground-truth
  callout turned out to be a solid fused diamond/star made of 2–4 filled
  triangular arrowheads with no separate round/pentagon container at all —
  a shape an early wording's "round container with an attached arrow"
  phrasing did not cover.
- **`elevation` is any captioned, single-direction view, not only casework
  standing on a floor line.** Direct ground-truth inspection found column
  plan details, roof-edge details, and wall details annotated as
  `elevation` alongside ordinary casework elevations — an earlier, narrower
  wording ("a flat, straight-on view of casework on a floor line") caused a
  model to return an empty result on whole sheets of these.
- **`callout` bubbles are annotated wherever they are drawn, including
  inside elevations, details, and sections** — not only on floor
  plans/RCPs as an earlier rule assumed from a subset of the dataset that
  happened to keep callouts and elevations visually separate. A filled
  arrowhead is not required either; a plain circle with a leader line
  counts.
- **Countertop over-extension**, traced to a specific document's reported
  symptom: an early wording only ever warned against stopping *short* of a
  countertop's real extent, creating an asymmetric bias toward
  over-tracing past the drawn line's actual end. Reworded to warn against
  both directions equally, and to require a non-zero box height.
- **Edge-interpolation drift across repeated bays**, diagnosed as the likely
  cause of one document's broad, sheet-wide under-performance: sheets with
  6+ near-identical cabinet bays in one run invite extrapolating the rest of
  the run's spacing from the first bay or two, an assumption that compounds
  error the further it is carried. The prompt now explicitly instructs
  reading each bay's own edges directly instead.
- **`section` exclusion**, added from a later, substantially expanded
  version of the reference PDF: a wide cross-sectional cutaway is not an
  `elevation`, and a cabinet visible only inside one is not annotated there
  — it is annotated on its own separate elevation view instead, which the
  source documents were confirmed to reliably also contain.
- **Countertop definition broadened** to allow a countertop carried on legs
  or brackets with no cabinet run underneath at all.
- **Callout vs. title bubble**, an explicit named exception: a numbered
  circle attached to the left end of a view's own underlined title (naming
  that view) is not a callout, even when its contents look identical to a
  real one — the distinguishing signal is attachment, not content.
- **Coordinate scale reverted from 0–1000 integers back to 0–1 fractions**
  (§2.3) in the round that produced the current, best-performing prompt.

A separate, explicitly time-boxed attempt targeted `claude-sonnet-5`'s
chronic near-zero accuracy (§8): several rounds of prompt-wording changes
(reframing the coordinate section, removing hallucination-priming language)
were tried and did not resolve it. That problem is currently understood to
be structural — a coordinate-space reporting issue, not a prompt-wording one
— and is tracked separately in §8 rather than folded into further prompt
iteration.

One general lesson from this process, applied consistently across every
round: **naming the wrong answer in order to warn against it is avoided.**
An early draft of the coordinate section repeated "do not use pixels, do
not use a 0.0–1.0 fraction" three times in slightly different wording (from
the phase when 0–1000 integers were the target scale); this was cut down to
one clean positive instruction per checkpoint, since naming the wrong
format to warn against it risks priming the model toward exactly that
format (negation priming), on top of being needless verbosity. The same
principle was applied when a sentence describing sheets as containing "a
repetitive grid of similar-looking framed drawings" was found to correlate
with a model inventing repeated, pixel-identical frames that were not
actually on the page — replaced with a neutral instruction not to stop
scanning early, without asserting what the sheet typically contains.

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
sent together or split one-request-per-page. **The current prompt in
§3.1.1 is written for, and current prompt-tuning work assumes, exactly one
image per request** — i.e. the page-splitting axis set to
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

**What is, and is not, confirmed about the harness behind the paper's
eleven-model results.** The paper's own prompt text and scored detections
are known with confidence, since both are recoverable directly from the
shared results table each run was written to. Whether that specific run of
eleven models used exactly this section's rendering DPI, request
granularity, and retry/repair behavior, as opposed to some other
configuration of the same underlying pipeline, is not independently
documented and is called out as an open item in both the paper's own
Limitations section and §7 below, rather than assumed.

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
It was the strongest method in the original completed comparison (§7), by a
clear margin, but is not currently the focus of active development — see
§1's introduction. Re-running it against the current five-type taxonomy,
the current nine-document set, and a wider model roster is planned future
work, not yet done (§7).

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
per cell. Grid still targets the original four-type taxonomy (§2.2/§7), and
— like Two-Stage — has not been re-run against the current dataset,
taxonomy, or model roster; that re-run is planned future work (§7), not yet
done, and no accuracy claim about Grid in this document should be read as
current.

## 4. Models and configuration

### 4.1 Models evaluated

Two evaluations exist at different scope, and should not be conflated:

**The current One-Stage evaluation** (fully reported in the paper) covers
**eleven models across five providers**, all accessed through OpenRouter:

| Provider | Models |
|---|---|
| OpenAI | `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra` |
| Google | `gemini-3.8-flash`, `gemini-3.5-flash`, `gemini-3.1-pro-preview` |
| Anthropic | `claude-fable-5.1`, `claude-opus-5`, `claude-sonnet-5` |
| Qwen | `qwen3.8-max` |
| xAI | `grok-4.6` |

This is the primary, up-to-date accuracy/cost picture for the One-Stage
method (§3.1) on the current nine-document, five-type dataset (§2); full
per-model precision/recall/F1, cost, and latency are in the paper's §6, not
repeated here.

**The original, completed cross-method comparison** (§7) — the only
comparison that includes Two-Stage and Grid at all — covered **four**
models: `google/gemini-3.1-pro-preview`, `google/gemini-3.6-flash`,
`openai/gpt-5.6-terra-pro`, and `anthropic/claude-sonnet-5`, on the earlier
three-document, four-type version of the dataset. The application itself is
not limited to these four for any method — any OpenRouter-served vision
model can be configured per run — but Two-Stage and Grid have not been
re-run against the current eleven-model roster, and their numbers from the
original comparison should not be compared directly against the current
One-Stage results, which use a different dataset, taxonomy, and (for most
of the eleven) different models entirely.

### 4.2 Reasoning-budget equalization

Several models support (or require) an internal reasoning/thinking pass
before producing output, and leaving that uncontrolled would confound
"model capability" with "how much the provider's default reasoning budget
happens to allocate to this task." Every model call through this
application classifies the model name by substring match and applies a
uniform policy:

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
value is used identically by all three detection methods. Whether the
harness behind the paper's eleven-model results applies this same policy is
not independently confirmed (§3.1.4, §7).

### 4.3 Per-model input limits — DPI and pixel dimensions

This is the constraint that most shapes what any given method/model
combination can actually see, and it is tracked along **two independent
axes** that are easy to conflate: the DPI a page is *rendered* at, and the
pixel dimension a model's own encoder actually *retains* after its internal
downsampling. Raising the first past what the second can use adds nothing
but upload size and cost.

**Render DPI ceiling, from the original four-model comparison — the highest
DPI at which sending a full page in one request was still worth its cost,
determined empirically:**

| Model | Max useful full-page DPI | Basis |
|---|---:|---|
| `gemini-3.1-pro-preview` / `gemini-3.6-flash` | 400–500 | Most tolerant of large page images of the four; accuracy at higher DPI does not clearly improve further. |
| `gpt-5.6-terra-pro` | 300 | Accuracy/cost trade-off point observed for this model specifically. |
| `claude-sonnet-5` | 180 | The lowest of the four — compounded by the internal downsampling below. |

These four numbers are the only ones measured directly. **The equivalent
ceiling for the other models in the current eleven-model roster (§4.1) —
including newer entries in the Gemini and Claude families, and every
OpenAI, Qwen, and xAI model — has not been measured.** The paper identifies
a per-model image-resolution ceiling as one of two structural bottlenecks
behind its results, without pinning down a number per current model; this
document does not fill that gap with an estimate.

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

The equivalent downsample target for every other model — in both the
original four-model comparison and the current eleven-model roster — has
**not been independently measured**. This should be read as an open item
(§8), not a measured fact.

**How `max_dim` was actually used across methods, and why the two flows
differ:**

- **Grid always applies `max_dim`** (UI default and typical value 1568,
  clamp range 256–8092 px) — the render is downscaled to at most this many
  pixels on its long edge *before* the grid lines are drawn, so that grid
  labels remain a legible fraction of what the model actually receives
  regardless of the sheet's native size. Because this value is fixed at
  Claude's own observed ceiling and applied identically to all four models
  in the original completed comparison, it is a plausible under-utilization
  of the two Gemini models' and GPT's higher resolution tolerance in that
  specific comparison — Grid's numbers there are not measured on fully
  equal footing across models, only on equal *settings*.
- **One-Stage's `max_dim` is optional and off by default** (§3.1.4), with
  the same 256–8092 px clamp range when set. It was introduced *after* the
  original 36-run comparison in §7 — every One-Stage run in that comparison
  used DPI alone, with no post-render pixel cap. Whether a `max_dim` cap
  (and at what value, per model) improves current One-Stage accuracy is an
  open, only partly explored question: an early default of 1568 px for
  every model (matching Grid's Claude-derived value) was tried and then
  deliberately reverted to opt-in/off-by-default, specifically because 1568
  px was never independently validated as correct for the non-Claude
  models — see §8.
- **Two-Stage has no `max_dim` control** — both stages render at a
  configured DPI only (300 DPI, uniform across all four models, in the
  original completed comparison), with Stage 2's per-elevation crop being
  the mechanism that keeps the image small enough for detail to survive
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

Scoring is performed by a shared internal package, `location-scorer`
(pinned at `v0.1.0` for the current eleven-model One-Stage evaluation),
identical across all three detection methods and both the original
completed comparison and the current evaluation, so that a difference in
measured accuracy reflects the detection approach, not a difference in how
it was scored. For each page, predicted and ground-truth boxes are bucketed
by `(page, object_type)` and matched greedily by IoU; a match counts as a
true positive at or above a configured IoU threshold (0.5 throughout, in
both the original comparison and the current evaluation), with unmatched
predictions counted as false positives and unmatched ground truth as false
negatives. Precision, recall, and F1 are reported as **aggregate
("overall") metrics** — true positives, false positives, and false
negatives summed across every document in a run first, then divided once
(micro-averaged), not averaged per-document — a deliberate choice so that a
run's headline number is not skewed by a small, easy-to-perfect document
counting equally with a large, hard one.

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
correct, and is also how the paper separates a placement problem from a
detection problem in its own results.

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
big sheet set this exhausted available memory and crashed the service,
independent of any request-level concurrency setting. The fix renders and
encodes one page at a time, so at most one full-resolution raster is held in
memory per file at once — a pipeline property, not a change to detection
behavior or accuracy. The same bulk-render pattern is present, unfixed, in
the Two-Stage and Grid flows' own page-rendering steps; it was left
untouched as out of scope for the One-Stage-specific fix, not because it is
believed safe on a large-enough PDF — the current nine-document set
includes several documents at or beyond the size that originally triggered
this failure mode.

**6.4 Every run is fully persisted.** Each run — for all three methods — is
saved with the exact rendered images, prompts, execution settings, and each
model's raw response, so a reported number can always be traced back to
literally what was sent to and returned by the model, not just a summary
statistic.

## 7. Scope of the completed comparisons vs. current state

Two distinct comparisons exist, and neither should be read as extending the
other's coverage:

**The original completed comparison**, reported in `COMPARISON.md` (36 runs
= 4 models × 3 methods × 3 documents, 147 ground-truth objects per model per
method), was measured against the **original three-document, four-type**
version of this dataset (`elevation`, `cabinet`, `countertop`,
`elevation_callout`; no `floor_plan`), with One-Stage rendering at a
per-model DPI and no pixel cap. It is the only comparison that includes
Two-Stage and Grid at all, and found Two-Stage the strongest method by a
clear margin, with Grid trailing both One-Stage and Two-Stage overall while
still helping specific coordinate-unreliable models (§8).

**The current evaluation**, reported in full in the paper, covers **only
One-Stage**, run against the current nine-document, five-type dataset (§2)
across eleven models from five providers (§4.1). Since the original
comparison was run:

- The taxonomy was revised to five types (§2.2), and later corrected against
  direct ground-truth inspection (§3.1.3) — but **only the One-Stage prompt
  currently targets the current, corrected five-type taxonomy**; Two-Stage's
  and Grid's prompts still target the original four types.
- The dataset was replaced by the current nine-document, 1,353-object set
  (§2.1) — larger and more varied than the original three-to-five-document
  set the original comparison and its immediate follow-ups used.
- One-Stage's prompt underwent the multi-round refinement described in
  §3.1.3 (including reverting the coordinate scale from 0–1000 integers back
  to 0–1 fractions), and gained an optional `max_dim` pixel cap (§4.3) —
  neither existed at the time of the original comparison.
- The model roster grew from four to eleven, across five providers instead
  of four (§4.1).

**A benchmark re-run of Two-Stage and Grid under the current dataset,
taxonomy, and model roster is therefore a distinct, not-yet-conducted
experiment**, and is the concrete next step both this document and the
paper's Conclusion and Future Work identify. Until that re-run happens, no
number from `COMPARISON.md` should be read as describing Two-Stage's or
Grid's current standing relative to One-Stage's now much stronger,
eleven-model results — the comparison that would establish that has simply
not been run yet.

One methodological principle is worth naming explicitly, since it shapes how
any of this should be extended: **a method's definition is not changed while
it is being benchmarked.** An accuracy problem discovered mid-comparison
(e.g. a model consistently hallucinating a specific failure pattern, or a
known model-specific coordinate bug) is addressed within that method's own
definition — its prompt, its per-model settings — never by folding in a
structural change (such as tiling the page into overlapping regions) that
would make it a different method mid-measurement. Such changes are
legitimate future work, but are scoped and evaluated as their own new
method, not retrofitted into an existing comparison.

## 8. Known open issues

- **A model-specific coordinate-reporting failure (`claude-sonnet-5`).**
  This model's returned coordinates are internally consistent but placed in
  neither the requested coordinate space nor the render's true pixel space,
  producing systematically wrong (not merely imprecise) box placement in
  every method that asks it for a numeric coordinate. It scored its best
  result of any method under Grid detection in the original comparison
  (§3.3), where it never emits a coordinate at all — evidence the failure is
  in the coordinate-reporting contract, not in the model's ability to
  locate the objects. It remains the weakest model in the current
  eleven-model One-Stage evaluation as well (see the paper's §6), consistent
  with this being unresolved. Several rounds of prompt-wording fixes
  (§3.1.3) did not resolve it. Verifying a spatial claim like this against
  the actual document, rather than trusting that internally self-consistent
  output implies correct placement, was itself a lesson learned during
  diagnosis — an earlier hypothesis ("it's just a unit/scale mismatch")
  looked plausible from the numbers alone and turned out to be wrong once
  checked against the real page.
- **Grid detection's fitness is model-dependent, not universal**, as far as
  the original four-model comparison showed. It helped models that were
  unreliable at emitting coordinates and hurt models that were already
  accurate at it, because cell-derived boxes structurally cannot hug an
  object that does not align to a grid boundary. Whether this still holds
  for the current eleven-model roster is unknown, since Grid has not been
  re-run against it (§7).
- **Two-Stage's and Grid's standing relative to the current, much stronger
  One-Stage results is unknown** (§7) — the single most consequential open
  item in this document, and the one both this document and the paper's
  Conclusion and Future Work identify as the next planned study.
- **Non-Claude internal downsample targets are unmeasured** (§4.3), for
  both the original four models and every model added since. Only Claude's
  ~1568 px ceiling has been directly pinned down.
- **Whether `max_dim` helps One-Stage at all, and at what value per model,
  is untested.** It was added, briefly defaulted to Claude's 1568 px value
  for every model, then made opt-in/off pending real evidence — no scored
  comparison of One-Stage with vs. without a pixel cap, per model, has been
  completed yet.
- **DPI has a per-model ceiling beyond which it buys nothing** (§4.3), which
  means "render everything at the highest DPI available" is not a valid
  accuracy lever once a model's internal downsampling threshold is passed —
  the DPI/method choice has to be made per model, not once globally. This
  ceiling is documented for only four models total (§4.3).
- **The execution harness behind the current eleven-model results is not
  independently documented** beyond its prompt and scored outputs (§3.1.4,
  §7) — its exact rendering DPI, request granularity, and retry/repair
  behavior are not confirmed to match this section's description of the
  application's own pipeline, and are flagged as an open item in the paper
  itself rather than assumed.
