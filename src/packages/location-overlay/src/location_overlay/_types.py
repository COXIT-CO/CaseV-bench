from typing import NotRequired, Sequence, TypedDict

Color = tuple[int, int, int]
Size = tuple[int, int]  # (width, height) in pixels
Rect = tuple[int, int, int, int]  # [left, top, right, bottom] in pixels


class Box(TypedDict):
    object_type: str
    bbox: Sequence[float]  # [x_min, y_min, x_max, y_max], 0–1, top-left origin
    page: NotRequired[int]  # never drawn, only checked for agreement — see `render()`
