"""Counting scoring + Leaderboard — the first fully-scored, comparable path
(spec: Ground truth & scoring, Leaderboard; ADR 0004; ticket 08).

Two pieces around one pure seam:

- ``score_counting`` (the pure, session-free test seam): given a Result's per-label
  predicted totals (summed across pages) and the Drawing's counting GT totals, returns
  a ``CountingScore`` — per-label absolute error + exact-match flag — or ``None`` when
  no GT exists, so "unscored" stays distinct from "scored zero" (spec: Runs 33).
- ``ScoringService``: sums a Result's ok Predictions into per-label totals, upserts the
  ``Score`` row (recomputed against current GT so a GT edit is reflected without a
  re-run, ADR 0004), and builds the Leaderboard — Results as prompt-version × model
  rows, filtered by Task + Drawing, sorted by a chosen metric best-first.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from sqlmodel import Session, select

from models.prompt import Prompt, Task
from models.results import OBJECT_LABELS
from models.run import Prediction, PredictionStatus, Result, Run
from models.score import Score
from services.counting_ground_truth import CountingGroundTruthService


@dataclass(frozen=True)
class LabelScore:
    """One label's counting score: the summed prediction, the GT total, and the two
    derived metrics (spec: per-label absolute error + exact-match flag)."""

    label: str
    predicted: int
    gt: int
    absolute_error: int
    exact_match: bool


@dataclass(frozen=True)
class CountingScore:
    """A Result's full counting score — one ``LabelScore`` per taxonomy label, plus the
    aggregates the Leaderboard ranks on."""

    per_label: list[LabelScore]

    @property
    def total_absolute_error(self) -> int:
        return sum(ls.absolute_error for ls in self.per_label)

    @property
    def exact_match_count(self) -> int:
        return sum(1 for ls in self.per_label if ls.exact_match)

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "label": ls.label,
                    "predicted": ls.predicted,
                    "gt": ls.gt,
                    "absolute_error": ls.absolute_error,
                    "exact_match": ls.exact_match,
                }
                for ls in self.per_label
            ]
        )


def score_counting(
    predicted: Mapping[str, int], gt: Mapping[str, int]
) -> CountingScore | None:
    """Score summed-across-pages predicted totals against counting GT.

    Returns per-label absolute error + exact-match over the fixed taxonomy, or ``None``
    when no GT has been entered (unscored, not zero — spec: Runs 33). A missing label on
    either side is treated as 0, so a Result that never predicted a label still scores.
    """
    if not gt:
        return None
    per_label = []
    for label in OBJECT_LABELS:
        p = predicted.get(label, 0)
        g = gt.get(label, 0)
        per_label.append(
            LabelScore(
                label=label,
                predicted=p,
                gt=g,
                absolute_error=abs(p - g),
                exact_match=p == g,
            )
        )
    return CountingScore(per_label=per_label)


class LeaderboardMetric(str, Enum):
    """The metric a Leaderboard is ranked by, each best-first in its natural direction:
    fewest total errors, or most exact-matching labels."""

    total_absolute_error = "total_absolute_error"
    exact_match_count = "exact_match_count"


@dataclass(frozen=True)
class LeaderboardRow:
    """One Leaderboard line: a prompt-version × model Configuration outcome and its
    Score, or ``scored=False`` when the Drawing has no GT yet."""

    result_id: int
    run_id: int
    model: str
    prompt_family: str
    prompt_version: int
    scored: bool
    total_absolute_error: int | None = None
    exact_match_count: int | None = None


class ScoringService:
    def __init__(self, session: Session):
        self.session = session

    def predicted_totals(self, result: Result) -> dict[str, int]:
        """Sum a Result's ok Predictions across pages into a per-label Drawing total
        (spec: predictions summed across pages). Failed Predictions contribute nothing;
        the drill-down surfaces them."""
        totals = {label: 0 for label in OBJECT_LABELS}
        for pred in result.predictions:
            if pred.status != PredictionStatus.ok or pred.parsed_json is None:
                continue
            counts = json.loads(pred.parsed_json)
            for label in OBJECT_LABELS:
                totals[label] += int(counts.get(label, 0))
        return totals

    def _stage_score(self, result: Result, gt: Mapping[str, int]) -> Score | None:
        """Compute a Result's Score against ``gt`` and stage the upsert **without
        committing** — the caller commits once so a whole leaderboard build is a single
        write. Returns the staged ``Score`` (its metric fields readable in memory before
        the commit), or ``None`` when there is no GT, staging the deletion of any stale
        Score so a since-cleared GT can't leave a phantom rank."""
        computed = score_counting(self.predicted_totals(result), gt)
        existing = self.session.exec(
            select(Score).where(Score.result_id == result.id)
        ).first()
        if computed is None:
            if existing is not None:
                self.session.delete(existing)
            return None

        score = existing or Score(result_id=result.id)
        score.total_absolute_error = computed.total_absolute_error
        score.exact_match_count = computed.exact_match_count
        score.per_label_json = computed.to_json()
        self.session.add(score)
        return score

    def score_result(self, result_id: int) -> Score | None:
        """Recompute and upsert a single Result's counting Score against current GT.

        Returns the persisted ``Score``, or ``None`` when the Drawing has no counting GT
        (the Result is unscored, distinct from a zero score).
        """
        result = self.session.get(Result, result_id)
        if result is None:
            raise ValueError(f"no result with id {result_id}")
        run = self.session.get(Run, result.run_id)
        gt = CountingGroundTruthService(self.session).get_totals(run.drawing_id)
        score = self._stage_score(result, gt)
        self.session.commit()
        if score is not None:
            self.session.refresh(score)
        return score

    def leaderboard(
        self,
        drawing_id: int | None = None,
        metric: LeaderboardMetric = LeaderboardMetric.total_absolute_error,
    ) -> list[LeaderboardRow]:
        """Build the counting Leaderboard: every counting Result as a prompt-version ×
        model row, filtered by Drawing, ranked best-first by ``metric``. Scores are
        recomputed against current GT on read (ADR 0004), fetching each Drawing's GT once
        and committing all upserts in a single write. Unscored Results (no GT) always sort
        last, so a missing-GT row never outranks a real score."""
        stmt = (
            select(Result, Run, Prompt)
            .join(Run, Result.run_id == Run.id)
            .join(Prompt, Run.prompt_id == Prompt.id)
            .where(Run.task == Task.counting)
        )
        if drawing_id is not None:
            stmt = stmt.where(Run.drawing_id == drawing_id)

        gt_service = CountingGroundTruthService(self.session)
        gt_by_drawing: dict[int, dict[str, int]] = {}
        rows: list[LeaderboardRow] = []
        for result, run, prompt in self.session.exec(stmt).all():
            if run.drawing_id not in gt_by_drawing:
                gt_by_drawing[run.drawing_id] = gt_service.get_totals(run.drawing_id)
            score = self._stage_score(result, gt_by_drawing[run.drawing_id])
            rows.append(
                LeaderboardRow(
                    result_id=result.id,
                    run_id=run.id,
                    model=result.model,
                    prompt_family=prompt.family,
                    prompt_version=prompt.version,
                    scored=score is not None,
                    total_absolute_error=(
                        score.total_absolute_error if score else None
                    ),
                    exact_match_count=(score.exact_match_count if score else None),
                )
            )
        self.session.commit()

        return _rank(rows, metric)


def _rank(
    rows: list[LeaderboardRow], metric: LeaderboardMetric
) -> list[LeaderboardRow]:
    """Sort rows best-first by ``metric`` with unscored rows pinned to the bottom.
    Direction is folded into the key (lower total error, more exact matches) so a plain
    ascending sort yields best-first. Ties fall back to the other metric, then result id
    for a stable order."""

    # Each metric's two components, most-significant first, negated where "more is
    # better" so a plain ascending sort is best-first. The chosen metric leads; the
    # other breaks ties. result_id is the final, stable tie-break.
    fewest_errors = lambda row: row.total_absolute_error  # noqa: E731
    most_matches = lambda row: -row.exact_match_count  # noqa: E731
    order = (
        (fewest_errors, most_matches)
        if metric == LeaderboardMetric.total_absolute_error
        else (most_matches, fewest_errors)
    )

    def key(row: LeaderboardRow):
        if not row.scored:
            # A leading 1 keeps unscored rows last whichever metric is chosen.
            return (1, 0, 0, row.result_id)
        return (0, order[0](row), order[1](row), row.result_id)

    return sorted(rows, key=key)
