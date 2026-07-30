"""Parsing LLM output into structured results, and building benchmark export formats."""
from __future__ import annotations

import json
import re

from json_repair import repair_json
from pydantic import TypeAdapter, ValidationError

from app.models.schemas import (
    BBox,
    DetectedObject,
    ExportCountResponse,
    ExportLocationObject,
    ExportLocationResponse,
    PageResult,
    PageResultStatus,
    ParsedResult,
)

_OBJECT_ADAPTER = TypeAdapter(DetectedObject)

# Key-name typos/aliases seen in real model output -- e.g. Gemini 3.1 Pro
# Preview emitting "y.min" for "y_min", or a bare "y"/"x" in place of
# "y_min"/"x_min" -- that are safe to silently rewrite before validation.
# Unlike a semantically-wrong value (see DetectedObject's 0-1000 bounds), a
# misnamed key doesn't change what the model meant.
_KEY_ALIASES = {
    "x.min": "x_min",
    "y.min": "y_min",
    "x.max": "x_max",
    "y.max": "y_max",
    "x": "x_min",
    "y": "y_min",
}

# A model has previously nested coordinates under a sub-object instead of
# the current flat per-detection shape (that was the schema before this
# one -- see CLAUDE.md's coordinate-system entry) or borrowed a different
# vendor's key name for the same concept. If any of these show up as a
# nested dict on a detection, its contents get merged up into the
# detection itself (see _flatten_nested_box) rather than failing validation.
_NESTED_BOX_KEYS = ("bounding_box", "bbox", "bounding_2d", "box_2d")

# The model's `label` (see prompts/detector_system.txt / cabinet_user.txt:
# "cabinet", "countertop", "elevation", "elevation_callout") to the plural
# category names used across config.yaml's `categories`, ExportCountResponse,
# and the frontend's summary tiles.
LABEL_TO_CATEGORY = {
    "cabinet": "cabinets",
    "countertop": "countertops",
    "elevation": "elevations",
    "elevation_callout": "elevation_callouts",
}

# The reverse of the above, for Multi-Prompting mode: given the category a
# request was fired for (see AnalyzeRequest.category), what singular label to
# force onto every detection it returns -- see relabel_for_category. Category
# color-coding in the frontend (BBoxCanvas.jsx's CATEGORY_COLORS) is keyed on
# this singular label, so it has to be right regardless of what label string
# the model itself used.
CATEGORY_TO_LABEL = {v: k for k, v in LABEL_TO_CATEGORY.items()}


