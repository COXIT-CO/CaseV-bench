# Method comparison — One-Stage vs Two-Stage vs Grid

Full evaluation of the three detection approaches across four vision models.

## What was measured

**36 runs** = 4 models × 3 methods × 3 human-labeled PDF sets, one run each.

| | |
|---|---|
| Documents | `prj0001.pdf` (4 pages), `01+3T+MRI+Cabinets+Drawings.pdf` (2 pages), `Attachment+5+Drawings+Cabinetry.pdf` (9 pages) |
| Ground-truth objects | 147 per model per method (70 + 45 + 32) |
| Labels | `elevation`, `cabinet`, `countertop`, `elevation_callout` |
| Match rule | greedy IoU against the human-labeled box on the same page with the same label, correct at **IoU ≥ 0.5** |
| Metrics | **overall** precision / recall / F1, micro-averaged (TP/FP/FN summed across documents, then divided) |
| Cost | real per-request charge reported by OpenRouter, not an estimate from a price list |

All numbers below are aggregate across all object types. Per-type and per-page
breakdowns are kept in each run's `meta.json` if needed later.

Caveat worth stating up front: one run per cell, no repeats. Differences of a
few points are noise; the differences that matter here are much larger than that.

---

## 1. Headline — method ranking

| Method | TP | FP | FN | Precision | Recall | **F1** | Cost (36 runs) | Requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Two-Stage** | 291 | 268 | 297 | 52.1% | 49.5% | **50.7%** | $3.42 | 204 |
| One-Stage | 114 | 432 | 474 | 20.9% | 19.4% | **20.1%** | $1.52 | 60 |
| Grid | 81 | 479 | 507 | 14.5% | 13.8% | **14.1%** | $2.66 | 60 |

Two-Stage finds **2.6×** as many objects correctly as One-Stage and **3.6×** as
many as Grid. Grid finishes last on every single metric while costing 75% more
than One-Stage.

## 2. Method × model

The ranking is not uniform — the choice of model matters as much as the choice
of method.

| Method | Model | TP | FP | FN | Precision | Recall | **F1** |
|---|---|---:|---:|---:|---:|---:|---:|
| **Two-Stage** | **gemini-3.1-pro-preview** | 107 | 63 | 40 | 62.9% | 72.8% | **67.5%** |
| Two-Stage | gemini-3.6-flash | 97 | 81 | 50 | 54.5% | 66.0% | **59.7%** |
| Two-Stage | gpt-5.6-terra-pro | 76 | 38 | 71 | 66.7% | 51.7% | **58.2%** |
| Two-Stage | claude-sonnet-5 | 11 | 86 | 136 | 11.3% | 7.5% | **9.0%** |
| One-Stage | gemini-3.1-pro-preview | 53 | 55 | 94 | 49.1% | 36.1% | **41.6%** |
| One-Stage | gemini-3.6-flash | 45 | 94 | 102 | 32.4% | 30.6% | **31.5%** |
| One-Stage | gpt-5.6-terra-pro | 10 | 128 | 137 | 7.2% | 6.8% | **7.0%** |
| One-Stage | claude-sonnet-5 | 6 | 155 | 141 | 3.7% | 4.1% | **3.9%** |
| Grid | gpt-5.6-terra-pro | 27 | 127 | 120 | 17.5% | 18.4% | **17.9%** |
| Grid | claude-sonnet-5 | 26 | 94 | 121 | 21.7% | 17.7% | **19.5%** |
| Grid | gemini-3.6-flash | 13 | 112 | 134 | 10.4% | 8.8% | **9.6%** |
| Grid | gemini-3.1-pro-preview | 15 | 146 | 132 | 9.3% | 10.2% | **9.7%** |

Two things stand out:

- **Two-Stage improves every model except claude-sonnet-5**, and the best cell in
  the whole matrix (gemini-3.1-pro-preview, F1 67.5%) is more than 3× the best
  Grid cell.
- **Grid inverts the model ranking.** The two Gemini models are the strongest on
  the other two methods and the *weakest* on Grid; GPT and Claude are the
  opposite. Grid does not measure the same ability the other methods do — see §6.

## 3. Model totals (all methods)

