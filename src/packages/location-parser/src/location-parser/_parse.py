import json

from ._boxes import find_container, to_box
from ._extract import extract_candidate
from ._repair import repair_truncated
from ._sanitize import sanitize
from ._types import ParseResult


def parse(
    text: str,
    *,
    page: int = 0,
    allowed_labels: set[str] | None = None,
) -> ParseResult:
    """Extract usable bounding boxes from one raw model reply.

    `text` is exactly what came back from the model for a single request -- a full
    completion, whatever shape it's in. It is commonly not clean JSON: it may be
    wrapped in a fenced code block or in explanatory prose, cut off mid-array by a
    token limit, sprinkled with stray semicolons or trailing commas, or labeled with
    strings a taxonomy never agreed to. This function decides what in there can be
    trusted and returns the rest as counts, never raising on bad input::

        {"boxes": [...], "dropped": 0, "complete": True, "error": None}

    Every returned box is `{"object_type", "bbox": [x_min, y_min, x_max, y_max],
    "page"}` -- the same shape `location_scorer.score()` takes as `predictions`
    directly, so the two packages compose without a translation step in between.

    `page` is stamped onto every box this call returns; a call parses one reply for
    one page, so pass whatever page number that reply was generated for. It has
    nothing to do with any page number the model itself might have mentioned in the
    text -- that value, if present, is not read.

    `allowed_labels`, when given, drops any entry whose label isn't in the set --
    this is where an invented label gets caught, since the label itself is otherwise
    read and trusted as-is, with no taxonomy of its own.

    **Coordinate scale is auto-detected per box.** If every coordinate in a box's bbox
    has an absolute value of 1.0 or less, it is assumed to already be normalized 0-1
    and is passed through unchanged. If any coordinate exceeds 1.0, the whole box is
    assumed to be on a 0-1000 scale (the convention several prompts in this project
    use) and every coordinate in it is divided by 1000. This is a per-box decision --
    a reply that mixes both scales across different entries is handled correctly,
    though that would be unusual for one model reply.

    **What counts as `dropped` vs. left alone.** An entry is dropped -- removed
    entirely, counted in `dropped`, absent from `boxes` -- when it is structurally
    unusable (not an object, missing a recognizable label key, a label
    `allowed_labels` doesn't include, missing a recognizable bbox in any accepted
    shape, or a coordinate that isn't finite), or when it is an **exact duplicate** of
    a box already kept from this same call (same `object_type` and, after scaling and
    coordinate-order swapping, byte-identical `bbox`) -- some models repeat an entry
    verbatim rather than emit it once. A box that parses cleanly but describes
    nonsense geometry -- inverted corners now swapped back into order, zero area, or
    coordinates outside 0-1 even after scaling -- is *not* dropped: it is returned
    as-is, because a downstream scorer will naturally score it as a false positive
    (`location_scorer` does this via IoU 0), which is more informative than silently
    erasing evidence that the model hallucinated a box.

    **What counts as `complete=False`.** Extracting a JSON payload out of a fenced
    code block or surrounding commentary is treated as normal wrapping, not damage --
    it does not affect `complete`. `complete` is False only when the JSON itself had
    to be repaired to parse at all: a reply cut off mid-structure (closed back up by
    finding, then discarding, its last incomplete entry -- see `repair_truncated`), or
    syntax a strict JSON parser rejects outright (a stray `;` in place of `,`, a
    trailing comma before a closing bracket, a `//` or `/* */` comment -- see
    `sanitize`). When `complete` is False, `error` names which kind of repair was
    needed. When nothing could be recovered at all, `boxes` is empty, `dropped` is 0
    (there was nothing to individually drop), `complete` is False, and `error` says
    why.

    This function never raises: malformed input from an unreliable model is exactly
    the case it exists to handle, not something that should stop a batch.
    """
    candidate = extract_candidate(text)

    truncated = False
    sanitized_changed = False

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        sanitized, sanitized_changed = sanitize(candidate)
        try:
            parsed = json.loads(sanitized)
        except json.JSONDecodeError:
            repaired = repair_truncated(sanitized)
            if repaired is None:
                return {
                    "boxes": [],
                    "dropped": 0,
                    "complete": False,
                    "error": "could not parse a JSON object or array out of the response text",
                }
            parsed = repaired
            truncated = True

    container = find_container(parsed)
    if container is None:
        return {
            "boxes": [],
            "dropped": 0,
            "complete": not truncated,
            "error": (
                "response was truncated and no boxes could be recovered"
                if truncated
                else "no recognizable list of boxes found in the parsed JSON"
            ),
        }

    boxes = []
    seen: set[tuple[str, tuple[float, ...]]] = set()
    dropped = 0
    for entry in container:
        box = to_box(entry, page=page, allowed_labels=allowed_labels)
        if box is None:
            dropped += 1
            continue
        key = (box["object_type"], tuple(box["bbox"]))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        boxes.append(box)

    complete = not (truncated or sanitized_changed)
    error = None
    if truncated:
        error = "response was truncated; recovered by closing unterminated brackets"
    elif sanitized_changed:
        error = (
            "response contained malformed JSON syntax (stray semicolons, trailing "
            "commas, or comments); repaired automatically"
        )

    return {"boxes": boxes, "dropped": dropped, "complete": complete, "error": error}
