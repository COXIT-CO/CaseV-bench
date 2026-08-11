import math
from typing import Any

from ._types import Box

# Tried in order; the first key present on an entry wins. Different prompts and
# different model families settle on different names for the same thing, and this
# package holds no opinion about which is "correct".
_LABEL_KEYS = ("label", "object_type", "class", "type")
_BBOX_KEYS = ("box", "bbox", "bounding_box")

# Fallback shapes when a bbox isn't a 4-element sequence under one of `_BBOX_KEYS`:
# four separate named fields instead. Tried in order, and tried both directly on the
# entry and inside a nested "identifying_properties" object, since both have been seen
# in the wild. Each tuple is (x_min_key, y_min_key, x_max_key, y_max_key).
_NAMED_BBOX_KEY_SETS = (
    ("left", "top", "right", "bottom"),
    ("x0", "y0", "x1", "y1"),
)

# A coordinate whose absolute value exceeds this is assumed to be on a 0-1000 scale
# (the convention several of our own prompts use) rather than the 0-1 scale
# `location_scorer` expects, and is rescaled by /1000 accordingly. 1.0 exactly is
# still treated as in-range 0-1, since a box legitimately touching the far edge of the
# image has a coordinate of exactly 1.0.
_SCALE_THRESHOLD = 1.0 + 1e-6

# Container keys tried, in order, when the parsed JSON is an object rather than a bare
# array. "objects" is checked first because it's what our own prompts ask for; the
# rest are common alternates seen across other prompt styles.
_CONTAINER_KEYS = ("objects", "boxes", "detections", "predictions", "results")


def find_container(parsed: Any) -> list[Any] | None:
    """Locate the list of box-shaped entries inside a parsed JSON value.

    Accepts a bare list directly, an object with one of the known container keys, or
    a single object that itself looks like one box (has both a label-like and a
    bbox-like key) -- wrapped into a one-item list. Returns None if nothing
    recognizable is found.
    """
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in _CONTAINER_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                return value
        if any(k in parsed for k in _LABEL_KEYS) and any(
            k in parsed for k in _BBOX_KEYS
        ):
            return [parsed]
    return None


def to_box(entry: Any, *, page: int, allowed_labels: set[str] | None) -> Box | None:
    """Validate and normalize one candidate entry into a `Box`, or return None if it
    isn't structurally usable.

    A box is dropped here (never repaired or guessed) when: it isn't an object; it has
    no recognizable label key with a non-empty string value; `allowed_labels` was
    given and the label isn't in it; it has no recognizable bbox in any of the
    accepted shapes (see `_resolve_bbox`); or any of those four values is missing,
    non-numeric, or non-finite (NaN/Infinity).

    Coordinates that are simply out of order (`x_min > x_max` and/or `y_min > y_max`)
    are swapped back into order rather than left inverted or dropped: this has been
    observed from real models (Gemini in particular) on otherwise-valid detections,
    and discarding them would silently lose real boxes. A box that is still zero-area
    after swapping, or otherwise geometrically degenerate, is NOT dropped here either
    -- it is handed to the caller as-is so a downstream scorer can score it as a false
    positive, which is more informative than silently hiding it. Only structurally
    unusable entries count against `ParseResult.dropped`.
    """
    if not isinstance(entry, dict):
        return None

    label = None
    for key in _LABEL_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            label = value.strip()
            break
    if label is None:
        return None
    if allowed_labels is not None and label not in allowed_labels:
        return None

    raw_bbox = _resolve_bbox(entry)
    if raw_bbox is None:
        return None

    coords: list[float] = []
    for value in raw_bbox:
        number = _as_float(value)
        if number is None or not math.isfinite(number):
            return None
        coords.append(number)

    if any(abs(c) > _SCALE_THRESHOLD for c in coords):
        coords = [c / 1000.0 for c in coords]

    coords = _swap_into_order(coords)

    return {"object_type": label, "bbox": coords, "page": page}


def _resolve_bbox(entry: dict[str, Any]) -> Any | None:
    """Find the four bbox values on `entry`, trying every accepted shape in order:
    a 4-element list/tuple under `box`/`bbox`/`bounding_box`; then four separate named
    fields (`left`/`top`/`right`/`bottom`, then `x0`/`y0`/`x1`/`y1`) read either
    directly off `entry` or, if present, off a nested `identifying_properties` object
    -- both layouts have been seen from real models. Returns the four raw values in
    `[x_min, y_min, x_max, y_max]` order, unconverted, or None if nothing matched."""
    for key in _BBOX_KEYS:
        value = entry.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 4:
            return value

    nested = entry.get("identifying_properties")
    source = nested if isinstance(nested, dict) else entry
    for key_set in _NAMED_BBOX_KEY_SETS:
        if all(k in source for k in key_set):
            return [source[k] for k in key_set]

    return None


def _swap_into_order(coords: list[float]) -> list[float]:
    x_min, y_min, x_max, y_max = coords
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    if y_min > y_max:
        y_min, y_max = y_max, y_min
    return [x_min, y_min, x_max, y_max]


def _as_float(value: Any) -> float | None:
    # bool is a subclass of int in Python; excluded explicitly so a stray `true`/
    # `false` in a bbox slot is dropped rather than silently read as 1.0/0.0.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
