"""Salvaged location detections are scored app-wide (ADR 0027, amending 0019; ticket 01).

The scoring gate is *"has usable detections"*, not ``status == ok``: a truncated response
whose surviving boxes were stored in ``parsed_json`` (a salvaged ``error``) contributes to
its Result's Score, so the same numbers appear on the Leaderboard and in the report. A
detection list is valid element by element — each surviving box is a real claim, so
precision holds on the boxes it emitted, and the boxes lost to truncation become false
negatives that recall and F1 already measure.

These drive the session-level ``ScoringService`` seam directly: build a Result with seeded
Predictions, then assert what ``predicted_boxes_by_page`` includes and what the Result
scores against seeded GT.
"""

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.prompt import Prompt
from core.models.results import BoundingBox, LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from core.services.scoring import ScoringService

MODEL = "anthropic/claude-sonnet-4.5"

# One GT box per page: a cabinet at [0.1, 0.1, 0.4, 0.4].
GT_BOX = (0.1, 0.1, 0.4, 0.4)


def _detection(x_min, y_min, x_max, y_max, label="cabinet") -> LocationDetection:
    return LocationDetection(
        label=label,
        bounding_box=BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
    )


def _location_json(*detections) -> str:
    return LocationResult(detections=list(detections)).model_dump_json()


def _seed_drawing_with_pages(session, n_pages: int) -> tuple[Drawing, list[Page]]:
    drawing = Drawing(name="kitchen")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    pages = []
    for n in range(1, n_pages + 1):
        page = Page(
            drawing_id=drawing.id,
            page_number=n,
            image_path=f"/tmp/page_{n}.png",
            width_px=100,
            height_px=100,
        )
        session.add(page)
        pages.append(page)
    session.commit()
    for page in pages:
        session.refresh(page)
    return drawing, pages


def _seed_location_result(session, drawing) -> Result:
    prompt = Prompt(family="boxes", version=1, text="find boxes")
    session.add(prompt)
    session.commit()
    session.refresh(prompt)
    run = Run(
        prompt_id=prompt.id,
        drawing_id=drawing.id,
        status=RunStatus.done,
        dpi=150,
        downsample_px=None,
        max_tokens=4096,
        temperature=None,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    result = Result(run_id=run.id, model=MODEL)
    session.add(result)
    session.commit()
    session.refresh(result)
    return result


def _add_prediction(session, result, page, *, status, parsed_json, parse_error=None):
    session.add(
        Prediction(
            result_id=result.id,
            page_id=page.id,
            page_number=page.page_number,
            status=status,
            raw_content="<raw>",
            parsed_json=parsed_json,
            parse_error=parse_error,
        )
    )
    session.commit()
    session.refresh(result)


def _seed_gt(session, page, *boxes) -> None:
    for x_min, y_min, x_max, y_max in boxes:
        session.add(
            LocationGroundTruth(
                page_id=page.id,
                label="cabinet",
                x_min=x_min,
                y_min=y_min,
                x_max=x_max,
                y_max=y_max,
            )
        )
    session.commit()


# --- predicted_boxes_by_page: the gate is now presence-of-detections, not status ---


def test_salvaged_error_boxes_are_included_regardless_of_status(session):
    drawing, (page,) = _seed_drawing_with_pages(session, 1)
    result = _seed_location_result(session, drawing)
    # A salvaged, non-clean parse: status error, but a valid box survived in parsed_json.
    _add_prediction(
        session,
        result,
        page,
        status=PredictionStatus.error,
        parsed_json=_location_json(_detection(*GT_BOX)),
        parse_error="response was truncated; salvaged intact array elements",
    )

    by_page = ScoringService(session).predicted_boxes_by_page(result)

    # The salvaged error's box contributes despite the error status (ADR 0027).
    assert [b.label for b in by_page[page.id]] == ["cabinet"]


def test_failed_prediction_without_boxes_contributes_nothing(session):
    drawing, (page,) = _seed_drawing_with_pages(session, 1)
    result = _seed_location_result(session, drawing)
    # A total-garbage failure: error with no salvaged JSON at all.
    _add_prediction(
        session,
        result,
        page,
        status=PredictionStatus.error,
        parsed_json=None,
        parse_error="no valid boxes in response",
    )

    by_page = ScoringService(session).predicted_boxes_by_page(result)

    # No parsed_json → nothing to score; the page is absent from the map.
    assert by_page == {}


# --- app-wide scoring: the salvaged Result now scores non-None and ranks by its boxes ---


def test_salvaged_only_result_is_scored_not_unscored(session):
    drawing, (page,) = _seed_drawing_with_pages(session, 1)
    result = _seed_location_result(session, drawing)
    _seed_gt(session, page, GT_BOX)
    # The Result's *only* Prediction is a salvaged error whose box exactly matches GT.
    _add_prediction(
        session,
        result,
        page,
        status=PredictionStatus.error,
        parsed_json=_location_json(_detection(*GT_BOX)),
        parse_error="truncated",
    )

    score = ScoringService(session).score_location_result(result.id)

    # Previously unscored (error-gated); now scored, and a perfect match on the one box.
    assert score is not None
    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.f1 == 1.0


def test_truncated_response_scores_high_precision_low_recall(session):
    # GT has two boxes across two pages; a truncated response emits only the first,
    # correctly. Precision stays perfect (every emitted box is right); recall halves.
    drawing, pages = _seed_drawing_with_pages(session, 2)
    result = _seed_location_result(session, drawing)
    _seed_gt(session, pages[0], GT_BOX)
    _seed_gt(session, pages[1], GT_BOX)
    # Page 1: a clean, correct box. Page 2: truncated before it emitted its box.
    _add_prediction(
        session,
        result,
        pages[0],
        status=PredictionStatus.error,  # whole response truncated → non-clean
        parsed_json=_location_json(_detection(*GT_BOX)),
        parse_error="truncated",
    )

    score = ScoringService(session).score_location_result(result.id)

    assert score is not None
    assert score.precision == 1.0  # the one emitted box is correct
    assert score.recall == 0.5  # 1 of 2 GT boxes found
    assert 0.0 < score.f1 < 1.0


def test_fully_failed_model_scores_f1_zero_against_present_gt(session):
    drawing, (page,) = _seed_drawing_with_pages(session, 1)
    result = _seed_location_result(session, drawing)
    _seed_gt(session, page, GT_BOX)
    # No detections survived at all.
    _add_prediction(
        session,
        result,
        page,
        status=PredictionStatus.error,
        parsed_json=None,
        parse_error="unreadable",
    )

    score = ScoringService(session).score_location_result(result.id)

    # GT present → scored (not None), but nothing matched → F1 0, not unscored.
    assert score is not None
    assert score.f1 == 0.0
    assert score.recall == 0.0


def test_salvaged_result_ranks_on_the_location_leaderboard(session):
    drawing, (page,) = _seed_drawing_with_pages(session, 1)
    result = _seed_location_result(session, drawing)
    _seed_gt(session, page, GT_BOX)
    _add_prediction(
        session,
        result,
        page,
        status=PredictionStatus.error,
        parsed_json=_location_json(_detection(*GT_BOX)),
        parse_error="truncated",
    )

    rows = ScoringService(session).location_leaderboard(drawing.id)

    (row,) = rows
    assert row.scored is True  # salvaged-only Result now ranks, no longer unscored
    assert row.f1 == 1.0
