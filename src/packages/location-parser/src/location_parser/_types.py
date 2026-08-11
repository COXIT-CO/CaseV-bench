from typing import NamedTuple, NotRequired, Sequence, TypedDict


class Box(TypedDict):
    object_type: str
    bbox: Sequence[float]
    page: int

class ParseResult(TypedDict):
    boxes: list[Box]
    dropped: int
    complete: bool
    error: str | None
    