def strip_code_fences(text: str) -> str:
    """Strip a leading/trailing ```json ... ``` fence, if the model added one
    despite being told not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _normalize_keys(value):
    """Recursively rewrite known key aliases (see _KEY_ALIASES) throughout a
    parsed JSON value, so one misnamed key doesn't fail an otherwise-valid
    detection.

    A model has been seen emitting both a shorthand and the canonical key in
    the same malformed object (e.g. "y": 632 alongside "y_min": 632, or two
    "y" entries that collapse to one during JSON parsing) -- canonical keys
    always win, aliases only fill in a name that isn't already present, so a
    real value is never silently overwritten by a fallback one.
    """
    if isinstance(value, dict):
        normalized = {
            k: _normalize_keys(v) for k, v in value.items() if k not in _KEY_ALIASES
        }
        for k, v in value.items():
            if k in _KEY_ALIASES:
                normalized.setdefault(_KEY_ALIASES[k], _normalize_keys(v))
        return normalized
    if isinstance(value, list):
        return [_normalize_keys(v) for v in value]
    return value


def _flatten_nested_box(item):
    """Merge a nested box sub-object (see _NESTED_BOX_KEYS) up into the
    detection itself, if present -- existing top-level keys win on conflict.
    A no-op for an already-flat detection (the current, expected shape)."""
    if not isinstance(item, dict):
        return item
    flattened = dict(item)
    for key in _NESTED_BOX_KEYS:
        nested = flattened.pop(key, None)
        if isinstance(nested, dict):
            for k, v in nested.items():
                flattened.setdefault(k, v)
    return flattened


def validate_detection(item) -> DetectedObject | None:
    """Flatten a nested box sub-object (see _flatten_nested_box), normalize
    known key aliases (see _normalize_keys), and validate the result as a
    DetectedObject -- returning None (not raising) for anything that's
    still invalid afterward (bad syntax repair, or values outside the
    documented 0-1000 range), so a caller iterating a list of raw detection
    dicts can just skip the ones that come back None instead of failing
    the whole batch over one bad entry.

    Extracted out of parse_model_response's own loop so ai_crop.py's Pass 2
    parser (services/ai_crop.py's _build_verified_crops, a different
    top-level response shape entirely) can reuse the exact same
    per-detection resilience without reaching into this module's
    underscored internals directly.
    """
    item = _normalize_keys(_flatten_nested_box(item))
    try:
        return _OBJECT_ADAPTER.validate_python(item)
    except ValidationError:
        return None


def parse_model_response(raw_text: str) -> tuple[ParsedResult | None, bool]:
    """Try to parse a model's raw text into a ParsedResult.

    The model's raw response is a single JSON object with one key,
    "bounding_box", holding the array of detections (see
    prompts/detector_system.txt) -- `ParsedResult` only exists as this app's
    internal aggregate (objects + computed counts). A bare array at the top
    level (the previous schema's shape) is also accepted -- unambiguous
    either way, and models have drifted back to a prior schema's shape
    before.

    Real models occasionally produce a response that's mostly right but not
    quite: a misnamed key, coordinates re-nested under a sub-object instead
    of flattened onto the detection, or (rarer) outright broken JSON syntax
    partway through a long array while the rest is fine. Rather than failing
    the whole page over one bad detection, this: (1) falls back to a
    JSON-repair pass if strict parsing fails, (2) flattens a nested box
    sub-object onto the detection if one shows up anyway, (3) normalizes
    known key aliases, and (4) validates each array entry independently,
    dropping only the entries that are actually invalid (bad syntax repair,
    or values outside the documented 0-1000 range) instead of the whole
    response.

    Returns (parsed_result_or_None, parse_error_flag). The raw text is always
    preserved by the caller regardless of parse success.
    """
    cleaned = strip_code_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            data = repair_json(cleaned, return_objects=True)
        except Exception:
            return None, True

    if isinstance(data, list):
        detections = data
    elif isinstance(data, dict):
        detections = data.get("bounding_box")
    else:
        return None, True

    if not isinstance(detections, list):
        return None, True
    if not detections:
        return ParsedResult(objects=[], counts={}), False

    objects: list[DetectedObject] = []
    for item in detections:
        obj = validate_detection(item)
        if obj is not None:
            objects.append(obj)  # one malformed detection doesn't sink the whole page

    if not objects:
        return None, True

    tally: dict[str, int] = {}
    for obj in objects:
        category = LABEL_TO_CATEGORY.get(obj.label, obj.label)
        tally[category] = tally.get(category, 0) + 1
    return ParsedResult(objects=objects, counts=tally), False


def relabel_for_category(parsed: ParsedResult, category: str) -> ParsedResult:
    """Force every detection's label to the one canonical singular label for
    `category` (see CATEGORY_TO_LABEL), and recompute counts from that --
    used for a Multi-Prompting request, where the user prompt already asked
    the model for exactly one category, so the category is known for certain
    regardless of what label string the model actually used. This is what
    guarantees BBoxCanvas.jsx's category color-coding stays correct even if
    a model drifts (e.g. capitalizes "CABINET", or invents a slightly
    different label) when only asked to find one thing."""
    label = CATEGORY_TO_LABEL.get(category, category)
    for obj in parsed.objects:
        obj.label = label
    return ParsedResult(
        objects=parsed.objects,
        counts={category: len(parsed.objects)} if parsed.objects else {},
    )


def _sum_optional(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def _append_raw(base: str | None, category: str | None, raw: str | None) -> str | None:
    if raw is None:
        return base
    labeled = f"--- {category or 'response'} ---\n{raw}"
    return f"{base}\n\n{labeled}" if base else labeled


def merge_page_results(existing: PageResult | None, incoming: PageResult) -> PageResult:
    """Combine one category's result for a page with whatever's already
    been merged in for other categories of the same page (see
    session_store.SessionStore.set_result's `merge` flag) -- used only for
    Multi-Prompting requests (AnalyzeRequest.category set). `existing` is
    None for the first category to arrive for a given page; order doesn't
    matter otherwise, since this is called under session_store's lock so
    concurrent categories for the same page merge one at a time, never racing.

    A category that errored or failed to parse doesn't sink the merged
    result -- it's recorded in `category_errors` instead, and the page still
    shows `status: done` with whatever the other categories found, as long
    as at least one of them produced something.
    """
    base_objects = list(existing.parsed.objects) if existing and existing.parsed else []
    base_counts = dict(existing.parsed.counts) if existing and existing.parsed else {}
    base_category_errors = dict(existing.category_errors) if existing and existing.category_errors else {}

    objects = base_objects + (list(incoming.parsed.objects) if incoming.parsed else [])
    counts = base_counts
    for key, count in (incoming.parsed.counts if incoming.parsed else {}).items():
        counts[key] = counts.get(key, 0) + count
    had_any_success = bool(existing and existing.parsed is not None)
    parsed = (
        ParsedResult(objects=objects, counts=counts)
        if had_any_success or incoming.parsed is not None
        else None
    )

    category_errors = base_category_errors
    category_key = incoming.category or "unknown"
    if incoming.status == PageResultStatus.ERROR:
        category_errors[category_key] = incoming.error_message or "Request failed"
    elif incoming.parsed is None:
        category_errors[category_key] = "Could not parse JSON from the response"

    # AI-crop mode's `crops` (see services/ai_crop.py) -- concatenated
    # across categories with no dedup, same "just concatenate" philosophy
    # already used for `objects` above (each category ran its own
    # independent two-pass pipeline, see analyze.py's analyze_one).
    base_crops = list(existing.crops) if existing and existing.crops else []
    crops = base_crops + (list(incoming.crops) if incoming.crops else [])

    return PageResult(
        page_id=incoming.page_id,
        page_number=incoming.page_number,
        status=PageResultStatus.DONE if parsed is not None else PageResultStatus.ERROR,
        raw_response=_append_raw(
            existing.raw_response if existing else None, incoming.category, incoming.raw_response
        ),
        parsed=parsed,
        parse_error=parsed is None,
        truncated=bool(existing and existing.truncated) or incoming.truncated,
        error_message=(
            None
            if parsed is not None
            else "; ".join(f"{c}: {m}" for c, m in category_errors.items())
        ),
        reasoning_tokens=_sum_optional(
            existing.reasoning_tokens if existing else None, incoming.reasoning_tokens
        ),
        completion_tokens=_sum_optional(
            existing.completion_tokens if existing else None, incoming.completion_tokens
        ),
        category=None,  # a merged result spans multiple categories
        category_errors=category_errors or None,
        crops=crops or None,
        ai_crop_message=(existing.ai_crop_message if existing else None) or incoming.ai_crop_message,
    )


def build_count_export(results: list[PageResult]) -> ExportCountResponse:
    """Sum per-category counts across all analyzed pages in a session."""
    totals = ExportCountResponse()
    for result in results:
        if result.parsed is None:
            continue
        for category, count in result.parsed.counts.items():
            if hasattr(totals, category):
                setattr(totals, category, getattr(totals, category) + int(count))
    return totals


def build_location_export(
    project_id: str,
    results: list[PageResult],
    page_dimensions: dict[int, tuple[int, int]],
) -> ExportLocationResponse:
    """Flatten per-page detected objects into the benchmark's location schema.

    Models are prompted to return `x_min, y_min, x_max, y_max` normalized
    0-1000 relative to the image's own width/height (see
    prompts/detector_system.txt), so they don't need to know a page's exact
    pixel dimensions. The benchmark's obj-location.json format is pixel-space
    (see dataset/project-*/prj*-obj-location.json), so that conversion
    happens here, at the export boundary, using each page's known
    width/height rather than anywhere upstream.
    """
    objects: list[ExportLocationObject] = []
    for result in results:
        if result.parsed is None:
            continue
        width, height = page_dimensions.get(result.page_number, (1, 1))
        for index, obj in enumerate(result.parsed.objects):
            pixel_bbox = BBox(
                x=round((obj.x_min / 1000) * width, 1),
                y=round((obj.y_min / 1000) * height, 1),
                width=round(((obj.x_max - obj.x_min) / 1000) * width, 1),
                height=round(((obj.y_max - obj.y_min) / 1000) * height, 1),
            )
            objects.append(
                ExportLocationObject(
                    id=f"page{result.page_number}-{index}",
                    category=LABEL_TO_CATEGORY.get(obj.label, obj.label),
                    page=result.page_number,
                    bbox=pixel_bbox,
                )
            )
    return ExportLocationResponse(project_id=project_id, objects=objects)
