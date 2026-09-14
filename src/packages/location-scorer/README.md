# location-scorer

Pure, dependency-free scoring of localization predictions against ground truth, so every team
scoring MLLM output on drawings gets comparable numbers. It computes; it never stores.

Python 3.11+, no runtime dependencies, no network, no filesystem, no global state.

## Install

```bash
pip install "location-scorer @ git+https://github.com/COXIT-CO/CaseV-bench.git@location-scorer-v0.1.0#subdirectory=src/packages/location-scorer"
```

**Pin the tag, not a branch.** Releases are tagged `location-scorer-vX.Y.Z`, versioned
independently of the application in the rest of that repository.

> **The reproducibility anchor is the version *and* the IoU threshold together.** The threshold
> is a runtime argument with no default, so two teams on the same release can publish different
> numbers from the same data. Record both — `(location-scorer 0.1.0, IoU 0.5)`; either half alone
> states nothing.

## Input

Predictions and ground truth are lists of the same item:

```python
{"object_type": "cabinet", "bbox": [x_min, y_min, x_max, y_max], "page": 1}
```

- **`object_type`** — a match key only, compared for equality. Any string: the library holds no
  taxonomy and validates none, so your labels need not look like anyone else's.
- **`bbox`** — coordinate-space agnostic; the only requirement is that both sides share one
  space. (Teams standardize on 0–1 floats with a top-left origin at the call site; the scorer
  does not enforce it.)
- **`page`** — required on every item. Matching is partitioned by `(page, object_type)`, so a box
  on page 1 can never be credited against one on page 5. Single-page data passes a constant.

`iou_threshold` is **required and has no default**: the same version scores differently at
different operating points, so the choice belongs in every call site and every diff rather than
buried in a signature. It is echoed back in the result, so a stored score carries the operating
point it was computed under.

## Worked example

Four ground-truth objects across two sheets, and four predictions carrying one of each
interesting outcome — an exact hit, a duplicate, a box too loose to clear the threshold, and an
object the model never saw.

```python
from location_scorer import score

ground_truth = [
    {"object_type": "cabinet",    "bbox": [0.10, 0.10, 0.30, 0.30], "page": 1},
    {"object_type": "cabinet",    "bbox": [0.50, 0.10, 0.70, 0.30], "page": 1},
    {"object_type": "countertop", "bbox": [0.10, 0.60, 0.90, 0.70], "page": 1},
    {"object_type": "cabinet",    "bbox": [0.20, 0.20, 0.40, 0.40], "page": 2},
]

predictions = [
    {"object_type": "cabinet", "bbox": [0.10, 0.10, 0.30, 0.30], "page": 1},  # exact hit
    {"object_type": "cabinet", "bbox": [0.50, 0.10, 0.58, 0.30], "page": 1},  # IoU 0.4 — too loose
    {"object_type": "cabinet", "bbox": [0.10, 0.10, 0.30, 0.30], "page": 1},  # duplicate of the first
    {"object_type": "cabinet", "bbox": [0.20, 0.20, 0.40, 0.40], "page": 2},  # exact hit
]                                                                             # countertop: never predicted

score(predictions, ground_truth, iou_threshold=0.5)
```

The result is the full shape, rates shown rounded:

```python
{
  "iou_threshold": 0.5,
  "counts":  {"tp": 2, "fp": 2, "fn": 2},
  "metrics": {"precision": 0.5, "recall": 0.5, "f1": 0.5},

  "per_type": {
    "cabinet":    {"counts": {"tp": 2, "fp": 2, "fn": 1},
                   "metrics": {"precision": 0.5, "recall": 0.667, "f1": 0.571}},
    "countertop": {"counts": {"tp": 0, "fp": 0, "fn": 1},
                   "metrics": {"precision": 0.0, "recall": 0.0, "f1": 0.0}},
  },

  "per_page": [                       # a list sorted by page, never a page-keyed dict
    {"page": 1,
     "counts":  {"tp": 1, "fp": 2, "fn": 2},
     "metrics": {"precision": 0.333, "recall": 0.333, "f1": 0.333},
     "per_type": {"cabinet":    {"counts": {"tp": 1, "fp": 2, "fn": 1}, "metrics": {...}},
                  "countertop": {"counts": {"tp": 0, "fp": 0, "fn": 1}, "metrics": {...}}}},
    {"page": 2,
     "counts":  {"tp": 1, "fp": 0, "fn": 0},
     "metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
     "per_type": {"cabinet": {"counts": {"tp": 1, "fp": 0, "fn": 0}, "metrics": {...}}}},
  ],
}
```

