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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

from sqlmodel import Session, select

from models.prompt import Prompt, Task
from models.results import OBJECT_LABELS, LocationResult
from models.run import Prediction, PredictionStatus, Result, Run
from models.score import Score
from services.counting_ground_truth import CountingGroundTruthService
from services.location_ground_truth import LocationGroundTruthService


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


class LocationBox(NamedTuple):
    """A labeled normalized (0-1) box — the shared shape both a predicted detection and
    a ``LocationGroundTruth`` row reduce to, so the pure matcher never touches the ORM.
    """

    label: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


def _iou(a: LocationBox, b: LocationBox) -> float:
    """Intersection-over-union of two normalized boxes; 0 when they don't overlap or
    either is degenerate (zero-area union)."""
    inter_w = max(0.0, min(a.x_max, b.x_max) - max(a.x_min, b.x_min))
    inter_h = max(0.0, min(a.y_max, b.y_max) - max(a.y_min, b.y_min))
    intersection = inter_w * inter_h
    area_a = max(0.0, a.x_max - a.x_min) * max(0.0, a.y_max - a.y_min)
    area_b = max(0.0, b.x_max - b.x_min) * max(0.0, b.y_max - b.y_min)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _match_count(
    predicted: Sequence[LocationBox],
    gt: Sequence[LocationBox],
    iou_threshold: float,
) -> int:
    """True positives among same-label boxes on one page: greedily pair predictions to
    GT boxes best-IoU-first, each box used at most once, counting pairs at or above the
    threshold. Greedy-by-descending-IoU is the standard 1:1 detection match and is exact
    for the small per-page, per-label box counts here."""
    candidates = sorted(
        (
            (_iou(p, g), pi, gi)
            for pi, p in enumerate(predicted)
            for gi, g in enumerate(gt)
        ),
        reverse=True,
    )
    used_pred: set[int] = set()
    used_gt: set[int] = set()
    matched = 0
    for iou, pi, gi in candidates:
        if iou < iou_threshold:
            break  # sorted descending: nothing later can clear the bar either
        if pi in used_pred or gi in used_gt:
            continue
        used_pred.add(pi)
        used_gt.add(gi)
        matched += 1
    return matched


@dataclass(frozen=True)
class LabelLocationScore:
    """One label's IoU-matched tally and derived rates over the whole Result."""

    label: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


def _rates(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """precision / recall / F1 from a tally, each 0.0 when its denominator is 0 (no
    predictions → precision 0; no GT for the label → recall 0)."""
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


@dataclass(frozen=True)
class LocationScore:
    """A location Result's score: per-label IoU@0.5 tallies plus a **micro-averaged**
    Result aggregate (TP/FP/FN summed across every page and label, then rated). Micro so
    a perfect prediction scores 1.0 whatever the label distribution, and a false positive
    on any label is visible in the aggregate."""

    per_label: list[LabelLocationScore]

    @property
    def _totals(self) -> tuple[int, int, int]:
        return (
            sum(ls.tp for ls in self.per_label),
            sum(ls.fp for ls in self.per_label),
            sum(ls.fn for ls in self.per_label),
        )

    @property
    def precision(self) -> float:
        return _rates(*self._totals)[0]

    @property
    def recall(self) -> float:
        return _rates(*self._totals)[1]

    @property
    def f1(self) -> float:
        return _rates(*self._totals)[2]

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "label": ls.label,
                    "tp": ls.tp,
                    "fp": ls.fp,
                    "fn": ls.fn,
                    "precision": ls.precision,
                    "recall": ls.recall,
                    "f1": ls.f1,
                }
                for ls in self.per_label
            ]
        )


def score_location(
    predicted_by_page: Mapping[int, Sequence[LocationBox]],
    gt_by_page: Mapping[int, Sequence[LocationBox]],
    iou_threshold: float = 0.5,
) -> LocationScore | None:
    """Score predicted boxes against location GT, both keyed by page (ADR 0004).

    Predictions are matched to GT **within the same page and label** at IoU ≥
    ``iou_threshold``; unmatched predictions are false positives and unmatched GT boxes
    false negatives. Returns per-label tallies + rates over the fixed taxonomy, or
    ``None`` when the Drawing has no GT at all (unscored, not zero — spec: Runs 33). A
    page with predictions but no GT contributes pure false positives, and vice-versa.
    """
    if not gt_by_page:
        return None

    pages = set(predicted_by_page) | set(gt_by_page)
    per_label: list[LabelLocationScore] = []
    for label in OBJECT_LABELS:
        tp = fp = fn = 0
        for page in pages:
            preds = [b for b in predicted_by_page.get(page, []) if b.label == label]
            gts = [b for b in gt_by_page.get(page, []) if b.label == label]
            matched = _match_count(preds, gts, iou_threshold)
            tp += matched
            fp += len(preds) - matched
            fn += len(gts) - matched
        precision, recall, f1 = _rates(tp, fp, fn)
        per_label.append(
            LabelLocationScore(
                label=label,
                tp=tp,
                fp=fp,
                fn=fn,
                precision=precision,
                recall=recall,
                f1=f1,
            )
        )
    return LocationScore(per_label=per_label)


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


class LocationLeaderboardMetric(str, Enum):
    """The metric a location Leaderboard is ranked by; each is higher-is-better, so a
    plain descending sort is best-first."""

    f1 = "f1"
    precision = "precision"
    recall = "recall"


