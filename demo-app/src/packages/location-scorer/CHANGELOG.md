# Changelog

Semver, tagged `location-scorer-vX.Y.Z`. **Any change to the numbers a given input produces is
breaking** — see [Versioning and releases](README.md#versioning-and-releases).

## 0.1.0 — 2026-07-26

First release.

- `score(predictions, ground_truth, iou_threshold, include_objects=False)` — the entire public
  surface. Pure, no runtime dependencies, Python 3.11+.
- Greedy one-to-one matching, partitioned by `(page, object_type)`, ties broken on lowest input
  index. `iou_threshold` is required and echoed back in the result.
- Overall counts and rates, plus per-`object_type` and per-page breakdowns, micro-averaged.
- `include_objects=True` lists every TP, FP and FN with its input index and, when unmatched, its
  `best_iou`.
- An inverted or zero-area box raises `ValueError` in *ground truth*, and scores as an FP in a
  *prediction*.
- **Known limitation:** a single operating point only — no precision/recall curves and no mAP,
  because there is no confidence field to sweep.
