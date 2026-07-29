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

Location scoring lives in the shared ``location-scorer`` library (ADR 0028), consumed
here as a pinned release tag; ``score_location`` keeps its signature and is now the
adapter around it. Counting scoring stays local — absolute error and an exact-match flag
leave no semantic room for two teams to disagree.

Two smaller seams serve the Operating point a downloaded Report has to stamp:
``LOCATION_IOU_THRESHOLD`` (the single threshold every Location score is computed at, and
``score_location``'s default) and ``scorer_version`` (the installed library version, read
from distribution metadata).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version

from location_scorer import score as score_boxes
from sqlmodel import Session, select

from core.models.prompt import Prompt, Task
from core.models.results import OBJECT_LABELS, LabeledBox, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run
from core.models.score import Score
from core.services.counting_ground_truth import CountingGroundTruthService
from core.services.location_ground_truth import LocationGroundTruthService


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


# The pure matcher and Leaderboard work in ``LabeledBox`` (models.results) — the flat
# labeled-box shape shared with the overlay renderer. Kept aliased so scoring code and
# its tests keep referring to it as ``LocationBox``.
LocationBox = LabeledBox


def _scorer_items(boxes_by_page: Mapping[int, Sequence[LocationBox]]) -> list[dict]:
    """Page-keyed ``LocationBox`` lists flattened into the library's item shape — one
    flat sequence whose entries each carry their page, with our label as the
    ``object_type`` match key (the library holds no taxonomy and only tests it for
    equality).

    Off-taxonomy boxes are dropped on both sides, which is what the pre-port
    ``for label in OBJECT_LABELS`` loop did implicitly. It matters because the library
    pools *every* label it is handed into the aggregate while our breakdown only pads out
    the fixed taxonomy: forwarding a stray label would move precision and F1 while being
    invisible in every per-label row, so the two would stop reconciling. ``LocationBox``
    types its label as a plain ``str``, so this is the boundary that holds the taxonomy —
    both current sources (a ``LocationResult`` parsed against the ``ObjectLabel`` literal,
    and the GT importer's own taxonomy filter) already only produce known labels.
    """
    return [
        {
            "object_type": box.label,
            "bbox": [box.x_min, box.y_min, box.x_max, box.y_max],
            "page": page,
        }
        for page, boxes in boxes_by_page.items()
        for box in boxes
        if box.label in OBJECT_LABELS
    ]


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


@dataclass(frozen=True)
class LocationScore:
    """A location Result's score: per-label IoU@0.5 tallies plus a **micro-averaged**
    Result aggregate (TP/FP/FN pooled across every page and label, then rated). Micro so
    a perfect prediction scores 1.0 whatever the label distribution, and a false positive
    on any label is visible in the aggregate.

    The aggregate is a stored field rather than a sum over ``per_label``, because the
    library now owns every rate and the app keeps no second implementation to re-derive it
    with (ADR 0028). Both come out of one ``score()`` call over one set of items, so they
    describe the same tallies — see ``_scorer_items`` for the one boundary that keeps that
    true.

    ``iou_threshold`` is the library's **echo** of the operating point these rates were
    computed at, carried back rather than re-read from a constant: ADR 0030 makes
    ``(scorer version, iou_threshold)`` the reproducibility anchor and has ``score()`` echo
    it "for exactly this reason". A Report stamps this value, so anything that stamped a
    constant instead would keep printing 0.50 beside numbers scored at something else.
    """

    per_label: list[LabelLocationScore]
    precision: float
    recall: float
    f1: float
    iou_threshold: float

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


_EMPTY_TYPE_SCORE = {
    "counts": {"tp": 0, "fp": 0, "fn": 0},
    "metrics": {"precision": 0.0, "recall": 0.0, "f1": 0.0},
}