Page 2 is perfect and page 1 is not, which is the question `per_page` exists to answer. Page 1's
four errors come from only three boxes — a too-loose box costing both an FP *and* an FN, a
duplicate, and a countertop nobody predicted. The counts cannot tell those apart;
`include_objects=True` can.

Every level is a `TypedDict` (`ScoreResult`, `Counts`, `Metrics`, `PageScore`, …) and the package
ships `py.typed`, so nested keys autocomplete and misspelled ones are caught. At runtime it is an
ordinary `dict`: `json.dumps(result)` works. `per_page` is a list precisely because JSON keys are
always strings — a dict keyed by an int `page` would come back keyed by `"1"`.

### Per-object breakdown

`include_objects=True` adds an `objects` key listing every outcome one by one. It is **absent,
not `None`**, when not requested, so "not requested" stays distinct from "requested, nothing
found".

```python
"objects": {
  "tp": [{"page": 1, "object_type": "cabinet",
          "prediction_index": 0, "ground_truth_index": 0,
          "prediction": [0.1, 0.1, 0.3, 0.3], "ground_truth": [0.1, 0.1, 0.3, 0.3],
          "iou": 1.0}, ...],
  "fp": [{"page": 1, "object_type": "cabinet", "prediction_index": 1,
          "prediction": [0.5, 0.1, 0.58, 0.3], "best_iou": 0.4},   # just missed the threshold
         {"page": 1, "object_type": "cabinet", "prediction_index": 2,
          "prediction": [0.1, 0.1, 0.3, 0.3],  "best_iou": 1.0}],  # duplicate detection
  "fn": [{"page": 1, "object_type": "cabinet", "ground_truth_index": 1,
          "ground_truth": [0.5, 0.1, 0.7, 0.3], "best_iou": 0.4},  # found it, drew it loosely
         {"page": 1, "object_type": "countertop", "ground_truth_index": 2,
          "ground_truth": [0.1, 0.6, 0.9, 0.7], "best_iou": 0.0}], # never saw it
}
```

Every entry carries its **index in your input list** — the only thing that tells two
byte-identical boxes apart. IoUs are ordinary floats, so compare them with a tolerance.

Every *unmatched* box carries **`best_iou`**: its highest IoU against any same-`(page,
object_type)` box on the other side, matched or not, and `0.0` when there is none.

| | |
|---|---|
| FP, high `best_iou` | a duplicate detection, or a box that just missed the threshold |
| FP, `best_iou` `0.0` | a hallucination |
| FN, high `best_iou` | the model found it and drew a loose box |
| FN, `best_iou` `0.0` | the model never saw it |

It is also how a threshold gets re-anchored empirically: collect the `best_iou` distribution on
real data and set the operating point from a measurement instead of a guess.

## Matching

Stated precisely enough that another implementation, on any stack, produces identical numbers.

1. **Partition** both sides by `(page, object_type)`. Pairs are only formed within a partition.
2. Within a partition, compute **IoU for every (prediction, ground truth) pair**:
   `intersection / (area(p) + area(g) − intersection)`, where an area is
   `max(0, x_max − x_min) × max(0, y_max − y_min)` and the intersection is the area of
   `[max(x_min), max(y_min), min(x_max), min(y_max)]`. **IoU is `0.0` when the union is zero.**
3. **Sort by IoU descending**, ties broken on **lowest prediction index, then lowest ground-truth
   index** — which is what stops a shuffle of identical boxes changing your score.
4. **Walk the sorted list**, taking a pair when **both** its boxes are still free and skipping it
   otherwise. Taking a pair marks both used: matching is strictly **one-to-one**.
5. A pair matches when **`IoU >= iou_threshold`** — at the threshold exactly, it matches — **and
   `IoU > 0`**. The zero floor is not redundant: without it a threshold of `0.0` would credit a
   prediction against a box it misses entirely. The list is descending, so the walk **stops at
   the first pair below the bar**.
