import math
from typing import Sequence

from ._types import Box


def validate(boxes: Sequence[Box]) -> None:
    """Reject what cannot be drawn, and the one mistake that would draw the wrong picture.

    Shape is checked; geometry never is. See `render()` for why that line falls where it does.
    """
    pages: set[int] = set()
    for index, box in enumerate(boxes):
        _validate_object_type(box, index)
        _validate_bbox(box, index)
        page = box.get("page")
        if page is not None:
            pages.add(page)

    if len(pages) > 1:
        raise ValueError(
            f"boxes span pages {sorted(pages)}: one image is one page, so filter "
            f"the boxes to the page being drawn before calling render()"
        )


def _validate_object_type(box: Box, index: int) -> None:
    if "object_type" not in box:
        raise ValueError(f"boxes[{index}] has no 'object_type'")
    if not isinstance(box["object_type"], str):
        raise ValueError(
            f"boxes[{index}]['object_type'] is not a string: {box['object_type']!r}"
        )


def _validate_bbox(box: Box, index: int) -> None:
    if "bbox" not in box:
        raise ValueError(f"boxes[{index}] has no 'bbox'")

    bbox = box["bbox"]
    if isinstance(bbox, (str, bytes)) or len(bbox) != 4:
        raise ValueError(
            f"boxes[{index}]['bbox'] is not four numbers "
            f"[x_min, y_min, x_max, y_max]: {bbox!r}"
        )

    for value in bbox:
        # `bool` is an `int`, and `True` as a coordinate is a bug wearing a number's clothes.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"boxes[{index}]['bbox'] holds a non-number: {bbox!r}",
            )
        if not math.isfinite(value):
            raise ValueError(
                f"boxes[{index}]['bbox'] holds {value}, which has no place on a page: {bbox!r}"
            )