def _label_score(label: str, per_type: Mapping[str, dict]) -> LabelLocationScore:
    """One taxonomy label's row, padding the library's ``per_type`` out to our fixed
    taxonomy: a label neither predicted nor in GT is absent there, but keeps an all-zero
    row here so the stored breakdown and its drill-down never lose a label."""
    entry = per_type.get(label, _EMPTY_TYPE_SCORE)
    return LabelLocationScore(
        label=label,
        tp=entry["counts"]["tp"],
        fp=entry["counts"]["fp"],
        fn=entry["counts"]["fn"],
        precision=entry["metrics"]["precision"],
        recall=entry["metrics"]["recall"],
        f1=entry["metrics"]["f1"],
    )


# CaseV's single Location Operating point (ADR 0004, parity on adoption — ADR 0030). Named
# rather than inlined so the one place it is chosen is also the one place it is defaulted;
# what gets *displayed* is never this constant but the threshold the library echoed back
# (``LocationScore.iou_threshold``), so a stamp can never disagree with its own numbers.
LOCATION_IOU_THRESHOLD = 0.5

SCORER_DISTRIBUTION = "location-scorer"


def scorer_version() -> str | None:
    """The installed ``location-scorer`` version, read from distribution metadata so it cannot
    drift from the code that actually scored — a literal here would keep printing the old
    version after a dependency bump.

    ``None`` when the library is importable but has no distribution metadata (a
    path/PYTHONPATH install). Scoring is unaffected by that, so a Report drops the version
    clause and keeps the rest rather than failing the download — and it says nothing rather
    than inventing a version it cannot vouch for.
    """
    try:
        return version(SCORER_DISTRIBUTION)
    except PackageNotFoundError:
        return None


