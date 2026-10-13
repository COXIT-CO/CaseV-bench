import json
import re
from dataclasses import dataclass
from typing import Any

from core.dataset import ALLOWED_LABELS
from core.scoring import Box

RESCALE_THRESHOLD = 2.0
RESCALE_DIVISOR = 1000.0

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_decoder = json.JSONDecoder()


@dataclass(frozen=True, slots=True)
class ParseResult:
    boxes: list[Box]
    dropped: int
    complete: bool


class ZeroDetectionsError(Exception):
    pass


class ResponseParser:
    """Parses a model's raw response text into predicted boxes, tolerating
    a markdown-fenced or truncated array and salvaging whatever entries
    parse cleanly before the point it breaks."""

    @staticmethod
    def _extract_array_text(response_text: str) -> str | None:
        text = response_text.strip()
        fence_match = _FENCE_PATTERN.search(text)
        if fence_match:
            text = fence_match.group(1).strip()
        start = text.find("[")
        if start == -1:
            return None
        return text[start:]

    @staticmethod
    def _salvage_entries(array_text: str) -> tuple[list[Any], int]:
        entries: list[Any] = []
        i = 1  # skip the opening '['
        n = len(array_text)

        while i < n:
            while i < n and array_text[i] in " \t\r\n,":
                i += 1
            if i >= n:
                break
            if array_text[i] == "]":
                return entries, 0
            try:
                value, end = _decoder.raw_decode(array_text, i)
            except json.JSONDecodeError:
                return entries, 1
            entries.append(value)
            i = end

        return entries, 1  # ran off the end without a top-level ']'

    @staticmethod
    def _entry_to_box(entry: Any, page: int) -> Box | None:
        if not isinstance(entry, dict):
            return None
        label, bounding_box = entry.get("label"), entry.get("bounding_box")
        if not isinstance(label, str) or label not in ALLOWED_LABELS:
            return None
        if not isinstance(bounding_box, dict):
            return None
        try:
            x_min, y_min, x_max, y_max = (
                float(bounding_box[key]) for key in ("x_min", "y_min", "x_max", "y_max")
            )
        except (KeyError, TypeError, ValueError):
            return None

        x_min, x_max = sorted((x_min, x_max))
        y_min, y_max = sorted((y_min, y_max))
        if max(abs(x_min), abs(y_min), abs(x_max), abs(y_max)) > RESCALE_THRESHOLD:
            x_min, y_min, x_max, y_max = (v / RESCALE_DIVISOR for v in (x_min, y_min, x_max, y_max))

        return {"object_type": label, "bbox": [x_min, y_min, x_max, y_max], "page": page}

    def parse_response(self, response_text: str, page: int) -> ParseResult:
        array_text = self._extract_array_text(response_text)
        if array_text is None:
            return ParseResult(boxes=[], dropped=0, complete=False)

        entries, salvage_dropped = self._salvage_entries(array_text)
        candidates = [self._entry_to_box(entry, page) for entry in entries]
        boxes = [box for box in candidates if box is not None]
        return ParseResult(
            boxes=boxes,
            dropped=salvage_dropped + len(candidates) - len(boxes),
            complete=salvage_dropped == 0,
        )