6. Unmatched prediction → **false positive**. Unmatched ground truth → **false negative**.

Partitions are independent and may be processed in any order.

**Greedy is deliberate, not an approximation.** It is what COCO / `pycocotools` and essentially
every published detection benchmark does, so these numbers stay comparable with the literature.
Its **known cost**: greedy can under-count true positives relative to optimal (Hungarian)
assignment, because a strong pair can strand a weaker one whose only remaining partner then falls
below the threshold. That holds at any size — a 2×2 counterexample is worked out in ADR 0030 in
the source repository.

## Metrics

`precision = tp / (tp + fp)`, `recall = tp / (tp + fn)`, `f1` = their harmonic mean. **A zero
denominator yields `0.0`**, never an error, so an empty side never crashes a batch. **There is no
metric named "accuracy"** — nothing here would mean what a reader assumes it means.

**Aggregation is micro at every level**: pool the `{tp, fp, fn}` tallies, *then* compute the
rates, never an average of the rates below. A `per_type` entry pools that type across all pages; a
`per_page` entry pools all types on that page. That is why a perfect prediction scores `1.0`
however the labels are distributed.

`per_type` keys are exactly **the types present in predictions ∪ ground truth** — no padding to
any taxonomy, since the library holds none. A caller wanting a fixed row per label pads it.

## Validation is asymmetric

Ground truth is trusted, human-authored input; predictions are untrusted model output.

| Condition | Ground truth | Prediction |
|---|---|---|
| `x_min > x_max` or `y_min > y_max` | `ValueError` | area 0 → IoU 0 → false positive |
| zero width or zero height | `ValueError` | area 0 → IoU 0 → false positive |

Such a ground-truth box can never be matched, so it would otherwise be a permanent, invisible
false negative capping recall below 1.0 with nothing in the numbers explaining why. Validation
runs before any matching and names the index and the condition — `ground_truth[3] has zero width:
x_min and x_max are both 0.42` — so you can find the box in your source data. The same geometry in
a *prediction* is simply a wrong prediction and scores as a false positive: one malformed box from
an unreliable model must not stop a run.

## Versioning and releases

Semantic versioning under tags `location-scorer-vX.Y.Z` — namespaced so they never collide with
the application's own tags in the same repository. One rule overrides the usual reading: **any
change to the numbers a given input produces is breaking**, whatever the diff looks like; a pinned
consumer took this dependency for comparability, not just for an API. While the library is `0.x`
that means a minor bump, and everything else is a patch. Versions are listed in
[CHANGELOG.md](CHANGELOG.md).

**To cut a release:** bump `version` in `pyproject.toml` and add the dated changelog entry, in a
pull request of its own. Once it is on `main` with CI green, tag the merge commit — never a branch
head — and push:

```bash
git tag -a location-scorer-v0.1.0 -m "location-scorer 0.1.0" && git push origin location-scorer-v0.1.0
```

Then prove the tag installs from a directory that is *not* a checkout of this repository, so
nothing resolves against local application code:

```bash
cd "$(mktemp -d)" && python3.11 -m venv .venv && . .venv/bin/activate
pip install "location-scorer @ git+https://github.com/COXIT-CO/CaseV-bench.git@location-scorer-v0.1.0#subdirectory=src/packages/location-scorer"
python -c "import location_scorer as s; print(s.score([], [], iou_threshold=0.5)['counts'])"
```

If that fails, delete and re-cut the tag rather than leave a broken one published — but once
anyone has consumed a tag it is immutable, so fix forward with a patch.

**To take a new version:** bump the tag in the dependency URL, re-lock, install. Read the
changelog entry for anything that moves numbers first, and say so in the pull request if it does —
a leaderboard that moves unannounced reads as a bug in whatever else shipped that week. Never
bundle a version bump with an `iou_threshold` change: they are separately dated decisions
precisely so a score movement can't be mistaken for a port bug.

## Develop

```bash
cd src/packages/location-scorer
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && black --check . && isort --check-only .
```

Use 3.11 — the declared floor, which CI enforces on its own job. `score()`'s docstring is the
contract in code, and the tests go through the public function only, including the worked example
above, whose every quoted number is asserted in `tests/test_score.py` so this file cannot go stale
unnoticed.
