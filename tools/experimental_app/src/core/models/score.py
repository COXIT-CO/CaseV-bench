"""Score table — the metrics computed for a Result against Ground Truth
(spec: Ground truth & scoring; glossary: Score; ADR 0004).

One row per Result (the comparison unit scores attach to), holding IoU@0.5 ``precision`` /
``recall`` / ``f1``, micro-averaged over the per-page, per-label box matches, plus a
``per_label_json`` breakdown that retains the full per-label detail so a later metric change
is a recompute, not a re-run (ADR 0004).

The three metrics are **required**: a Result with no ground truth simply has no Score row —
the scoring service deletes the row rather than nulling its metrics — so a Score row exists
**iff** the Result was scored, and "unscored" stays distinct from "scored zero" (ADR 0032).
Kept DB-agnostic per ADR 0007/0008.
"""

from sqlmodel import Field, SQLModel, UniqueConstraint


class Score(SQLModel, table=True):
    __tablename__ = "score"
    __table_args__ = (UniqueConstraint("result_id", name="uq_score_result"),)

    id: int | None = Field(default=None, primary_key=True)
    result_id: int = Field(foreign_key="result.id", index=True)

    # IoU@0.5 micro-averaged over the per-page, per-label box matches. F1 is the default
    # ranking metric (higher is better).
    precision: float
    recall: float
    f1: float

    # JSON list of per-label detail: {label, tp, fp, fn, precision, recall, f1}.
    per_label_json: str
