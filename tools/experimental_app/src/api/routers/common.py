"""Response DTOs shared by more than one router.

These few shapes are referenced across resources (a Drawing summary appears on both the
leaderboard filter and the run-launch form; a curated model entry on both the launch form
and the model catalog), so they live here rather than being owned by one router and
imported sideways.
"""

from pydantic import BaseModel

from core.adapters.openrouter import efforts_for
from core.models.model_catalog import ModelCatalogEntry


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
    """One curated model the launch form renders as a checkbox/chip and the catalog lists.
    ``max_reasoning_effort`` is the highest effort this Model answers to, so the form can offer
    the selection's shared band rather than let a Run be launched that one Model would reject
    (``efforts_for``)."""

    slug: str
    label: str
    max_reasoning_effort: str

    @classmethod
    def of(cls, entry: ModelCatalogEntry) -> "CatalogEntryOut":
        """Build one from a catalog row, deriving the ceiling — so every endpoint that
        returns a catalog entry reports the same one."""
        return cls(
            slug=entry.slug,
            label=entry.label,
            max_reasoning_effort=efforts_for([entry.slug])[-1],
        )


class KnobsOut(BaseModel):
    """The read-only per-run knobs snapshot a Run recorded (spec: Runs 18), embedded in both
    the Run detail and the Result drill-down. ``temperature`` is null when the Run used the
    provider default; ``downsample_px`` is null when the Run sent full-resolution images
    (ADR 0018/0019)."""

    dpi: int
    downsample_px: int | None
    max_tokens: int
    temperature: float | None
    # Null on a Run launched before the knob existed — see ``Run.reasoning_effort``.
    reasoning_effort: str | None
