"""Score table — the metric(s) computed for a Result against Ground Truth
(spec: Ground truth & scoring; glossary: Score; ADR 0004).

One row per Result (the comparison unit scores attach to), holding whichever task's
metrics apply — the other task's columns stay ``None``:

- **Counting** — ``total_absolute_error`` and ``exact_match_count`` (over the fixed
  4-label taxonomy).
- **Location** — IoU@0.5 ``precision`` / ``recall`` / ``f1``, micro-averaged over the
  per-page, per-label box matches.

Either way a ``per_label_json`` breakdown retains the full per-label detail so a later
metric change is a recompute, not a re-run (ADR 0004). A Result with no ground truth
simply has no Score row, so "unscored" stays distinct from "scored zero". Kept
DB-agnostic per ADR 0007/0008.
"""

from sqlmodel import Field, SQLModel, UniqueConstraint


class Score(SQLModel, table=True):
    __tablename__ = "score"
    __table_args__ = (UniqueConstraint("result_id", name="uq_score_result"),)

    id: int | None = Field(default=None, primary_key=True)
    result_id: int = Field(foreign_key="result.id", index=True)

    # Counting metrics (None on a location Score). Sum of per-label absolute errors —
    # the default counting ranking metric (lower is better).
    total_absolute_error: int | None = None
    # How many of the taxonomy labels matched the GT total exactly (0..len(taxonomy)).
    exact_match_count: int | None = None

    # Location metrics (None on a counting Score): IoU@0.5 micro-averaged over the
    # per-page, per-label box matches. F1 is the default location ranking metric
    # (higher is better).
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None

    # JSON list of per-label detail. Counting: {label, predicted, gt, absolute_error,
    # exact_match}. Location: {label, tp, fp, fn, precision, recall, f1}.
    per_label_json: str
