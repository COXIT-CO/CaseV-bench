from typing import Literal, NamedTuple, get_args

from pydantic import BaseModel, Field

ObjectLabel = Literal["cabinet", "countertop", "elevation", "elevation_callout"]

# The fixed object taxonomy in canonical order (glossary: ObjectType). Derived from
# ``ObjectLabel`` so the enum stays the single source of truth for the labels.
OBJECT_LABELS: tuple[ObjectLabel, ...] = get_args(ObjectLabel)


class CountResult(BaseModel):
    cabinet: int
    countertop: int
    elevation: int
    elevation_callout: int


class BoundingBox(BaseModel):
    x_min: float = Field(ge=0, le=1)
    y_min: float = Field(ge=0, le=1)
    x_max: float = Field(ge=0, le=1)
    y_max: float = Field(ge=0, le=1)


class LocationDetection(BaseModel):
    label: ObjectLabel
    bounding_box: BoundingBox


class LocationResult(BaseModel):
    detections: list[LocationDetection]


class LabeledBox(NamedTuple):
    """A labeled normalized (0-1) box — the flat shape both a predicted detection and a
    ``LocationGroundTruth`` row reduce to. Shared by scoring's pure matcher (so it never
    touches the ORM) and the overlay renderer (so it draws predictions and GT uniformly).
    """

    label: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float