| Model | Precision | Recall | F1 | Total cost | Total wall time |
|---|---:|---:|---:|---:|---:|
| gemini-3.1-pro-preview | 39.9% | 39.7% | **39.8%** | $1.42 | 286 s |
| gemini-3.6-flash | 35.1% | 35.1% | **35.1%** | $1.10 | 293 s |
| gpt-5.6-terra-pro | 27.8% | 25.6% | **26.7%** | $3.31 | 608 s |
| claude-sonnet-5 | 11.4% | 9.8% | **10.5%** | $1.76 | 301 s |

`gemini-3.6-flash` reaches 88% of the pro model's F1 at 78% of its cost — on this
task the flash tier is not obviously the wrong choice.

`gpt-5.6-terra-pro` costs **3× the Gemini models** and scores below both.

## 4. Per-document difficulty

| Method | prj1 (4 p, 70 obj) | prj2 (2 p, 45 obj) | prj3 (9 p, 32 obj) |
|---|---:|---:|---:|
| Two-Stage | 43.9% | **61.7%** | 51.3% |
| One-Stage | 15.3% | 20.2% | 31.4% |
| Grid | 11.2% | 14.1% | 21.5% |

Method ranking holds on every document, so the result is not an artifact of one
awkward file. `prj1` is hardest for all three — it is the densest sheet set, with
70 objects over 4 pages.

## 5. Cost and latency

### Per method

| Method | Total cost | Cost / document | **Cost per correct box** | Input tokens | Output tokens | Avg wall / document | Requests |
|---|---:|---:|---:|---:|---:|---:|---:|
| One-Stage | $1.52 | $0.126 | **$0.0133** | 945 k | 60 k | 34.8 s | 60 |
| Two-Stage | $3.42 | $0.285 | **$0.0118** | 2 498 k | 112 k | 35.3 s | 204 |
| Grid | $2.66 | $0.222 | **$0.0328** | 855 k | 235 k | 54.0 s | 60 |

Two-Stage costs 2.25× more per document than One-Stage — but because it is
correct far more often, it is the **cheapest per correct box of the three**. Its
extra spend buys real output rather than just more tokens.

Grid is the opposite: second-most-expensive per document and **2.5× the worst
cost per correct box**. It also produced ~4× the output tokens of One-Stage
(cell lists and per-object fractions are verbose) and is the slowest method by
wall clock despite issuing the same number of requests.

Two-Stage's spend splits **45% stage 1 / 55% stage 2** — the crop pass is not the
dominant cost, even though it issues far more requests (144 vs 60).

### Per method × model — cost per correct box

| Model | One-Stage | Two-Stage | Grid |
|---|---:|---:|---:|
| gemini-3.1-pro-preview | $0.0034 | **$0.0052** | $0.0452 |
| gemini-3.6-flash | $0.0036 | **$0.0051** | $0.0342 |
| gpt-5.6-terra-pro | $0.0821 | $0.0198 | $0.0366 |
| claude-sonnet-5 | $0.0584 | $0.0785 | $0.0212 |

Best value overall: **Two-Stage on either Gemini model, ≈ half a cent per correct
box** — roughly 4× cheaper per correct box than any Grid configuration and 15×
cheaper than Two-Stage on GPT.

### Cost per document ($)

Page counts differ a lot between documents (4 / 2 / 9), which is why per-page
cost below is the fairer comparison.

| Method | Model | prj1 (4 p) | prj2 (2 p) | prj3 (9 p) | Total |
|---|---|---:|---:|---:|---:|
| One-Stage | gemini-3.6-flash | 0.0637 | 0.0350 | 0.0654 | **0.1640** |
| One-Stage | gemini-3.1-pro-preview | 0.0554 | 0.0430 | 0.0830 | **0.1814** |
| One-Stage | claude-sonnet-5 | 0.1340 | 0.0664 | 0.1498 | **0.3502** |
| One-Stage | gpt-5.6-terra-pro | 0.2626 | 0.1862 | 0.3726 | **0.8214** |
| Two-Stage | gemini-3.6-flash | 0.2771 | 0.0841 | 0.1313 | **0.4924** |
| Two-Stage | gemini-3.1-pro-preview | 0.3012 | 0.1081 | 0.1511 | **0.5604** |
| Two-Stage | claude-sonnet-5 | 0.3991 | 0.1622 | 0.3022 | **0.8635** |
| Two-Stage | gpt-5.6-terra-pro | 0.7273 | 0.3427 | 0.4335 | **1.5035** |
| Grid | gemini-3.6-flash | 0.1446 | 0.0703 | 0.2297 | **0.4446** |
| Grid | claude-sonnet-5 | 0.2136 | 0.0901 | 0.2468 | **0.5505** |
| Grid | gemini-3.1-pro-preview | 0.2772 | 0.1650 | 0.2351 | **0.6774** |
| Grid | gpt-5.6-terra-pro | 0.4034 | 0.1781 | 0.4057 | **0.9873** |

