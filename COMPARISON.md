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

### Latency (wall clock per document, seconds)

| Model | One-Stage | Two-Stage | Grid |
|---|---:|---:|---:|
| gemini-3.1-pro-preview | 14.3 | 29.5 | 51.5 |
| gemini-3.6-flash | 24.1 | 27.8 | 45.7 |
| claude-sonnet-5 | 27.7 | 26.8 | 45.8 |
| gpt-5.6-terra-pro | 73.0 | 56.8 | 72.9 |

Two-Stage's many extra requests cost far less wall time than their count
suggests, because crops run concurrently (`stage2_concurrency = 5`). Grid is
consistently the slowest — one large gridded image per page, and a long verbose
response per page.

## 6. Model input limits — DPI and image size

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

### A caveat on claude-sonnet-5

Claude's numbers are the weakest everywhere (F1 10.5% overall, 3.9% on One-Stage)
and should not be read as a clean measure of the model. Its Stage 1 responses
were observed returning coordinates in neither the requested 0–1000 space nor the
render's true pixel space — e.g. `left=1765, right=2140` on a full sheet —
producing internally consistent but wrongly-placed boxes. Two rounds of prompt
tightening did not fix it. Its 180 DPI cap plus internal downsampling leaves it
seeing the least detail of any model here, and on a sheet whose casework occupies
roughly a third of the page it appears to fall back on plausible-looking layouts.
Treat its row as "unresolved", not as a final verdict.

## 7. Conclusions

1. **Two-Stage is the clear winner.** F1 50.7% vs 20.1% (One-Stage) and 14.1%
   (Grid). It wins on every document and on every model except the one with an
   unresolved coordinate bug. It is also the cheapest per correct box despite
   costing the most per document. The human review checkpoint between stages is
   an extra benefit the numbers do not capture.

2. **The grid approach did not work out, and the evaluation is now complete.**
   It ranks last on precision, recall, F1, cost per correct box, and latency.
   The idea — have the model name a grid cell instead of estimating coordinates,
   then derive the box in code — is sound in principle, and it does remove
   coordinate-format errors. But it replaces one hard visual task with another:
   the model must now count grid lines accurately on a dense overlay. At 50×50
   (2 500 cells) on a downsampled sheet, that counting is where it fails, and a
   one-cell miscount moves a box by 2% of the sheet, which at IoU ≥ 0.5 on small
   objects is already a miss. Grid also inverts the model ranking, which suggests
   it is measuring line-counting rather than drawing comprehension.
   **Recommendation: do not pursue it further in this form.** If it is revisited,
   the single change most likely to matter is a much coarser grid matched to what
   the model can actually resolve, not a finer one.

3. **Model choice matters as much as method choice.** The spread across models
   within Two-Stage (9.0% → 67.5%) is wider than the spread across methods.
   `gemini-3.1-pro-preview` is the best overall; `gemini-3.6-flash` gets within
   ~8 F1 points for ~20% less money and is the better value; `gpt-5.6-terra-pro`
   costs 3× the Gemini models and scores below both.

4. **Resolution is the real bottleneck, not prompting.** The methods that give
   the model a smaller region per request do better, and the per-model DPI limits
   in §6 cap how much any prompt can achieve. Prompt tuning did not recover
   claude-sonnet-5.

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