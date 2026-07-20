from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

LABEL_ALIASES = {
    "cabinet": "cabinets", "cabinets": "cabinets",
    "countertop": "countertops", "countertops": "countertops",
    "elevation": "elevations", "elevations": "elevations",
    "elevation_callout": "elevation_callouts",
    "elevation_callouts": "elevation_callouts",
    "elevation callout": "elevation_callouts",
    "elevation callouts": "elevation_callouts",
}
CANONICAL_LABELS = ("cabinets", "countertops", "elevations", "elevation_callouts")


def extract_json(text: str) -> Any:
    """Parse JSON from a model response, including fenced or surrounded JSON."""
    if not isinstance(text, str):
        return text
    value = text.strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(value):
            if char not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(value[index:])
                return parsed
            except json.JSONDecodeError:
                continue
        raise


def normalize_label(label: Any) -> str | None:
    if label is None:
        return None
    key = str(label).strip().lower().replace("-", "_")
    return LABEL_ALIASES.get(key)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return None
    return None


def extract_counts(data: Any) -> dict[str, int]:
    counts = Counter({key: 0 for key in CANONICAL_LABELS})
    if isinstance(data, dict):
        candidates = [data]
        for key in ("expected_summary", "counts", "summary", "totals", "result"):
            if isinstance(data.get(key), dict):
                candidates.insert(0, data[key])
        found_direct = False
        for candidate in candidates:
            for key, value in candidate.items():
                label = normalize_label(key)
                number = _number(value)
                if label and number is not None:
                    counts[label] = int(number)
                    found_direct = True
            if found_direct:
                return dict(counts)
        objects = data.get("objects") or data.get("items") or data.get("boxes")
        if isinstance(objects, list):
            data = objects
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                label = normalize_label(item.get("label") or item.get("type") or item.get("class"))
                if label:
                    counts[label] += 1
    return dict(counts)


def _normalize_coord_order(left: float, top: float, right: float, bottom: float) -> list[float]:
    """Sort coordinates instead of discarding the box.

    Models (Gemini in particular) occasionally emit a box with left/right or
    top/bottom swapped, even though the detection itself is otherwise valid.
    Dropping such boxes silently deletes real detections (observed: several
    "elevation" boxes per document lost this way). Sorting the pairs recovers
    the detection while still rejecting truly degenerate (zero-area) boxes
    downstream.
    """
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top
    return [left, top, right, bottom]


def _bbox(obj: dict[str, Any]) -> list[float] | None:
    box = obj.get("box") or obj.get("bbox")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        try:
            left, top, right, bottom = (float(v) for v in box)
        except (TypeError, ValueError):
            return None
        return _normalize_coord_order(left, top, right, bottom)
    source = obj.get("identifying_properties") if isinstance(obj.get("identifying_properties"), dict) else obj
    keys = ("left", "top", "right", "bottom") if all(k in source for k in ("left", "top", "right", "bottom")) else ("x0", "y0", "x1", "y1")
    try:
        left, top, right, bottom = (float(source[k]) for k in keys)
    except (KeyError, TypeError, ValueError):
        return None
    return _normalize_coord_order(left, top, right, bottom)


def extract_objects(data: Any, page_index: int = 0) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("objects", "items", "boxes", "detections"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        label = normalize_label(item.get("label") or item.get("type") or item.get("class"))
        box = _bbox(item)
        if not label or not box:
            continue

        # The localization prompt requires normalized 0..1000 page coordinates.
        left, top, right, bottom = [max(0.0, min(1000.0, value)) for value in box]
        if left >= right or top >= bottom:
            continue

        singular = label[:-1] if label != "elevation_callouts" else "elevation_callout"
        normalized_box = [
            int(round(left)),
            int(round(top)),
            int(round(right)),
            int(round(bottom)),
        ]
        normalized_object = {
            "label": singular,
            "left": normalized_box[0],
            "top": normalized_box[1],
            "right": normalized_box[2],
            "bottom": normalized_box[3],
            "box": normalized_box,
            "image_index": int(item.get("image_index", page_index)),
        }

        # Avoid drawing the same model detection more than once.
        duplicate = any(
            existing["label"] == normalized_object["label"]
            and existing["box"] == normalized_object["box"]
            for existing in result
        )
        if not duplicate:
            result.append(normalized_object)
    return result


def normalize_expected(data: Any) -> dict[str, int]:
    """Accept a plain count object or a previously exported run JSON."""
    if isinstance(data, dict):
        if isinstance(data.get("expected_summary"), dict):
            return extract_counts(data["expected_summary"])
        results = data.get("results")
        if isinstance(results, list):
            for result in results:
                if isinstance(result, dict) and isinstance(result.get("expected_summary"), dict):
                    return extract_counts(result["expected_summary"])
    return extract_counts(data)


def build_comparison(actual: dict[str, int], expected: dict[str, int] | None) -> list[dict[str, Any]]:
    if not expected:
        return []
    rows = []
    for label in CANONICAL_LABELS:
        exp = int(expected.get(label, 0))
        act = int(actual.get(label, 0))
        accuracy = 100.0 if exp == act == 0 else (0.0 if exp == 0 else min(act, exp) / max(act, exp) * 100)
        rows.append({"label": label, "expected": exp, "actual": act, "delta": act - exp, "accuracy": round(accuracy, 2)})
    total_expected = sum(r["expected"] for r in rows)
    total_actual = sum(r["actual"] for r in rows)
    total_accuracy = 100.0 if total_expected == total_actual == 0 else (0.0 if total_expected == 0 else min(total_actual, total_expected) / max(total_actual, total_expected) * 100)
    rows.append({"label": "total", "expected": total_expected, "actual": total_actual, "delta": total_actual - total_expected, "accuracy": round(total_accuracy, 2)})
    return rows