Note how Grid's cost barely varies with page count — prj3 has 4.5× the pages of
prj2 but costs only ~2-3× more, because each page is downsized to the same
`max_dim` regardless. One-Stage and Two-Stage scale more directly with content.

### Cost normalized (15 pages per model per method)

| Method | Model | $ / page | $ / request | Requests |
|---|---|---:|---:|---:|
| One-Stage | gemini-3.6-flash | **0.0109** | 0.0109 | 15 |
| One-Stage | gemini-3.1-pro-preview | **0.0121** | 0.0121 | 15 |
| One-Stage | claude-sonnet-5 | 0.0233 | 0.0233 | 15 |
| One-Stage | gpt-5.6-terra-pro | 0.0548 | 0.0548 | 15 |
| Two-Stage | gemini-3.6-flash | 0.0328 | **0.0091** | 54 |
| Two-Stage | gemini-3.1-pro-preview | 0.0374 | **0.0104** | 54 |
| Two-Stage | claude-sonnet-5 | 0.0576 | 0.0176 | 49 |
| Two-Stage | gpt-5.6-terra-pro | 0.1002 | 0.0320 | 47 |
| Grid | gemini-3.6-flash | 0.0296 | 0.0296 | 15 |
| Grid | claude-sonnet-5 | 0.0367 | 0.0367 | 15 |
| Grid | gemini-3.1-pro-preview | 0.0452 | 0.0452 | 15 |
| Grid | gpt-5.6-terra-pro | 0.0658 | 0.0658 | 15 |

Two-Stage issues 3-3.5× the requests but each is much cheaper (a crop is a small
image), so its per-page cost lands at only ~3× One-Stage. Grid's single request
per page is the **most expensive individual request of any method** for three of
four models — a gridded 3072 px sheet plus a long cell-list response.

### Token usage (15 pages per model per method)

| Method | Model | Input | Output | Reasoning |
|---|---|---:|---:|---:|
| One-Stage | gemini-3.1-pro-preview | 48 582 | 7 016 | 0 |
| One-Stage | gemini-3.6-flash | 58 737 | 10 125 | 3 125 |
| One-Stage | claude-sonnet-5 | 115 365 | 11 949 | 874 |
| One-Stage | gpt-5.6-terra-pro | 722 672 | 30 748 | 20 190 |
| Two-Stage | gemini-3.1-pro-preview | 213 548 | 11 108 | 0 |
| Two-Stage | gemini-3.6-flash | 245 529 | 16 550 | 8 821 |
| Two-Stage | claude-sonnet-5 | 393 573 | 7 638 | 531 |
| Two-Stage | gpt-5.6-terra-pro | 1 645 327 | 76 728 | 65 231 |
| Grid | gemini-3.1-pro-preview | 53 166 | 47 586 | 0 |
| Grid | gemini-3.6-flash | 63 702 | 46 541 | 2 838 |
| Grid | claude-sonnet-5 | 123 336 | 30 379 | 1 794 |
| Grid | gpt-5.6-terra-pro | 614 770 | 110 962 | 57 175 |

The single biggest cost driver in the whole benchmark is how `gpt-5.6-terra-pro`
tokenizes images: **722 k input tokens for 15 pages** on One-Stage, where Gemini
needs 49 k — roughly **15×** for the same pages. That, not the per-token price,
is why GPT costs 3× more overall.