def score_location(
    predicted_by_page: Mapping[int, Sequence[LocationBox]],
    gt_by_page: Mapping[int, Sequence[LocationBox]],
    iou_threshold: float = LOCATION_IOU_THRESHOLD,
) -> LocationScore | None:
    """Score predicted boxes against location GT, both keyed by page (ADR 0004).

    A thin adapter over the shared ``location-scorer`` library (ADR 0028), which owns the
    matching and the arithmetic: predictions pair with GT **within the same page and
    label** at IoU ≥ ``iou_threshold``, one-to-one, best-IoU-first; unmatched predictions
    are false positives and unmatched GT boxes false negatives. Everything CaseV-specific
    stays here — flattening the page-keyed maps into library items, padding the per-label
    breakdown back out to the fixed taxonomy (the library holds no taxonomy, so it only
    reports labels actually present), and the guard below.

    Returns per-label tallies + rates plus the micro-averaged aggregate, or ``None`` when
    the Drawing has no GT at all (unscored, not zero — spec: Runs 33; the library instead
    returns a well-formed F1 of 0.0, which would let a missing answer key outrank a real
    Score). A page with predictions but no GT contributes pure false positives, and
    vice-versa.

    ``iou_threshold`` keeps its ``LOCATION_IOU_THRESHOLD`` default even though the library
    deliberately has none ("the choice belongs in every call site and every diff"). CaseV has
    exactly one operating point, fixed by ADR 0004 and stamped on the Report, so a per-call
    choice here would be a second place for it to drift from the one the Leaderboard was built
    on. Re-anchoring it is a dated decision of its own (ADR 0030), never a caller's.

    A degenerate GT box (zero-area or inverted) raises ``ValueError`` from the library —
    nothing can ever match it, so it would otherwise cap recall below 1.0 with nothing in
    the numbers saying why (ADR 0031). The importer rejects such boxes (scope 7, ticket
    01), so a raise here means a store predating that guard. It is deliberately not caught:
    scoring runs per Result inside a Leaderboard build, so this fails the whole board rather
    than quietly ranking one Drawing against a broken answer key. ``python -m core.audit``
    names the offending boxes; the fix is re-importing that Drawing from a corrected source.
    """
    if not gt_by_page:
        return None

    scored = score_boxes(
        _scorer_items(predicted_by_page),
        _scorer_items(gt_by_page),
        iou_threshold=iou_threshold,
    )

    return LocationScore(
        per_label=[_label_score(label, scored["per_type"]) for label in OBJECT_LABELS],
        precision=scored["metrics"]["precision"],
        recall=scored["metrics"]["recall"],
        f1=scored["metrics"]["f1"],
        # The library's echo, not the argument above — so the operating point travels with
        # the rates it produced and a Report can stamp it (ADR 0030).
        iou_threshold=scored["iou_threshold"],
    )


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
        (spec: predictions summed across pages). Counting stays clean-only — the gate is
        ``status == ok`` (ADR 0027): a truncated/partial count is not partly valid, so a
        salvaged ``error`` count is not summed (unlike location boxes — see
        ``predicted_boxes_by_page``). Failed Predictions contribute nothing; the drill-down
        surfaces them."""
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
        prompt_family: str | None = None,
        prompt_version: int | None = None,
    ) -> list[LeaderboardRow]:
        """Build the counting Leaderboard: every counting Result as a prompt-version ×
        model row, filtered by Drawing, ranked best-first by ``metric``. Scores are
        recomputed against current GT on read (ADR 0004), fetching each Drawing's GT once
        and committing all upserts in a single write. Unscored Results (no GT) always sort
        last, so a missing-GT row never outranks a real score.

        Optional ``prompt_family`` / ``prompt_version`` narrow the board to one prompt
        lineage (compare its versions across models) or pin one exact version, applied as
        ``WHERE`` clauses on the Prompt join (spec-run-report, ticket 04)."""
        stmt = (
            select(Result, Run, Prompt)
            .join(Run, Result.run_id == Run.id)
            .join(Prompt, Run.prompt_id == Prompt.id)
            .where(Run.task == Task.counting)
        )
        if drawing_id is not None:
            stmt = stmt.where(Run.drawing_id == drawing_id)
        if prompt_family is not None:
            stmt = stmt.where(Prompt.family == prompt_family)
        if prompt_version is not None:
            stmt = stmt.where(Prompt.version == prompt_version)

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
        """A location Result's Predictions with usable detections as ``LocationBox`` lists
        keyed by page id. The gate is the presence of parsed boxes, not ``status == ok`` (ADR
        0027): a salvaged/truncated ``error`` whose surviving boxes were stored in
        ``parsed_json`` still contributes, so precision holds on the boxes it emitted and
        recall takes the honest hit for the ones it missed. The ok/error flag is now a
        displayed data-quality badge, not a scoring gate. A Prediction with no ``parsed_json``
        (an unsalvageable failure) contributes nothing; the drill-down surfaces it. Keying by
        page id aligns predictions with GT for the per-page match."""
        by_page: dict[int, list[LocationBox]] = {}
        for pred in result.predictions:
            if pred.parsed_json is None:
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
        prompt_family: str | None = None,
        prompt_version: int | None = None,
    ) -> list[LocationLeaderboardRow]:
        """Build the location Leaderboard: every location Result as a prompt-version ×
        model row, filtered by Drawing, ranked best-first by ``metric``. Mirrors the
        counting board — scores recomputed against current GT on read (ADR 0004), each
        Drawing's GT fetched once, all upserts committed in a single write, and unscored
        Results (no GT) pinned last.

        Optional ``prompt_family`` / ``prompt_version`` narrow the board to one prompt
        lineage or pin one exact version, applied as ``WHERE`` clauses on the Prompt join
        (spec-run-report, ticket 04)."""
        stmt = (
            select(Result, Run, Prompt)
            .join(Run, Result.run_id == Run.id)
            .join(Prompt, Run.prompt_id == Prompt.id)
            .where(Run.task == Task.location)
        )
        if drawing_id is not None:
            stmt = stmt.where(Run.drawing_id == drawing_id)
        if prompt_family is not None:
            stmt = stmt.where(Prompt.family == prompt_family)
        if prompt_version is not None:
            stmt = stmt.where(Prompt.version == prompt_version)

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
