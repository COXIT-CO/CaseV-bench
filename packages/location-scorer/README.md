# location-scorer

Pure, dependency-free scoring of localization predictions against ground truth, so every team
scoring MLLM output on drawings gets comparable numbers. It computes; it never stores.

> **Not released yet — no tag to install.** Present so far: page-scoped matching, overall counts
> and rates, the per-type and per-page breakdowns, ground-truth validation, and the per-object
> breakdown. The consumer-facing documentation (full result shape, worked example,
> reimplementation-grade matching rule, release procedure) lands with v0.1.0. Until then, use it
> from a checkout.

## Use

```python
from location_scorer import score

result = score(predictions, ground_truth, iou_threshold=0.5)
result["metrics"]["f1"]                            # overall
result["per_type"]["cabinet"]["metrics"]["recall"] # which label the prompt handles well
result["per_page"][0]["page"]                      # a list sorted by page, never a dict
```

`include_objects=True` adds an `objects` key listing every TP, FP and FN on its own — which
boxes went wrong, not just how many:

```python
result = score(predictions, ground_truth, iou_threshold=0.5, include_objects=True)
result["objects"]["fp"][0]["prediction_index"]     # which input box this outcome came from
result["objects"]["fp"][0]["best_iou"]             # high = duplicate or just-missed, 0.0 = invented
```

Every unmatched box carries `best_iou`: its highest IoU against any same-page, same-type box on
the other side, matched or not. Without it a box that landed just under the threshold and one the
model invented are both "1 FP" and read the same.

Both sides are lists of `{"object_type": str, "bbox": [x_min, y_min, x_max, y_max], "page": int}`,
and `score()`'s own docstring is the contract: the matching rule, the required threshold, and the
traps — including that **empty ground truth returns a well-formed 0.0 rather than an error**, which
a caller that ranks results has to detect rather than publish.

One asymmetry the docstring covers and callers keep hitting: an inverted or zero-area
*ground-truth* box raises `ValueError` naming the index and the condition, while the same geometry
in a *prediction* never raises and simply scores as a false positive.

Python 3.11 or newer, no runtime dependencies. Once v0.1.0 is tagged, consumers pin the release
rather than a branch — `location-scorer @ git+<repo>@location-scorer-v0.1.0#subdirectory=packages/location-scorer`
— because **the reproducibility anchor is the package version *and* the IoU threshold together**.

## Develop

```bash
cd packages/location-scorer
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
black --check . && isort --check-only .
```

Use 3.11 locally: it is the declared floor, and CI enforces it on its own job, independently of
the application's test suite.
