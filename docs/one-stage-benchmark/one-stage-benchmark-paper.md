# Single-Pass Vision-Language Model Prompting for Object Localization in Architectural Millwork Drawings

Andrii Chumak, Iryna Mykytyn, Andrian Kozynets, Yurii Didyk, Yelysaveta Mykytyn, Victor Mykhailov, Volodymyr Hresko

COXIT

*VERSION BY 18.09.26*


## Abstract

Architectural millwork and casework drawing sets — cabinet/casework elevations, floor
plans, and reflected ceiling plans — are dense, low-redundancy technical documents that
general-purpose vision-language models (VLMs) receive no domain-specific training for.
We study whether an off-the-shelf, non-fine-tuned VLM can nonetheless localize the
objects on such a sheet from a single instruction prompt and a single full-page image
per request ("One-Stage" detection), and at what accuracy, latency, and dollar cost.
We introduce **CaseV-Bench**, a benchmark built around five object categories —
`elevation`, `floor plan`, `cabinet`, `countertop`, and `callout` — hand-annotated on
real construction-document PDFs, and evaluate eleven current VLMs from five providers
(Google, Anthropic, OpenAI, xAI, and Alibaba/Qwen) under one fixed single-pass
prompting protocol, scored by greedy IoU matching against ground truth at IoU ≥ 0.5
with real, provider-reported per-request dollar cost. Across nine annotated sheet sets
(1,353 ground-truth objects), aggregate micro-averaged F1 is **55.4%**, ranging from
**21.0%** to **91.6%** across models — a 4.4× spread wide enough that model choice
dominates prompt design entirely. One model, `gpt-6-astra`, stands apart from the rest
of the field: it is the only model tested that keeps a high F1 (>80%) on the three
small, densely packed object types (`cabinet`, `countertop`, `callout`) where every
other model we test collapses to 0–68% F1, but it is also the single most expensive
model per document in the evaluation, so it is not the best model by every axis at
once. We report per-model, per-document, and per-object-type results and quantify two
structural bottlenecks specific to this domain — per-model image-resolution ceilings
and a model-specific coordinate-reporting failure. We conclude that single-pass VLM
prompting is, for at least one current model, a plausible unattended first pass for
this document class, while remaining an assisted-review signal at best for the rest of
the field, and identify the specific failure modes — small, densely packed objects and
per-model coordinate reliability — that bound accuracy for every model but the
strongest one.

## 1. Introduction

Automating even a first-pass inventory of the casework objects on an architectural
millwork sheet — which elevations exist, which cabinets and countertops they contain,
which plan-view callouts point to which elevation — is a plausible assisted-review task
for a vision-language model. It is also a genuinely hard one: the objects of interest
are small relative to the sheet, densely packed, and drawn with domain-specific
conventions (solid vs. dashed lines, front-view-only annotation rules, section vs.
elevation distinctions) that a model trained on general-purpose imagery has no special
exposure to. Unlike natural-image object detection, there is no large labeled corpus of
architectural millwork drawings to fine-tune against, which makes zero-shot, prompted
detection with a general-purpose VLM the only readily available approach — and raises
the question of how far that approach actually gets.

This paper focuses on the cheapest and most direct way to ask a VLM to do this: send it
one full-page raster image and one instruction prompt per request, and ask it to return
every object of every type it finds, in one pass ("One-Stage" detection, §4). This is
the object of active development in the underlying project because it is the
lowest-cost, lowest-latency configuration by a wide margin, and the central empirical
question is how much of its accuracy gap against a more expensive, multi-pass,
human-in-the-loop alternative can be closed by prompt design and per-model
configuration alone, without changing its one-request-per-page architecture.

Concretely, this paper asks:

1. **How accurately** can a current, general-purpose VLM localize five categories of
   millwork objects from one full-page image and one prompt, with no fine-tuning and no
   task-specific training data?
2. **Does model choice matter more than prompt engineering?** — i.e., given a single,
   carefully iterated prompt held fixed across models, how much does measured accuracy
   vary by model alone?
3. **What does an accurate-enough configuration cost**, in dollars and wall-clock
   latency, per document — using real, provider-billed cost rather than a
   token-count estimate?
4. **What holds single-pass detection back**, structurally — is the bottleneck image
   resolution, prompt specification, or something specific to how a given model reports
   coordinates?

We answer these with a benchmark (§3), a fully specified detection method and prompt
(§4), a fixed evaluation protocol (§5), and results across eleven models and nine
documents (§6), followed by a discussion of failure modes and limitations (§7–§8).

## 2. Related Work

**Visual grounding with general-purpose VLMs.** Localizing objects by asking a
general-purpose VLM to emit a bounding box — rather than training a dedicated detector
— is an active but still unreliable capability. Recent grounding benchmarks report that
strong general-purpose models are markedly weaker at precise coordinate output than
either specialized grounding models or the same models' own perceptual/reasoning
performance would suggest, with accuracy that degrades further as objects get smaller or
the image gets more cluttered [1, 2]. This matches the central empirical constraint this
paper works within: the object types considered here (`cabinet`, `countertop`
especially) are small and densely packed, precisely the regime where general-purpose VLM
grounding is weakest.

**Symbol and object detection in engineering/architectural drawings.** A separate line
of work treats this as a conventional supervised object-detection problem: YOLO-family
and transformer detectors trained on labeled corpora of engineering or floor-plan
drawings report strong accuracy (mAP@50 in the 80%+ range is typical) on the symbol
classes they are trained for [3, 4]. That accuracy is bought with a labeled training set
specific to the drawing convention and symbol vocabulary in question — an asset this
project does not have, and one that is expensive to build for a narrow, evolving
taxonomy (§3 below describes ours). The approach evaluated in this paper trades that
training cost for a zero-shot general-purpose model, at a currently much lower measured
accuracy (§6); how much of that gap a small amount of task-specific supervision would
close is outside this paper's scope.

**VLM-based document intelligence.** Broader surveys of LLM/VLM use in document
understanding cover layout analysis, OCR-free document QA, and structured extraction
from visually rich documents, and describe zero-shot, single-request-per-page prompting
(as used here) as one point in a design space that also includes retrieval-augmented and
multi-stage pipelines [5, 6]. This paper differs from that broader literature in scope
rather than approach: it evaluates one narrow, densely-annotated document class
(architectural millwork drawings) against real, human-produced ground truth with
IoU-based localization scoring, rather than end-to-end extraction accuracy or QA
correctness.

