"""Response DTOs shared by more than one router.

These few shapes are referenced across resources (a Drawing summary appears on both the
leaderboard filter and the run-launch form; a curated model entry on both the launch form
and the model catalog), so they live here rather than being owned by one router and
imported sideways.
"""

from pydantic import BaseModel


class LeaderboardDrawing(BaseModel):
    """One Drawing in a dropdown/filter surface: id, name, and page count (used by the
    leaderboard's Drawing filter and the run-launch form)."""

    id: int
    name: str
    page_count: int


class DrawingRef(BaseModel):
    """A minimal Drawing reference (id + name) embedded in run and drawing detail."""

    id: int
    name: str


class CatalogEntryOut(BaseModel):
    """One curated model the launch form renders as a checkbox/chip and the catalog lists."""

    slug: str
    label: str


class KnobsOut(BaseModel):
    """The read-only per-run knobs snapshot a Run recorded (spec: Runs 18), embedded in both
    the Run detail and the Result drill-down. ``temperature`` is null when the Run used the
    provider default (ADR 0018/0019)."""

    dpi: int
    downsample_px: int
    max_tokens: int
    temperature: float | None