@dataclass(frozen=True)
class LocationLeaderboardRow:
    """One location Leaderboard line: a prompt-version × model Configuration outcome and
    its IoU@0.5 rates, or ``scored=False`` when the Drawing has no GT yet."""

    result_id: int
    run_id: int
    model: str
    prompt_family: str
    prompt_version: int
    scored: bool
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None


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

    def predicted_boxes_by_page(self, result: Result) -> dict[int, list[LocationBox]]:
        """A location Result's ok Predictions as ``LocationBox`` lists keyed by page id.
        Failed Predictions (no parsed boxes) contribute nothing; the drill-down surfaces
        them. Keying by page id aligns predictions with GT for the per-page match."""
        by_page: dict[int, list[LocationBox]] = {}
        for pred in result.predictions:
            if pred.status != PredictionStatus.ok or pred.parsed_json is None:
                continue
            detections = LocationResult.model_validate_json(pred.parsed_json).detections
            by_page[pred.page_id] = [
                LocationBox(
                    label=d.label,
                    x_min=d.bounding_box.x_min,
                    y_min=d.bounding_box.y_min,
                    x_max=d.bounding_box.x_max,
                    y_max=d.bounding_box.y_max,
                )
                for d in detections
            ]
        return by_page

    def _stage_location_score(
        self, result: Result, gt_by_page: Mapping[int, Sequence[LocationBox]]
    ) -> Score | None:
        """Location counterpart to ``_stage_score``: compute the IoU@0.5 Score against
        ``gt_by_page`` and stage the upsert without committing, or stage the deletion of
        any stale Score and return ``None`` when the Drawing has no GT."""
        computed = score_location(self.predicted_boxes_by_page(result), gt_by_page)
        existing = self.session.exec(
            select(Score).where(Score.result_id == result.id)
        ).first()
        if computed is None:
            if existing is not None:
                self.session.delete(existing)
            return None

        score = existing or Score(result_id=result.id)
        score.precision = computed.precision
        score.recall = computed.recall
        score.f1 = computed.f1
        score.per_label_json = computed.to_json()
        self.session.add(score)
        return score

    def score_location_result(self, result_id: int) -> Score | None:
        """Recompute and upsert a single location Result's Score against current GT.

        Returns the persisted ``Score``, or ``None`` when the Drawing has no location GT
        (the Result is unscored, distinct from a zero score).
        """
        result = self.session.get(Result, result_id)
        if result is None:
            raise ValueError(f"no result with id {result_id}")
        run = self.session.get(Run, result.run_id)
        gt = self._location_gt_boxes(run.drawing_id)
        score = self._stage_location_score(result, gt)
        self.session.commit()
        if score is not None:
            self.session.refresh(score)
        return score

    def _location_gt_boxes(self, drawing_id: int) -> dict[int, list[LocationBox]]:
        """This Drawing's location GT as ``LocationBox`` lists keyed by page id — the
        shape ``score_location`` matches predictions against. Empty when no GT."""
        rows_by_page = LocationGroundTruthService(self.session).boxes_by_page(
            drawing_id
        )
        return {
            page_id: [
                LocationBox(
                    label=row.label,
                    x_min=row.x_min,
                    y_min=row.y_min,
                    x_max=row.x_max,
                    y_max=row.y_max,
                )
                for row in rows
            ]
            for page_id, rows in rows_by_page.items()
        }

    def location_leaderboard(
        self,
        drawing_id: int | None = None,
        metric: LocationLeaderboardMetric = LocationLeaderboardMetric.f1,
    ) -> list[LocationLeaderboardRow]:
        """Build the location Leaderboard: every location Result as a prompt-version ×
        model row, filtered by Drawing, ranked best-first by ``metric``. Mirrors the
        counting board — scores recomputed against current GT on read (ADR 0004), each
        Drawing's GT fetched once, all upserts committed in a single write, and unscored
        Results (no GT) pinned last."""
        stmt = (
            select(Result, Run, Prompt)
            .join(Run, Result.run_id == Run.id)
            .join(Prompt, Run.prompt_id == Prompt.id)
            .where(Run.task == Task.location)
        )
        if drawing_id is not None:
            stmt = stmt.where(Run.drawing_id == drawing_id)

        gt_by_drawing: dict[int, dict[int, list[LocationBox]]] = {}
        rows: list[LocationLeaderboardRow] = []
        for result, run, prompt in self.session.exec(stmt).all():
            if run.drawing_id not in gt_by_drawing:
                gt_by_drawing[run.drawing_id] = self._location_gt_boxes(run.drawing_id)
            score = self._stage_location_score(result, gt_by_drawing[run.drawing_id])
            rows.append(
                LocationLeaderboardRow(
                    result_id=result.id,
                    run_id=run.id,
                    model=result.model,
                    prompt_family=prompt.family,
                    prompt_version=prompt.version,
                    scored=score is not None,
                    precision=score.precision if score else None,
                    recall=score.recall if score else None,
                    f1=score.f1 if score else None,
                )
            )
        self.session.commit()

        return _rank_location(rows, metric)


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


def _rank_location(
    rows: list[LocationLeaderboardRow], metric: LocationLeaderboardMetric
) -> list[LocationLeaderboardRow]:
    """Sort location rows best-first by ``metric`` with unscored rows pinned to the
    bottom. Every location metric is higher-is-better, so the chosen rate is negated to
    make a plain ascending sort best-first; F1 breaks ties (then result id for stability).
    """

    def key(row: LocationLeaderboardRow):
        if not row.scored:
            # A leading 1 keeps unscored rows last whichever metric is chosen.
            return (1, 0.0, 0.0, row.result_id)
        chosen = getattr(row, metric.value)
        return (0, -chosen, -row.f1, row.result_id)

    return sorted(rows, key=key)