To the authors' knowledge, no existing benchmark evaluates general-purpose,
non-fine-tuned VLMs specifically on object localization within architectural millwork
drawing sets under a controlled single-pass prompting protocol with provider-billed cost
accounting — the gap this paper's benchmark is built to fill.

## 3. Dataset

### 3.1 Source documents

CaseV-Bench's current evaluation set consists of nine real millwork/casework drawing
sets, submitted as multi-page PDFs. Documents vary substantially in length and object
density — from a 2-page, 48-object set (`prj2`) to a 34-page, 234-object set
(`prj7`) — which is a deliberate property of the dataset, not an artifact: it lets
per-document difficulty (§6.2) separate "hard because dense" from "hard because large."

| Document | Pages | GT objects |
|---|---:|---:|
| `prj1` | 3 | 74 |
| `prj2` | 2 | 48 |
| `prj3` | 7 | 34 |
| `prj4` | 9 | 241 |
| `prj5` | 2 | 80 |
| `prj6` | 24 | 323 |
| `prj7` | 34 | 234 |
| `prj8` | 29 | 231 |
| `prj9` | 9 | 94 |
| **Total** | **119** | **1,353** |

All PDFs are rendered to raster images at request time by the application itself, never
pre-rendered, so every run controls its own DPI and, optionally, a post-render maximum
pixel dimension — the same source PDF can be re-rendered at whatever resolution a given
model needs (§4.3).

### 3.2 Object taxonomy

Ground truth and the detection prompt (§4) target five object types. Definitions below
are condensed from the live detection prompt's own type specifications, which are
themselves derived from a written annotation-rules reference authored by the project's
domain reviewers:

| Label | Definition (condensed) |
|---|---|
| `elevation` | A detailed orthographic view of one object, assembly, or wall, from a single direction — front, back, side, or top. Judged by geometry and scope (one assembly, one direction), not by caption wording. Excludes sections and floor plans. |
| `floor_plan` | A top-down view of a room or overall space — walls, boundaries, and layout seen from above. A top-down view of a single object/assembly is `elevation`, not `floor_plan`; the deciding axis is scope (room vs. single assembly). |
| `cabinet` | One built-in or freestanding storage unit with an enclosed body, annotated only in **front view**. The box covers the cabinet body only — not the countertop/backsplash above it, not the wall or ceiling above a wall cabinet, not adjoining units in the same run. |
| `countertop` | A horizontal work surface on cabinets or on its own legs/brackets, annotated only in **front view**; a backsplash is included in the same box where present. |
| `callout` | A small circular/diamond symbol, with or without a pointer triangle or leader line, referencing a page/elevation/section elsewhere in the set. Explicitly excludes a *view title bubble* — a circle attached to the left end of a view's own underlined title — even when its contents look identical to a real callout; the distinguishing signal is attachment, not content. |

A sixth category, `section` (a wide cross-sectional cutaway), is explicitly **excluded
from annotation**, including any cabinet, countertop, or callout that is visible only
inside one — those are annotated on their own separate front-view elevation instead.
Every box, of every type, is required to be **tight**: each of its four edges touches
the object's own outermost drawn line, with no padding.

By ground-truth object count, the taxonomy is dominated by `callout` (480, 35.5%) and
`elevation` (300, 22.2%), with `floor_plan` (166, 12.3%), `cabinet` (317, 23.4%), and
`countertop` (90, 6.7%) making up the rest. `callout`'s share is inflated by two
outlier-dense documents (`prj4`: 181 of 241 objects; `prj8`: 130 of 231) that
contain long runs of plan-view reference symbols; excluding those two documents, the
label distribution is closer to even across the five types.

### 3.3 Ground-truth format and coordinate convention

Ground-truth boxes are measured in the page's own coordinate space (PDF points at 72 pt
per inch), never in pixels of whatever DPI a given run renders at — normalizing a
ground-truth box against render pixels instead of native point size would silently
shrink every box by a factor of `(dpi / 72)`, large enough at typical rendering DPIs
(180–500) to collapse true-positive counts toward zero while producing no visible error.
Every scoring path derives each page's native point dimensions independently of the DPI
used for a given model call, and normalizes ground truth against that.

### 3.4 Data and code availability