Grid is the only method where **output** tokens are comparable to input (47 k out
vs 53 k in for gemini-3.1). Listing occupied cells plus a fraction pair per
object is far more verbose than four numbers, and on a 50×50 grid a single
wide object can span dozens of cells.

### Latency

| Method | Model | Wall / document | Mean / request | Slowest single request |
|---|---|---:|---:|---:|
| One-Stage | gemini-3.1-pro-preview | **14.3 s** | 10.1 s | 20.2 s |
| One-Stage | gemini-3.6-flash | 24.1 s | 18.5 s | 39.1 s |
| One-Stage | claude-sonnet-5 | 27.7 s | 14.9 s | 41.2 s |
| One-Stage | gpt-5.6-terra-pro | 73.0 s | 59.1 s | 91.9 s |
| Two-Stage | claude-sonnet-5 | 26.8 s | **5.9 s** | 32.7 s |
| Two-Stage | gemini-3.6-flash | 27.8 s | **6.3 s** | 25.8 s |
| Two-Stage | gemini-3.1-pro-preview | 29.5 s | **6.7 s** | 26.8 s |
| Two-Stage | gpt-5.6-terra-pro | 56.8 s | 25.0 s | 94.6 s |
| Grid | gemini-3.6-flash | 45.7 s | 16.6 s | 74.5 s |
| Grid | claude-sonnet-5 | 45.8 s | 24.0 s | 47.4 s |
| Grid | gemini-3.1-pro-preview | 51.5 s | 27.9 s | 66.7 s |
| Grid | gpt-5.6-terra-pro | 72.9 s | 48.2 s | 80.0 s |

Two-Stage's many extra requests cost far less wall time than their count
suggests: each crop request is 4-10× faster than a full-page one (≈ 6 s vs
10-59 s), and they run five at a time (`stage2_concurrency = 5`). Raising that
concurrency is the cheapest available latency win.

Grid is consistently the slowest per document despite issuing the same number of
requests as One-Stage — its requests are individually slower (bigger image in,
much longer list out).

Worth noting for interactive use: **wall time per document is dominated by the
slowest single request**, not the average. `gpt-5.6-terra-pro` has a 94.6 s
outlier on Two-Stage, so a "fast on average" configuration can still feel slow.

## 6. Coordinate quality — where Grid actually helps

The headline F1 hides the most interesting result in the benchmark. Grid is not
uniformly bad: it **rescues the two models that are worst at estimating
coordinates, and damages the two that are good at it.**

| Model | One-Stage F1 | Grid F1 | Δ |
|---|---:|---:|---:|
| claude-sonnet-5 | 3.9% | **19.5%** | **+15.6** |
| gpt-5.6-terra-pro | 7.0% | **17.9%** | **+10.9** |
| gemini-3.6-flash | 31.5% | 9.6% | −21.9 |
| gemini-3.1-pro-preview | 41.6% | 9.7% | −31.8 |

For claude-sonnet-5, Grid raised correct detections from **6 to 26** — a 5×
improvement. That is exactly what the method was designed to do: in Grid the
model never emits a coordinate at all. It names occupied cells, and the box is
computed in code from those cells, so an entire class of coordinate failure
becomes structurally impossible.

### Two different kinds of error

Because `location-scorer` stores the IoU of every match and the best available
IoU of every false positive, the two can be separated: **how tight** a box is
when it lands, versus **whether it lands anywhere near** the object.

| Method | Model | Mean IoU of matches | FPs nowhere near (best IoU < 0.10) |
|---|---|---:|---:|
| One-Stage | gemini-3.1-pro-preview | **0.885** | 44% |
| One-Stage | gemini-3.6-flash | 0.789 | 68% |
| One-Stage | claude-sonnet-5 | 0.775 | 81% |
| One-Stage | gpt-5.6-terra-pro | 0.682 | 66% |
| Two-Stage | gemini-3.1-pro-preview | **0.893** | 44% |
| Two-Stage | gemini-3.6-flash | 0.853 | 44% |
| Two-Stage | gpt-5.6-terra-pro | 0.847 | **26%** |
| Two-Stage | claude-sonnet-5 | 0.624 | 53% |
| Grid | gemini-3.6-flash | 0.703 | 77% |
| Grid | claude-sonnet-5 | 0.693 | 81% |
| Grid | gpt-5.6-terra-pro | 0.659 | **42%** |
| Grid | gemini-3.1-pro-preview | 0.633 | 59% |

