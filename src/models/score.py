"""Score table — the metric(s) computed for a Result against Ground Truth
(spec: Ground truth & scoring; glossary: Score; ADR 0004).

One row per Result (the comparison unit scores attach to). For **counting** it holds
the two ranking metrics — ``total_absolute_error`` and ``exact_match_count`` (over the
fixed 4-label taxonomy) — plus a ``per_label_json`` breakdown that retains the
predicted total, the ground-truth snapshot, the per-label absolute error, and the
exact-match flag. Storing the GT snapshot means a later metric change is a recompute,
not a re-run (ADR 0004). A Result with no ground truth simply has no Score row, so
"unscored" stays distinct from "scored zero". Kept DB-agnostic per ADR 0007/0008.
"""

from sqlmodel import Field, SQLModel, UniqueConstraint


class Score(SQLModel, table=True):
    __tablename__ = "score"
    __table_args__ = (UniqueConstraint("result_id", name="uq_score_result"),)

    id: int | None = Field(default=None, primary_key=True)
    result_id: int = Field(foreign_key="result.id", index=True)
    # Sum of per-label absolute errors — the default best-first ranking metric (lower
    # is better).
    total_absolute_error: int
    # How many of the taxonomy labels matched the GT total exactly (0..len(taxonomy)).
    exact_match_count: int
    # JSON list of per-label detail: {label, predicted, gt, absolute_error, exact_match}.
    per_label_json: str
