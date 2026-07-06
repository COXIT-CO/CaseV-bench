from pydantic import BaseModel, Field
from typing import Literal


ObjectLabel = Literal["cabinets", "countertops", "elevations", "elevation_callout"]


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
    page: int
    detections: list[LocationDetection]
