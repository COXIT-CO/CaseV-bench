import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw

from core.models.results import LabeledBox, LocationDetection

# Long-edge (px) each page image is downsampled to at ingest; snapshotted on a Run.
DEFAULT_DOWNSAMPLE_PX = 1568

# Colour the model's predicted boxes are drawn in on the prediction overlay (ticket 09).
# ``red`` reads clearly on a white drawing.
PREDICTION_COLOR = "red"


@dataclass(frozen=True)
class SalvageResult:
    """Outcome of ``salvage_json`` (ADR 0019). ``value`` is the recovered JSON structure
    (a ``dict``/``list``) or ``None`` when nothing could be recovered. ``complete`` is
    True only when the whole response was recovered as valid JSON — tolerating surrounding
    prose/fences, trailing commas and single quotes counts as complete because no data is
    dropped; a truncated/partly-corrupt array reduced to its intact elements does not. The
    caller scores a ``complete`` recovery ``ok`` and keeps a partial one an ``error`` whose
    salvage is shown for display only. ``error`` describes why a recovery was not complete.
    """

    value: object | None
    complete: bool
    error: str | None


def salvage_json(content: str | None) -> SalvageResult:
    """Recover a JSON structure from a model response, ignoring prose/thinking/fences and
    tolerating common malformations (ADR 0019, ticket 03).

    In order: (1) locate the outermost balanced ``{...}``/``[...]`` so surrounding chatter
    is ignored; (2) parse it, retrying after light repairs (drop trailing commas, single →
    double quotes); (3) for a truncated/partly-corrupt **array**, salvage element-by-element
    so the boxes the model did emit survive. Never raises — a total failure is reported as
    ``value=None``.
    """
    if content is None:
        return SalvageResult(None, False, "response content was empty")
    text = content.strip()
    if not text:
        return SalvageResult(None, False, "response content was empty")

    start = _first_bracket(text)
    if start is None:
        return SalvageResult(None, False, "no JSON object or array found in response")

    end = _matching_bracket(text, start)
    truncated = end is None
    candidate = text[start:end] if end is not None else text[start:]

    if not truncated:
        parsed = _loads_tolerant(candidate)
        if parsed is not None:
            return SalvageResult(parsed[0], True, None)

    # Element-by-element salvage is only meaningful for an array; a broken object is opaque.
    if text[start] == "[":
        elements = _salvage_array_elements(candidate)
        if elements:
            reason = (
                "response was truncated; salvaged intact array elements"
                if truncated
                else "array was partly corrupt; salvaged intact elements"
            )
            return SalvageResult(elements, False, reason)

    return SalvageResult(None, False, "response was not valid JSON")


def _first_bracket(text: str) -> int | None:
    """Index of the first ``{`` or ``[`` — where the JSON structure begins after any
    leading prose or code fence."""
    for i, ch in enumerate(text):
        if ch in "{[":
            return i
    return None


def _matching_bracket(text: str, start: int) -> int | None:
    """Index just past the bracket that closes the structure opened at ``start``, tracking
    nesting and skipping string contents. ``None`` when the structure is never closed (the
    response was truncated)."""
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _loads_tolerant(candidate: str) -> tuple[object] | None:
    """Parse ``candidate`` strictly, then after light repairs. Returns a 1-tuple wrapping
    the value (so a falsy value like ``[]`` is distinguishable from failure), or ``None``
    when neither parse succeeds."""
    for text in (candidate, _repair(candidate)):
        try:
            return (json.loads(text),)
        except ValueError:
            continue
    return None


def _repair(text: str) -> str:
    """Light, loss-free repairs for common malformations: single-quoted strings become
    double-quoted, and trailing commas before a ``}``/``]`` are removed."""
    text = re.sub(
        r"'((?:[^'\\]|\\.)*)'",
        lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
        text,
    )
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _salvage_array_elements(array_text: str) -> list[object]:
    """Split a (possibly unterminated) array's top-level elements and keep each that parses
    on its own — so a truncated or partly-corrupt array still yields the intact boxes that
    preceded the break (ADR 0019)."""
    salvaged: list[object] = []
    for chunk in _top_level_chunks(array_text):
        parsed = _loads_tolerant(chunk.strip())
        if parsed is None:
            break  # first unparseable element is the truncation/corruption point
        salvaged.append(parsed[0])
    return salvaged


def _top_level_chunks(array_text: str) -> list[str]:
    """The substrings between an array's top-level commas, stopping at its closing ``]`` or
    the end of a truncated string. Nested brackets and string contents are skipped so a
    comma inside an element or a string never splits it."""
    chunks: list[str] = []
    depth = 0
    in_str = False
    escape = False
    current: list[str] = []
    for ch in array_text[1:]:  # skip the opening '['
        if in_str:
            current.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            current.append(ch)
        elif ch in "{[":
            depth += 1
            current.append(ch)
        elif ch == "]" and depth == 0:
            break  # the array's own close
        elif ch in "}]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            chunks.append("".join(current))
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        chunks.append("".join(current))
    return chunks


def downsample(
    source: Path, dest: Path, max_long_edge: int = DEFAULT_DOWNSAMPLE_PX
) -> Path:
    with Image.open(source) as image:
        scale = max_long_edge / max(image.size)
        new_size = (round(image.width * scale), round(image.height * scale))
        image.resize(new_size).save(dest)
    return dest


def _detections_to_boxes(
    detections: Iterable[LocationDetection],
) -> list[LabeledBox]:
    """Flatten model detections into the shared ``LabeledBox`` shape."""
    return [
        LabeledBox(
            d.label,
            d.bounding_box.x_min,
            d.bounding_box.y_min,
            d.bounding_box.x_max,
            d.bounding_box.y_max,
        )
        for d in detections
    ]


def _build_overlay(
    image_path: Path, groups: list[tuple[Iterable[LabeledBox], str]]
) -> Image.Image:
    """Draw one or more color-coded groups of labeled boxes onto a copy of the page image.

    ``groups`` is a list of ``(boxes, color)``; boxes later in the list are drawn on top.
    The single place box drawing lives, so the prediction overlay (ticket 09) has one
    canonical renderer."""
    with Image.open(image_path) as image:
        overlay = image.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    width, height = overlay.size
    for boxes, color in groups:
        for box in boxes:
            x0, y0 = box.x_min * width, box.y_min * height
            draw.rectangle(
                (x0, y0, box.x_max * width, box.y_max * height),
                outline=color,
                width=3,
            )
            # Label the box so the taxonomy class is legible on the overlay, not just
            # the location — a developer needs to tell a cabinet box from a countertop.
            draw.text((x0 + 2, max(0, y0 - 12)), box.label, fill=color)
    return overlay


def draw_overlay(
    image_path: Path, detections: list[LocationDetection], dest: Path
) -> Path:
    """Render the model's detections (red) on the page image and cache it to ``dest``
    (ticket 09)."""
    overlay = _build_overlay(
        image_path, [(_detections_to_boxes(detections), PREDICTION_COLOR)]
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(dest)
    return dest