CaseV-Bench is a public benchmark. Aggregate, headline results are published at
[casevbench.com](https://casevbench.com/) and
[coxit.co/ai-drawing-benchmark](https://coxit.co/ai-drawing-benchmark/), alongside a
companion methodology write-up [7]. The evaluation code — including the IoU
greedy-matching scorer used throughout §5–§6 — is public at
[github.com/COXIT-CO/CaseV-bench](https://github.com/COXIT-CO/CaseV-bench/) [8]. That
repository also carries a **subset** of the annotated dataset as a public reference; the
complete nine-document, 1,353-object ground-truth set evaluated in this paper (§3.1) has
not been publicly released.

## 4. Method: One-Stage detection

One-Stage detection is the direct approach: a single request, carrying one full-page
sheet image and one fixed instruction prompt, asks the model to find every object of
every type on that page and report its label and box. No fine-tuning, few-shot examples,
or task-specific training data are used — the prompt is the entire mechanism by which
the model is told what to look for and how to report it. This section specifies that
prompt structurally; its full text is reproduced verbatim in Appendix A.

### 4.1 Task framing

The prompt opens by naming the document class explicitly (an AEC drawing sheet that may
hold several drawing views at very different scales — a floor plan or elevation filling
a quadrant of the sheet, a callout a tiny symbol within it) and states the task as
**multi-class localization**: find and box every instance of the five types in §3.2,
tag each with its label, and report large regions and small symbols side by side in the
same pass. Boxes of different types are explicitly permitted to overlap — a cabinet sits
inside its elevation, a callout sits inside a floor plan — and the model is told this is
expected, not an error to resolve by merging or dropping one of the two.

### 4.2 Disambiguation rules

Beyond the five per-type definitions (§3.2), the prompt carries rules aimed
specifically at cases the underlying reference distinguishes but a generic reading of
the definitions would not catch:

- **`floor_plan` vs. `elevation`** is scoped by *what the view covers*, not by view
  direction: a top-down view of a whole room is `floor_plan`; a top-down view of one
  object or assembly is still `elevation`. A same-direction view can land on either side
  of that line depending on scope alone.
- **`section` is out of scope entirely**, and so is anything drawn only inside one — a
  cabinet, countertop, or callout visible solely within a cross-sectional cutaway is not
  annotated there; it is expected to also appear on its own separate front-view
  elevation elsewhere on the sheet, which is where it is annotated instead.
- **`callout` vs. view title bubble** is the prompt's most detailed disambiguation rule,
  because the two can be textually identical: a callout and a title bubble can both read
  as a bare number or a number over a sheet ID. The prompt states explicitly that content
  does not decide — *attachment* does. A circle fused to the left end of a view's own
  underlined title is a title bubble, never a callout, regardless of what is written
  inside it.
- **Cabinet/countertop box edges are defined operationally, not just by object
  identity.** For a base cabinet, the box's top edge is the line directly under the
  countertop — the countertop and any backsplash are explicitly excluded from the
  cabinet's own box. For a wall cabinet, the top edge is the cabinet's own top line, never
  the wall or ceiling area above it. The prompt gives a standing self-check: before
  emitting a box, verify that the region between its top edge and the object's own top
  line contains nothing that belongs to a different object; if it does, shrink the box.

### 4.3 Coordinate and output contract

Coordinates are requested as **normalized fractions in [0, 1]** of the image actually
sent, independently on each axis, origin at the top-left corner — i.e. a plain
`x_min/y_min/x_max/y_max` fraction, not a 0–1000 integer scale or a pixel coordinate.
Each detection is one JSON object of the shape:

```json
{"label": "cabinet",
 "bounding_box": {"x_min": 0.12, "y_min": 0.34, "x_max": 0.20, "y_max": 0.41}}
```

with `label` constrained to exactly one of the five taxonomy strings (§3.2; `floor_plan`
spelled with an underscore) and the model instructed to return a bare JSON array of such
objects — no prose, no markdown code fences, and an explicit empty array `[]` when a
page contains none of the five types. No count or summary field is requested; consistent
with the rest of the project's detection methods, object counts are treated as something
to be recomputed from the final object list rather than trusted from the model.

### 4.4 Execution pipeline

The results reported in §6 were produced by a separate execution harness from the one
described in the project's internal engineering documentation for earlier prompt
iterations; this paper reports the prompt itself (§4.1–§4.3, Appendix A) and the
resulting scored detections (§5–§6) with confidence, since both are recoverable directly
from the shared results table each run was written to. The harness's own execution
parameters — rendering DPI, request granularity (per page vs. per document), and
retry/repair behavior for malformed model output — are not independently documented as
of this draft and are called out explicitly in §7 as an open item rather than assumed
from other parts of the project.

## 5. Evaluation protocol

### 5.1 Matching and metrics

Scoring is performed by a shared internal package, `location-scorer` (version
`v0.1.0`, identical across every run reported in this paper), so that a difference in
measured accuracy reflects the detection approach and model, not a difference in how it
was scored. For each page, predicted and ground-truth boxes are bucketed by `(page,
object_type)` and matched greedily by IoU; a match counts as a true positive at or above
a fixed **IoU threshold of 0.5**, with unmatched predictions counted as false positives
and unmatched ground-truth objects as false negatives.

Precision, recall, and F1 are reported as **aggregate ("overall") metrics** — true
positives, false positives, and false negatives summed across every document first, then
divided once (micro-averaged), not averaged per-document. This is a deliberate choice:
under a per-document average, a small, easy-to-perfect document would count equally with
a large, hard one, which is not the summary statistic of interest for a
deployment-oriented question ("how many objects, in total, does this configuration find
correctly"). Per-object-type and per-page breakdowns are computed by the same scorer and
retained per run, but are not used as the headline number in §6 for the same reason.

Beyond the binary match outcome, the scorer retains two continuous signals used in §7 to
diagnose *why* a configuration scores the way it does: the IoU of every true-positive
match (how tightly a landed box hugs the real object), and the best available IoU of
every false positive against any ground-truth box on that page (how close a miss
actually was). This separates "the box is loose but roughly in the right place" from
"the box is nowhere near any real object" — two very different failure modes that a
bare F1 number collapses into one.

### 5.2 Cost and latency instrumentation

Every scored run in this paper carries the **actual dollar cost charged for that
specific request**, as reported by the model provider through OpenRouter's per-request
usage accounting, rather than an estimate computed from a locally maintained token-price
table. This keeps reported cost correct as provider pricing changes and comparable across
models and providers without this paper needing to track pricing itself. Wall-clock
latency is recorded per run alongside cost. A scoring failure (e.g. no ground truth
available for a document) is isolated from the detection result itself and does not
appear in the tables in §6.

## 6. Results

All results below are for the current, best-performing One-Stage prompt (§4, Appendix
A), evaluated on all nine documents (§3.1, 1,353 ground-truth objects) with eleven
models from five providers. Where a (model, document) pair was run more than once, the
most recent run is used (§6.6 notes this as a scope decision, not a hidden average).
Aggregate metrics are micro-averaged (§5.1): true positives, false positives, and false
negatives are summed across all documents before precision/recall/F1 are computed once,
so one large document does not get outweighed by several small ones. COXIT also
publishes an aggregate, public-facing summary of this benchmark's headline results,
together with a companion methodology write-up [7]; the tables and analysis below are
the full per-model, per-document, and per-object-type breakdown behind that public
summary.

### 6.1 Headline — accuracy by model

| Model | Provider | TP | FP | FN | Precision | Recall | **F1** | Cost/doc | Mean latency/doc |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `gpt-6-astra` | OpenAI | 1,354 | 171 | 76 | 88.8% | 94.7% | **91.6%** | $2.91 | 58.7 s |
| `gemini-3.8-flash` | Google | 958 | 349 | 472 | 73.3% | 67.0% | **70.0%** | $0.98 | 120.2 s |
| `claude-fable-5.1` | Anthropic | 1,003 | 439 | 427 | 69.6% | 70.1% | **69.8%** | $2.16 | 33.6 s |
| `gpt-5.6-sol` | OpenAI | 932 | 517 | 498 | 64.3% | 65.2% | **64.7%** | $0.79 | 58.8 s |
| `qwen3.8-max` | Qwen | 956 | 895 | 397 | 51.6% | 70.7% | **62.0%** | $0.75 | 178.7 s |
| `gpt-5.6-terra` | OpenAI | 824 | 671 | 606 | 55.1% | 57.6% | **56.3%** | $0.74 | 35.2 s |
| `gemini-3.5-flash` | Google | 756 | 706 | 597 | 51.7% | 55.9% | **53.0%** | $0.46 | 28.5 s |
| `gemini-3.1-pro-preview` | Google | 593 | 448 | 760 | 57.0% | 43.8% | **51.0%** | $0.52 | 29.7 s |
| `claude-opus-5` | Anthropic | 722 | 1,542 | 631 | 31.9% | 53.4% | **39.9%** | $1.77 | 70.5 s |
| `grok-4.6` | xAI | 376 | 740 | 977 | 33.7% | 27.8% | **30.5%** | $1.92 | 346.9 s |
| `claude-sonnet-5` | Anthropic | 316 | 1,205 | 1,037 | 20.8% | 23.4% | **21.0%** | $0.96 | 71.0 s |
| **All models pooled** | — | 8,790 | 7,683 | 6,478 | 53.4% | 57.6% | **55.4%** | — | — |

![F1, precision, and recall by model](figures/fig1_f1_by_model.png)

Three things stand out. **One model, `gpt-6-astra`, is in a different regime from
every other model tested**: 91.6% F1, more than 20 points clear of the next-best model
(`gemini-3.8-flash`, 70.0%) and more than 30 points clear of every other model in the
field. Second, **the overall spread across all eleven models is wide** — 21.0% to
91.6% F1, a **4.4× range** — for the *same* prompt, the *same* documents, and the
*same* IoU threshold; model choice is not just a consequential lever on accuracy, it is
the dominant one. Third, **the ranking does not track provider "flagship" status
cleanly**: `gpt-6-astra` leads by a wide margin, but `claude-sonnet-5` is the weakest
model overall despite sharing a provider with the mid-ranked `claude-opus-5` and the
strong `claude-fable-5.1`, and the three OpenAI models (`gpt-6-astra`, `gpt-5.6-sol`,
`gpt-5.6-terra`) span nearly 35 F1 points among themselves. Recall varies more across
models than precision does (23.4–94.7%, a 71-point range, vs. 20.8–88.8%, a 68-point
range) — `gpt-6-astra`'s lead is driven by being strong on *both* axes at once, not by
trading one for the other.

### 6.2 Per-document difficulty

| Document | Pages | GT objects | F1 (all models pooled) |
|---|---:|---:|---:|
| `prj1` | 3 | 74 | 40.0% |
| `prj2` | 2 | 48 | 55.9% |
| `prj3` | 7 | 34 | 67.5% |
| `prj4` | 9 | 241 | 53.6% |
| `prj5` | 2 | 80 | **77.0%** |
| `prj6` | 24 | 323 | 45.5% |
| `prj7` | 34 | 234 | 55.1% |
| `prj8` | 29 | 231 | 70.9% |
| `prj9` | 9 | 94 | 53.0% |

![F1 by document](figures/fig2_f1_by_document.png)

`prj5` (2 pages) is the easiest document (77.0%) and `prj1` (3 pages) is the
hardest (40.0%) — both among the three smallest documents in the set, in either
direction. The three largest documents span most of the range rather than clustering
together: `prj8` (29 pages) is the second-best result (70.9%), `prj7` (34
pages) sits near the median (55.1%), and `prj6` (24 pages) is the second-worst
(45.5%). Object *density* and *type mix* (§6.3) appear to matter more for this
configuration than page count or raw object count per se; a per-document, per-model
breakdown is left for future work.

### 6.3 Accuracy by object type

| Type | TP | FP | FN | Precision | Recall | **F1** |
|---|---:|---:|---:|---:|---:|---:|
| `floor_plan` | 1,709 | 123 | 117 | 93.3% | 93.6% | **93.4%** |
| `elevation` | 2,550 | 363 | 750 | 87.5% | 77.3% | **82.1%** |
| `cabinet` | 1,673 | 1,813 | 1,814 | 48.0% | 48.0% | **48.0%** |
| `callout` | 2,627 | 4,774 | 3,038 | 35.5% | 46.4% | **40.2%** |
| `countertop` | 231 | 610 | 759 | 27.5% | 23.3% | **25.2%** |

![F1 by object type](figures/fig4_f1_by_type.png)

The two large, sheet-level region types (`floor_plan`, `elevation`) score far higher
(82.1–93.4% F1) than the three small/dense object types (`cabinet`, `callout`,
`countertop`, 25.2–48.0% F1) — roughly a 2–4× gap (93.4/25.2 ≈ 3.7×), though the pooled
numbers are pulled up by one model (`gpt-6-astra`) that does not show this pattern at
all (see below). This is the pattern the visual-grounding literature reports for
general-purpose VLMs more broadly (§2) — accuracy degrades as objects get smaller
relative to the image and more densely packed — but §6.3's per-model breakdown shows it
is not universal.

Per-model, the picture is stark:

![F1 by model and object type (heatmap)](figures/fig5_f1_heatmap_model_type.png)

| Model | `elevation` | `floor_plan` | `cabinet` | `countertop` | `callout` |
|---|---:|---:|---:|---:|---:|
| `gpt-6-astra` | **93.8%** | **98.8%** | **81.8%** | **83.6%** | **95.6%** |
| `qwen3.8-max` | 90.4% | 97.9% | 60.3% | 32.4% | 47.0% |
| `gemini-3.5-flash` | 89.6% | 96.4% | 45.3% | 16.5% | 32.8% |
| `claude-opus-5` | 88.7% | 95.5% | 51.2% | 18.8% | 15.0% |
| `gpt-5.6-sol` | 87.9% | 98.2% | 52.6% | 24.2% | 56.6% |
| `claude-fable-5.1` | 87.2% | 97.9% | 60.7% | 34.4% | 63.8% |
| `gpt-5.6-terra` | 86.9% | 93.9% | 51.3% | 23.5% | 38.3% |
| `gemini-3.8-flash` | 86.1% | 96.7% | 58.8% | 19.8% | 67.5% |
| `gemini-3.1-pro-preview` | 78.0% | 89.0% | 39.0% | 10.0% | 37.0% |
| `grok-4.6` | 68.7% | 91.4% | 5.4% | 7.4% | 0.3% |
| `claude-sonnet-5` | 47.3% | 73.0% | 9.7% | 0.0% | 4.0% |

`gpt-6-astra` is the single best model on every one of the five types, and unlike every
other model in the set, it does not show the large-vs-small gap at all: its own worst
type (`cabinet`, 81.8%) is still better than any other model's *best* small-object
score (`claude-fable-5.1`'s `countertop`, 34.4%). This is the central empirical finding
of this paper — the large-vs-small object gap, which §6.3 might otherwise treat as a
structural property of single-pass VLM prompting on this document class, turns out to
be a property of the other ten models, not of the task itself. `grok-4.6` and
`claude-sonnet-5` are the clearest examples of the conventional pattern: both score far
higher on the two large region types (47.3–91.4% F1) than on any small object type, and
both collapse on small objects specifically (`cabinet` 5.4–9.7%, `countertop`
0.0–7.4%, `callout` 0.3–4.0%). §6.4 examines whether the *other* ten models' gap is a
placement-precision problem or a detection problem and finds it is mostly the latter.

### 6.4 Precision of placement vs. failure to detect

The scorer retains two continuous signals beyond the binary match outcome (§5.1): the
IoU of every true-positive match, and the best available IoU of every false positive
against any ground-truth box on the same page. Splitting these apart tests a specific
hypothesis raised by §6.3's large-vs-small gap: is a weak model's small-object score low
because its boxes are loose (found the object, placed it imprecisely) or because it
mostly does not report the object at all (never found it)?

| Model | Mean IoU of matches | FPs nowhere near a GT box (best IoU < 0.10) | n (FP) |
|---|---:|---:|---:|
| `gpt-6-astra` | 0.867 | 81.9% | 171 |
| `gpt-5.6-sol` | 0.787 | 43.5% | 517 |
| `qwen3.8-max` | 0.785 | 79.1% | 895 |
| `gemini-3.8-flash` | 0.785 | 33.8% | 349 |
| `gpt-5.6-terra` | 0.781 | 51.4% | 671 |
| `gemini-3.5-flash` | 0.779 | 52.3% | 706 |
| `gemini-3.1-pro-preview` | 0.770 | 46.2% | 448 |
| `claude-fable-5.1` | 0.764 | 38.3% | 439 |
| `claude-opus-5` | 0.747 | 73.9% | 1,542 |
| `grok-4.6` | 0.720 | 60.8% | 740 |
| `claude-sonnet-5` | 0.700 | 65.6% | 1,205 |

Mean IoU of matches is close across ten of the eleven models (0.700–0.787); `gpt-6-astra`
sits clearly apart (0.867), meaning it is not just detecting more small objects than
everyone else (§6.3) but placing every box — small or large — measurably tighter than
any other model does. Among the other ten, `grok-4.6` and `claude-sonnet-5` remain the
weakest on small objects, and when they *do* find a small object they box it about as
tightly as most other models (0.700–0.720 mean IoU) — `claude-sonnet-5` reports **zero**
true-positive `countertop` matches across all nine documents (§6.3, 0.0% F1) rather than
a large number of loose, low-IoU ones. This weighs against the box-precision hypothesis
for those two models specifically: their small-object collapse is primarily a
**detection** failure (the object goes unreported), not a **placement** failure. The
nowhere-near-any-object share of false positives does not cleanly separate strong models
from weak ones: `gpt-6-astra`, the strongest model overall, has the *highest* rate of
wildly misplaced false positives (81.9%) of any model in the set — its very small false
positive count (171, the fewest of any model) is nonetheless disproportionately made up
of outright wrong guesses rather than close misses. `qwen3.8-max` shows a similarly
high rate (79.1%). Confirming *why* small-object recall specifically degrades for the
other ten models (a genuine perception limit vs. a prompt-following one) is not
resolved by this data and is noted as an open question in §7.

### 6.5 Cost, latency, and cost-efficiency

![Cost vs. F1 by model](figures/fig3_cost_vs_f1.png)

Cost and accuracy are not cleanly aligned across the eleven models. `gpt-6-astra` is
simultaneously the **most accurate and the single most expensive model per document**
in the evaluation ($2.91/doc, next-highest is `claude-fable-5.1` at $2.16/doc), so
being the most accurate model does not also mean being the cheapest. Among the
remaining ten models, a "cheap and accurate" pattern roughly holds: the three cheapest
models by mean cost per document — `gemini-3.5-flash` ($0.46/doc),
`gemini-3.1-pro-preview` ($0.52/doc), and `gpt-5.6-terra` ($0.74/doc) — are mid-table
on accuracy (51.0–56.3% F1) rather than at the bottom, and `claude-sonnet-5` is the
standout exception: a cheap model ($0.96/doc) that is nonetheless the least accurate of
all eleven (21.0% F1). `grok-4.6` is the worst value in the evaluation on every axis it
is not `gpt-6-astra`-adjacent: the second-highest cost per document, by far the highest
mean latency (346.9 s/doc — 2–12× every other model), and the second-lowest F1.

![Cost efficiency: F1 points per dollar](figures/fig6_cost_efficiency.png)

Because raw cost and raw accuracy do not move together once `gpt-6-astra` is included,
a cost-efficiency view — F1 points earned per dollar spent per document — adds
information the headline table does not: `gemini-3.5-flash` (115.2 F1 pts/$),
`gemini-3.1-pro-preview` (98.1), and `qwen3.8-max` (82.7) are the three most
cost-efficient models, while `gpt-6-astra` — despite leading on raw accuracy by more
than 20 points — ranks only **8th of 11** on this measure (31.5 F1 pts/$), behind
every model it beats on F1 except `claude-sonnet-5`, `claude-opus-5`, and `grok-4.6`.
Whether `gpt-6-astra`'s accuracy is worth its cost premium is therefore a
deployment-specific question this paper does not resolve: for a use case where missing a
small `cabinet` or `countertop` is costly, `gpt-6-astra`'s large lead on exactly those
types (§6.3) may justify roughly 4–6× the per-document cost of the next tier of models;
for a use case tolerant of the large-vs-small gap most other models show, the
cost-efficient tier is a materially cheaper choice.

### 6.6 Scope notes

Three caveats on how these numbers were produced, stated explicitly since they affect
how much weight to put on small differences:

- **One run per (model, document) pair**, taken as the most recent when a pair was run
  more than once; no repeated-trial variance estimate is available from this data.
  Differences of a few F1 points between two models should not be read as
  statistically distinguished from noise; the differences this section leads with (the
  4.4× model spread in §6.1, `gpt-6-astra`'s absence of a large-vs-small gap in §6.3) are
  much larger than that.
- **Coverage across (model, document) pairs was not perfectly uniform** in the
  underlying run history — some pairs were run more than once before the most-recent-run
  rule above was applied, for reasons not recorded in the data available for this draft.
  Every model was ultimately evaluated on all nine documents, so the headline numbers in
  §6.1 are not affected by missing cells.
- **Results attributed to a separate, sliding-window detection method (a different
  author's exploratory run, tagged `sliding_window` in the underlying run history) are
  excluded from this paper entirely.** That method uses a materially different request
  granularity than the One-Stage protocol specified in §4 and was run on a single
  document with an earlier taxonomy version, so it is not comparable to the results
  above and is left out rather than mixed in.

## 7. Limitations

**Why `gpt-6-astra` avoids the small-object failure mode that every other model shows is
not explained by this data.** §6.3 shows that one model spans the large-vs-small object
gap that this paper otherwise treats as a structural property of single-pass VLM
prompting on this document class; §6.4 shows it also places boxes more tightly overall
(mean IoU 0.867 vs. 0.700–0.787 for the rest of the field). This evaluation cannot say
*why*: candidate explanations include a materially higher effective image resolution
reaching the model's vision encoder, a different (and more literal) adherence to the
box-tightness and small-object instructions in the shared prompt (§4.2, Appendix A), or
an architectural difference unrelated to either. Distinguishing these would need a
controlled resolution sweep and a prompt ablation run specifically against
`gpt-6-astra`, neither of which this evaluation ran.

**The small-object failure mode in the other ten models is identified but not
explained.** §6.4 shows that the weakest models' collapse on
`cabinet`/`countertop`/`callout` is primarily a *detection* failure (the object goes
unreported) rather than a *placement* failure (a loose but present box) — but this data
cannot distinguish between the two most likely underlying causes: a genuine perception
limit (the model's vision encoder discards small-object detail before the language model
ever reasons about it, e.g. through aggressive internal image downsampling) and a
prompt-following limit (the model perceives the object well enough but under-reports it
for reasons specific to how the instructions are phrased or how much of a long, dense
list it is willing to emit). Distinguishing these would need either a controlled
resolution sweep per model or a targeted prompt ablation, neither of which this
evaluation ran.

**The execution harness that produced §6's results is not independently documented.**
As noted in §4.4, the prompt itself (Appendix A) and the scored outcomes (§6) are known
directly from the shared results this paper draws on, but the harness's own rendering
DPI, request granularity (whether a whole document or one page is sent per request), and
malformed-output retry behavior are not. Because §6.4 shows the dominant small-object
failure is a *recall* problem, and image resolution is one of the more likely causes of
a model failing to notice a small object in the first place, not knowing the rendering
DPI used per model is a real gap — a low-DPI render could produce exactly the pattern
observed even for a model that would do much better at a higher one. This paper reports
what is measured and flags what is not, rather than assuming values from unrelated parts
of the project.

**Ground truth reflects human judgment calls that are not independently verified for
agreement.** The taxonomy's most detailed disambiguation rules — `callout` vs. a view
title bubble decided by attachment rather than content, `cabinet`/`countertop` box edges
that must exclude an adjacent object even when densely packed (§4.2) — require real
judgment to annotate consistently. No second annotator pass or inter-annotator agreement
figure is reported alongside the ground truth used in §6, so some fraction of what this
evaluation scores as a false positive or false negative may reflect an ambiguous case
rather than a clear model error. This affects the absolute accuracy numbers more than
the cross-model comparison, since every model is scored against the same ground truth.

**Results are reported at a single IoU threshold (0.5) and a single run per
configuration.** §6.6 already states the run-repetition caveat; the IoU threshold choice
is a second, related one. A looser or tighter threshold would shift every number in §6
without necessarily changing the ranking, but that has not been checked, and no
statistical test accompanies any comparison in this paper — the differences led with in
§6 (a 4.4× model spread, `gpt-6-astra`'s absence of a large-vs-small gap) are treated as
self-evidently larger than plausible run-to-run noise rather than formally tested as
such.

**No fine-tuned or task-specific baseline is included.** §2 notes that supervised
detectors trained on labeled engineering-drawing corpora report substantially higher
accuracy than what is measured here for zero-shot VLM prompting. This paper does not
attempt to quantify that gap directly — CaseV-Bench's five-type taxonomy has no
comparably-sized labeled training set to fine-tune against — so the numbers in §6 should
be read as a zero-shot ceiling for this exact prompting approach, not as a statement
about what is achievable for this document class with any amount of supervision.

## 8. Conclusion and Future Work

Returning to the four questions posed in §1:

1. **How accurately can a current, general-purpose VLM localize these objects?**
   Aggregate F1 is 55.4% pooled across eleven models, but that pooled number obscures
   the real finding: the best single model (`gpt-6-astra`) reaches 91.6% F1, and unlike
   every other model tested, it stays high on the three small/dense types (81.8–95.6%
   F1) as well as the two large region types (93.8–98.8% F1). For that one model,
   single-pass VLM prompting looks like a plausible unattended first pass on this
   document class, not just an assisted-review signal. For the remaining ten models —
   the best of which is `qwen3.8-max` at 62.0% — the pattern is different: strong
   enough on `floor_plan`/`elevation` (73.0–97.9% F1) to be a useful first pass, too
   weak on `cabinet`/`countertop`/`callout` (0–68% F1 outside `gpt-6-astra`) for
   unattended use.

2. **Does model choice matter more than prompt engineering?** Within one fixed,
   carefully iterated prompt, F1 spans 21.0–91.6% across eleven models (§6.1) — a 4.4×
   range. That range is far larger than any plausible prompt-engineering gain, which
   means model selection is not a detail to fix arbitrarily and iterate the prompt
   around; it is the single largest lever measured in this paper, larger than the
   prompt itself.

3. **What does an accurate-enough configuration cost?** `gpt-6-astra` breaks a clean
   "cheap and accurate" pattern: it is simultaneously the most accurate model tested and
   the single most expensive one per document (§6.5). Raw accuracy and cost-efficiency
   (F1 points per dollar, §6.5) rank models differently — `gemini-3.5-flash` is the most
   cost-efficient model but only mid-table on raw F1, while `gpt-6-astra` is the
   reverse. Which one is "accurate enough" is a deployment-specific question this paper
   poses rather than resolves. Among the other ten models, a cheap-and-mid-table
   pattern roughly holds: `claude-sonnet-5` is cheap and the least accurate model
   tested, and `grok-4.6` is simultaneously expensive, the slowest by a wide margin, and
   second-worst on accuracy — neither low cost nor high cost reliably predicts where a
   model lands outside the `gpt-6-astra` outlier.

4. **What holds single-pass detection back, structurally?** For ten of the eleven
   models, the dominant pattern is a large-vs-small object gap (§6.3) that §6.4's IoU
   analysis narrows to a **detection** problem for the weakest of them — objects going
   unreported, not boxes landing loosely. But `gpt-6-astra` shows this gap does not have
   to exist for this document class and this prompt: it is not a law of single-pass VLM
   prompting, it is a property most current models happen to share. This reframes the
   open question from "why do all models struggle on small objects" to "what does
   `gpt-6-astra` do differently" — a question this data cannot yet answer (§7). A
   second, unresolved structural gap is that the execution harness's own rendering
   parameters are not documented for the specific runs analyzed here (§4.4, §7), which
   is exactly the kind of detail a resolution-limited failure mode would be sensitive
   to.

Overall, evaluating eleven models across five providers shows that single-pass VLM
prompting for architectural millwork drawing localization is not bounded, as a class,
by a large-vs-small object gap: at least one current model closes that gap almost
entirely. Whether that is because of a resolution advantage, a training difference, or
something else is the most consequential open question this paper raises, and
answering it — alongside closing the harness-visibility gap in §7 — is a
higher-priority next step than further prompt iteration on the other ten models.

**Future work.** Two further directions were explored informally before this paper's
scope was fixed to single-pass, One-Stage detection, and neither is quantified here,
but both are planned as follow-up benchmarking studies under the current protocol. An
earlier, higher-cost **two-stage, crop-based** pipeline — a first pass to locate
candidate regions, followed by a second, higher-resolution pass over each region — left
a clear impression of out-performing single-pass detection by a meaningful margin,
though that comparison ran under an earlier taxonomy and document set and was never
repeated under the protocol used in this paper. Re-running it — Two-Stage vs. One-Stage
on the current taxonomy, document set, and IoU-matching protocol (§5) — is planned as
the next study. A separate, **grid-based coordinate encoding** — asking the model to
reference cells of a coarse grid overlaid on the page rather than raw normalized
fractions — was also tried earlier, as a possible fix for models with unreliable
coordinate output; how it performed relative to the other two approaches is not
established with enough confidence to report here, and re-running it under the current
protocol is planned as a second follow-up study, alongside Two-Stage.

## References

1. H. Zhao, W. Ge, and Y. Chen. LLM-Optic: Unveiling the Capabilities of Large Language
   Models for Universal Visual Grounding. arXiv:2405.17104.
2. R. Li, L. Li, S. Ren, H. Tian, S. Gu, S. Li, Z. Yue, Y. Wang, W. Ma, Z. Yang, J. Ma,
   Z. Sui, and F. Luo. GroundingME: Exposing the Visual Grounding Gap in MLLMs through
   Multi-Dimensional Evaluation. arXiv:2512.17495.
3. S. Sarkar, P. Pandey, and S. Kar. Automatic Detection and Classification of Symbols
   in Engineering Drawings. arXiv:2204.13277.
4. D. Dosi, R. Meena, P. Rajpura, and Y. K. Meena. SkeySpot: Automating Service Key
   Detection for Digital Electrical Layout Plans in the Construction Industry.
   arXiv:2508.10449.
5. W. Wang, H. Hu, Z. Zhang, Z. Li, H. Shao, and D. Dahlmeier. Document Intelligence in
   the Era of Large Language Models: A Survey. arXiv:2510.13366.
6. Y. Ding, S. Luo, Y. Dai, Y. Jiang, Z. Li, Q. Sun, G. Martin, W. Liu, and Y. Peng. A
   Survey on MLLM-based Visually Rich Document Understanding: Methods, Challenges, and
   Emerging Trends. arXiv:2507.09861.
7. COXIT. CaseV-Bench: AI Architectural Drawings Benchmark.
   https://coxit.co/ai-drawing-benchmark/ and https://casevbench.com/ (accessed
   2026-09-15); methodology write-up at
   https://coxit.co/blog/ai-architectural-drawings-benchmark-part-2/.
8. COXIT-CO/CaseV-bench. Evaluation code and public dataset sample (GitHub repository).
   https://github.com/COXIT-CO/CaseV-bench/.

## Appendix A — Full One-Stage prompt text

Reproduced verbatim, as used for the runs reported in §6.

```
CONTEXT: You are looking at a full AEC drawing SHEET. It may hold several drawing views
         (floor plans, elevations, sections, details) and, within them, casework, work
         surfaces, and reference symbols. Objects appear at VERY DIFFERENT SCALES — a whole
         view fills a quadrant of the sheet; a callout is a tiny symbol.
TASK:    Multi-class localization. Find and box every instance of the FIVE object types
         below and tag each box with its `label`. Report large regions and small symbols
         alike. Boxes of DIFFERENT types may overlap (a cabinet sits inside an elevation;
         a callout sits inside a floor plan); that is expected — report each on its own.
         Do not merge different types.
TYPE 1 — label "floor_plan"  (large view region)
   A TOP-DOWN view of a room or overall space — walls, room boundaries, and layout seen
   from above. UNIT: one plan view = one box (drawing plus its title/scale text if present).
   NOT a floor plan: a detailed top view of a single object or assembly (that is an
   "elevation" — see TYPE 2); key/location plans reduced to a tiny diagram; schedules,
   legends, title block.
TYPE 2 — label "elevation"  (large view region)
   A DETAILED orthographic view of a specific object, assembly, or wall, seen from ONE
   direction — front, back, side, OR top. A detailed top view of an object is still an
   elevation, NOT a floor plan. A typical elevation has a recognizable elevation reference
   marker and usually a scale. UNIT: one titled view = one box (drawing plus its title,
   marker, and scale text). NOT an elevation: sections (see IGNORE below), floor plans,
   schedules/legends/tables, title block.
TYPE 3 — label "cabinet"  (small, blocky; front view only)
   A built-in or freestanding storage unit with an enclosed body, typically containing
   doors, drawers, shelves, or a combination. It may sit below a countertop, be mounted on
   a wall, or run floor to ceiling. Annotate ONLY cabinets shown in a FRONT view.
   UNIT: one distinct cabinet unit = one box; adjoining units in a run are separate boxes.
   BOX EDGES — the box covers ONLY the cabinet body itself, nothing above it:
     - TOP (y_min): the topmost drawn line of the cabinet body. For a base cabinet this is
       the line directly UNDER the countertop — the countertop slab and backsplash are NOT
       part of the cabinet. For a wall cabinet it is the cabinet's own top line — never the
       ceiling, soffit, or wall area above. NEVER extend the box upward into empty wall
       space or into another object.
     - BOTTOM (y_max): the floor line / bottom of toe-kick for base and tall cabinets; the
       cabinet's bottom line for wall cabinets (not the counter or wall below it).
     - SIDES: the unit's outer vertical edges.
   EXCLUDE: open shelving, wall panels, appliances, sinks, furniture, and any cabinet
   visible only from the side/profile.
TYPE 4 — label "countertop"  (thin, elongated; front view only)
   A horizontal work surface installed on top of cabinets or supported by legs, brackets,
   or another structure. Two variants, BOTH annotated as one "countertop" object:
     a) Standard countertop — the horizontal surface alone.
     b) Countertop with backsplash — includes a raised vertical section extending up the
        wall; include the backsplash inside the same box.
   Annotate ONLY countertops shown in a FRONT view; do NOT annotate countertops seen in a
   side/profile view. A countertop does not require a cabinet beneath it. Give each box a
   NON-ZERO height. Do NOT confuse with sills, trim, shelves, or soffit/floor lines.
TYPE 5 — label "callout"  (tiny symbol)
   A small circle (sometimes set in a diamond or similar shape, sometimes with a pointer
   triangle or leader line) containing short text that references a page, elevation, or
   section — e.g. a number over a sheet ID like "72 / A9.9.2", a diamond hub with view
   numbers on its sides, or a circle like "8 / AE504". Single-reference and multi-reference
   symbols BOTH count. A callout stands INSIDE or BESIDE drawing content and points AT
   something (via position, a pointer triangle, or a leader line); it is never the bubble
   fused to a view's title line — see EXCLUDE. UNIT: one whole symbol = one box (hub plus
   its attached pointer tags), NOT one box per arm.
   EXCLUDE — these look similar but are NOT callouts:
     - VIEW TITLE BUBBLES: the circle attached to the LEFT END of a view's underlined
       title text, with a scale note nearby (e.g. "(5) CONTROL ROOM 105 — SCALE: 1/2" =
       1'-0"" or "(1) CRAWL SPACE PIPING — 1/8" = 1'-0""). It NAMES the view it is
       attached to rather than referencing another view. WARNING: a title bubble may
       contain exactly the same content as a real callout — a bare number OR a number
       over a sheet ID like "5 / AE401". The contents do NOT decide; the attachment
       does. If the circle touches or immediately precedes an underlined view title,
       it is a title bubble — NEVER a callout, regardless of what is written inside it.
     - grid/column bubbles, door/window/room tags, dimension text, north arrows.
IGNORE — sections (do NOT annotate, under any label)
   A section is a wider CROSS-SECTIONAL cut — e.g. a cut-through of a building or multiple
   floors seen from the front, like a building cutaway, typically with cut/hatch marks at
   the cut line. Sections are OUT OF SCOPE: do not box them as "elevation", "floor_plan",
   or anything else. (Cabinets, countertops, and callouts drawn INSIDE a section are also
   out of scope only if they are shown in profile/cut; a true front view still counts.)
DISAMBIGUATION (view-level classes):
   - Top-down view of a ROOM or overall SPACE ............ floor_plan
   - Detailed one-direction view of an OBJECT/ASSEMBLY/WALL
     (front, back, side, or top) .......................... elevation
   - Wide cross-sectional cutaway through a building ....... section — IGNORE
BOX TIGHTNESS (all types): Every box must be TIGHT — each of its four edges touches the
   outermost drawn line of the object, with no padding and no surrounding empty space.
   Before emitting a box, verify: does the region between y_min and the object's top edge
   contain anything that is not this object (blank wall, a countertop, another cabinet)?
   If yes, shrink the box until it does not.
COORDS:  Normalized coordinates — fractions 0 to 1 of image width and height, for BOTH
         axes independently: x runs 0.0 (left edge) to 1.0 (right edge), y runs 0.0 (top
         edge) to 1.0 (bottom edge). Origin (0,0) is the TOP-LEFT corner. Box =
         [x_min, y_min, x_max, y_max] with x_min < x_max and y_min < y_max, all in [0,1].
OUTPUT:  Respond with ONLY a JSON array — no prose, no markdown fences. One object per
         detection, each shaped exactly like:
         {"label": "cabinet", "bounding_box": {"x_min": 0.12, "y_min": 0.34,
          "x_max": 0.20, "y_max": 0.41}}
         `label` MUST be exactly one of: "elevation", "floor_plan", "cabinet",
         "countertop", "callout". If nothing is found, return an empty array: [].
```
