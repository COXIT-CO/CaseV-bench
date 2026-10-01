"""Editable prediction JSON — a persisted manual override that never affects the Score
(ADR 0020, ticket 07).

When a location Model returns wrong or malformed boxes, a developer can fix the result JSON
and **redraw** the overlay from the fix. This service is the seam that sets and clears that
override:

- ``set_override`` validates the payload against ``LocationResult`` (taxonomy labels, 0–1
  coords) with a precise error, then stores it on ``Prediction.edited_json`` — the model's
  original ``parsed_json``/``raw_content`` are **left intact** and stay what ``ScoringService``
  ranks (the Leaderboard keeps measuring the model, not the correction).
- ``revert`` clears ``edited_json``, restoring the model's output and its cached overlay.
- ``edited_overlay_png`` renders the edited boxes **on demand** — re-rendering the page at the
  Run's ``(dpi, downsample_px)`` and drawing the edited detections through the shared renderer
  (``overlay_to_png_bytes``), caching nothing. Returns ``None`` when there is no edit, so the
  overlay route falls back to the cached run-time PNG.

The edit is **per-page** (per Prediction).
"""

from pathlib import Path

from pydantic import ValidationError
from sqlmodel import Session, select

from core.models.drawing import Drawing, Page
from core.models.results import LocationResult
from core.models.run import Prediction, Result, Run
from core.services.pdf_processing import render_run_page
from core.utils import overlay_to_png_bytes


class PredictionOverrideError(ValueError):
    """A rejected override, carrying a precise message the route surfaces as a ``400``:
    malformed JSON, a label outside the taxonomy, or an out-of-range coordinate."""


class PredictionOverrideService:
    """Session-scoped set/clear of a Prediction's manual JSON override (ADR 0020)."""

    def __init__(self, session: Session):
        self.session = session

    def set_override(
        self, result_id: int, page_number: int, payload: str
    ) -> Prediction:
        """Validate ``payload`` as a ``LocationResult`` and persist it as this Prediction's
        edit, leaving the model's original output untouched. Raises ``PredictionOverrideError``
        (invalid JSON / bad label / out-of-range coord) with nothing persisted, or
        ``LookupError`` when no such Prediction exists."""
        prediction = self._prediction(result_id, page_number)
        try:
            validated = LocationResult.model_validate_json(payload)
        except ValidationError as exc:
            raise PredictionOverrideError(_first_error(exc)) from exc
        # Store the normalized, validated JSON so the overlay render and JSON view stay
        # consistent — the same shape ``parsed_json`` uses.
        prediction.edited_json = validated.model_dump_json()
        self.session.add(prediction)
        self.session.commit()
        self.session.refresh(prediction)
        return prediction

    def revert(self, result_id: int, page_number: int) -> Prediction:
        """Clear the override, restoring the model's output and its cached overlay. A no-op on
        an already-unedited Prediction. Raises ``LookupError`` like ``set_override``."""
        prediction = self._prediction(result_id, page_number)
        prediction.edited_json = None
        self.session.add(prediction)
        self.session.commit()
        self.session.refresh(prediction)
        return prediction

    def edited_overlay_png(self, result_id: int, page_number: int) -> bytes | None:
        """The edited overlay's PNG bytes, rendered on demand from ``edited_json``, or ``None``
        when the Prediction has no edit (the caller then serves the cached run-time overlay).
        Re-renders the page at the Run's ``(dpi, downsample_px)`` — reusing the run's cached
        render — and draws the edited boxes through the shared renderer, caching nothing.
        """
        prediction = self._prediction(result_id, page_number)
        if not prediction.edited_json:
            return None
        page = self.session.get(Page, prediction.page_id)
        drawing = self.session.get(Drawing, page.drawing_id)
        # The only step that needs the Run: the re-render has to reuse the knobs the Run
        # rendered at, or the edited overlay would not line up with the cached one.
        run = self.session.get(Run, self.session.get(Result, result_id).run_id)
        image_path = render_run_page(
            Path(page.image_path).parent,
            prediction.page_number,
            drawing.source_path,
            run.dpi,
            run.downsample_px,
        )
        detections = LocationResult.model_validate_json(
            prediction.edited_json
        ).detections
        return overlay_to_png_bytes(image_path, detections)

    def _prediction(self, result_id: int, page_number: int) -> Prediction:
        """The Prediction at a (Result, page). Raises ``LookupError`` when there is none at
        that path (→ 404)."""
        prediction = self.session.exec(
            select(Prediction).where(
                Prediction.result_id == result_id,
                Prediction.page_number == page_number,
            )
        ).first()
        if prediction is None:
            raise LookupError(
                f"no prediction for result {result_id} page {page_number}"
            )
        return prediction


def _first_error(exc: ValidationError) -> str:
    """A single precise sentence from a Pydantic ``ValidationError`` — the first error's
    location and message — so the developer sees exactly what was wrong (a bad label, an
    out-of-range coordinate, malformed JSON) without the full multi-error dump."""
    errors = exc.errors()
    if not errors:
        return "edited JSON is not a valid LocationResult"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "payload"
    return f"{location}: {first.get('msg', 'invalid value')}"