| Method | Mean IoU of matches | FPs nowhere near |
|---|---:|---:|
| Two-Stage | **0.857** | **45%** |
| One-Stage | 0.823 | 69% |
| Grid | 0.672 | 63% |

Read together, these say something the F1 column alone does not:

- **Grid's boxes are the loosest of any method** (mean IoU 0.672 vs 0.823 for
  One-Stage). This is inherent: a box derived from grid cells snaps to cell
  edges, so it can never hug an object that doesn't happen to align with the
  grid. Grid buys reliability of *placement*, not precision of *outline* — and
  at IoU ≥ 0.5 that looseness costs matches on small objects.
- **But Grid reduces catastrophic misplacement where it matters.** For
  `gpt-5.6-terra-pro`, false positives that land nowhere near any real object
  fall from 66% to 42%. Its boxes stop being scattered at random.
- **For models already good at coordinates, Grid only takes away.**
  `gemini-3.1-pro-preview` has the tightest boxes in the benchmark (0.885 mean
  IoU on One-Stage); forcing it through a 50×50 cell vocabulary throws that
  precision away and replaces it with a line-counting task it does worse.

**So Grid is best understood as a coordinate-error fallback, not a general
method.** If a model cannot reliably emit a box, Grid converts "box lands
anywhere" into "box lands in roughly the right cell", which is a real and
useful gain. If a model *can* emit a box, Grid is strictly worse than simply
asking for one.

## 7. Model input limits — DPI and image size

This is the constraint that shapes the whole comparison, and it differs per
model. Sending a **whole sheet page** in one request, the practical DPI ceiling
is:

| Model | Max DPI for a full page | Notes |
|---|---:|---|
| gemini-3.6-flash / gemini-3.1-pro-preview | **400–500** | the most tolerant of large page images |
| gpt-5.6-terra-pro | **300** | |
| claude-sonnet-5 | **180** | also downsamples internally to ~1568 px on the long edge |

These are not cosmetic settings — they decide how much of the drawing the model
can actually resolve, and they are why the One-Stage runs used a **different DPI
per model** (Gemini 400, GPT 300, Claude 180). Two-Stage and Grid both ran at a
uniform 300 DPI.

Two consequences worth keeping in mind when reading the tables:

- **Above a certain point, more DPI buys nothing.** A model that downsamples an
  incoming image to ~1568 px on its long edge sees the same picture whether the
  sheet was rendered at 180 or 400 DPI — a 42-inch sheet is 7 560 px at 180 DPI
  and is reduced by ~5× either way. Raising DPI mainly raises upload size and
  cost. This is why Claude's 180 DPI cap costs it less than it looks like it
  should — and why it cannot be worked around by rendering higher.
- **Grid's `max_dim` interacts with this.** Grid downsizes to `max_dim` (3072 px
  here) before drawing the grid, so its render DPI barely matters. But for a
  model that then reduces to ~1568 px, everything above that is discarded — the
  same `max_dim` therefore means genuinely different effective resolutions on
  different models. Grid's numbers are not measured on equal footing across
  models, and its 50×50 grid (2 500 cells) is far finer than the resolution some
  of these models retain.

What actually raises effective resolution under a fixed DPI cap is **sending less
of the page per request** — which is exactly what Two-Stage's crop pass does, and
the most likely reason it dominates.

### A caveat on claude-sonnet-5 — likely a prompt problem, not a capability one

Claude's numbers are the weakest everywhere (F1 10.5% overall, 3.9% on One-Stage)
and should **not** be read as a measure of what the model can do.

The observed failure is specific: its Stage 1 responses came back with
coordinates in neither the requested 0–1000 space nor the render's true pixel
space — e.g. `left=1765, right=2140` on a full sheet — producing internally
consistent but wrongly-placed boxes. Values above 1000 then trip the rescaling
safety net in `normalize_box()`, which divides by the page's real pixel width and
collapses everything toward one corner, so a unit error compounds into a
completely wrong result.

