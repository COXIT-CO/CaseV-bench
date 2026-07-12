from typing import Literal, get_args

from pydantic import BaseModel, Field

ObjectLabel = Literal["cabinets", "countertops", "elevations", "elevation_callout"]

# The fixed object taxonomy in canonical order (glossary: ObjectType). Derived from
# ``ObjectLabel`` so the enum stays the single source of truth for the labels.
OBJECT_LABELS: tuple[ObjectLabel, ...] = get_args(ObjectLabel)


class CountResult(BaseModel):
    cabinets: int
    countertops: int
    elevations: int
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