**Grid is the evidence that this is fixable.** It is the one method where Claude
never emits a coordinate — it names cells, and the box is derived in code — and
there Claude jumps from F1 3.9% to 19.5%, its best result of the three methods,
with a mean matched IoU (0.693) in line with every other model. A model that
could not see or locate the drawings would not do that. What fails is the
**coordinate-reporting contract**, not the detection.

Two rounds of prompt tightening did not fix it, but that only rules out the
wordings tried, not the approach. Promising directions not yet tested:

- Have the model **declare the pixel frame it used** (`image_width` /
  `image_height` alongside the objects) and normalize in code, instead of asking
  it to normalize. Self-consistent pixel output is something it clearly manages;
  the arithmetic against an image whose size it cannot measure is what it does not.
- Drop the 0–1000 convention for this model and accept plain fractions (0–1), or
  percentages, which carry no scale to get wrong.

Its 180 DPI cap plus internal downsampling to ~1568 px also leaves it seeing the
least detail of any model here, which likely compounds the problem. Treat the
claude-sonnet-5 rows as **unresolved and probably fixable**, not as a verdict.

## 8. Conclusions

1. **Two-Stage is the clear winner.** F1 50.7% vs 20.1% (One-Stage) and 14.1%
   (Grid). It wins on every document and on every model except the one with an
   unresolved coordinate bug. It is also the cheapest per correct box despite
   costing the most per document. The human review checkpoint between stages is
   an extra benefit the numbers do not capture.

2. **Grid loses on aggregate, but it is a genuine fix for a specific failure —
   and the evaluation is now complete.** It ranks last overall on precision,
   recall, F1, cost per correct box and latency, so it is not the method to
   build on. But averages hide its real behaviour (§6): Grid **raised** F1 for
   the two models that estimate coordinates badly (claude-sonnet-5 +15.6,
   gpt-5.6-terra-pro +10.9, and it cut GPT's wildly-misplaced boxes from 66% to
   42%) while **halving** it for the two that estimate them well. Removing the
   coordinate step works exactly as intended where the coordinate step is what
   is broken.

   Its two structural costs are now measured. Boxes derived from cells snap to
   cell edges, giving the loosest boxes of any method (mean IoU 0.672 vs 0.823),
   which at IoU ≥ 0.5 costs matches on small objects. And it swaps one hard
   visual task for another — the model must count grid lines on a dense overlay,
   and at 50×50 (2 500 cells) on a downsampled sheet a one-cell miscount already
   moves a box by 2% of the sheet.

   **Recommendation: don't pursue Grid as the primary method, but keep it as a
   fallback for models that cannot emit reliable coordinates.** If it is
   revisited, the change most likely to help is a much *coarser* grid, matched to
   what the model can actually resolve — the 50×50 grid used here is far finer
   than the ~1568 px some of these models retain.

3. **Model choice matters as much as method choice.** The spread across models
   within Two-Stage (9.0% → 67.5%) is wider than the spread across methods.
   `gemini-3.1-pro-preview` is the best overall; `gemini-3.6-flash` gets within
   ~8 F1 points for ~20% less money and is the better value; `gpt-5.6-terra-pro`
   costs 3× the Gemini models and scores below both.

4. **Resolution is the main bottleneck — but not the only one.** The methods
   that give the model a smaller region per request do better, and the per-model
   DPI limits in §7 cap how much any prompt can achieve. Separately, the
   claude-sonnet-5 result is a *reporting-contract* failure rather than a
   resolution one (see the claude-sonnet-5 caveat in §7), and it is probably recoverable by changing how
   coordinates are requested — so its rows understate the model and the
   One-Stage average along with it.

5. **Absolute accuracy is still low for production.** The best configuration
   reaches F1 67.5% — useful as an assisted-review starting point, not as an
   unattended extraction step. The largest remaining error source is small dense
   objects (`cabinet`, `countertop`) on large sheets.

### Suggested next step

Push the winner rather than the field: Two-Stage on `gemini-3.1-pro-preview`,
with stage 2 crops rendered at a higher DPI. Stage 2 is the only place where DPI
reliably converts into detail the model can use, because a crop is small enough
to survive downsampling — and at 55% of the method's cost it is the part worth
spending on.