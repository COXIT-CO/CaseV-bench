"""Run / Result / Prediction tables — one launched experiment and its stored
outputs (spec: Runs & execution; glossary: Run, Result, Prediction; ADR 0006).

A ``Run`` is one execution of ``(task, prompt version, drawing, N models)``. It
snapshots the fixed knobs used (DPI, downsample long-edge, max_tokens, prefill,
temperature) so a result stays reproducible even though those knobs aren't tunable
(spec: Configuration is ``(prompt version, model)`` only). Each Model's outcome is a
``Result`` (the comparison unit scores later attach to); each Result holds one
``Prediction`` per Page — the raw model content plus parsed counts, or a failure
record with the parse error. ``status`` / ``progress`` exist now but only advance
synchronously in this ticket; ticket 06 moves execution to a background task.
Kept DB-agnostic per ADR 0007/0008.
"""

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, Relationship, SQLModel

from models.prompt import Task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    """Lifecycle of a Run (glossary: Run). Synchronous execution walks
    queued → running → done/failed in one request; ticket 06 backgrounds it."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class PredictionStatus(str, Enum):
    """Whether a Page's model output parsed (``ok``) or failed after one retry
    (``error``). Mirrors the POC's ModelSuccess / ModelFailure split."""

    ok = "ok"
    error = "error"


class Run(SQLModel, table=True):
    __tablename__ = "run"

    id: int | None = Field(default=None, primary_key=True)
    task: Task = Field(index=True)
    prompt_id: int = Field(foreign_key="prompt.id", index=True)
    drawing_id: int = Field(foreign_key="drawing.id", index=True)

    status: RunStatus = Field(default=RunStatus.queued, index=True)
    # Completed (model, page) units; == len(models) * len(pages) when done.
    progress: int = 0
    total_units: int = 0

    # Snapshot of the fixed knobs used, so results stay reproducible (spec: Runs 18).
    dpi: int
    downsample_px: int
    max_tokens: int
    prefill: bool
    temperature: float

    created_at: datetime = Field(default_factory=_utcnow)

    results: list["Result"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"order_by": "Result.id"},
    )


class Result(SQLModel, table=True):
    __tablename__ = "result"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    # The chosen slug string (seeded or free-text), stored on the Result per the
    # spec data model — the catalog is reference data, not a relational parent.
    model: str
    created_at: datetime = Field(default_factory=_utcnow)

    run: Run | None = Relationship(back_populates="results")
    predictions: list["Prediction"] = Relationship(
        back_populates="result",
        sa_relationship_kwargs={"order_by": "Prediction.id"},
    )


class Prediction(SQLModel, table=True):
    __tablename__ = "prediction"

    id: int | None = Field(default=None, primary_key=True)
    result_id: int = Field(foreign_key="result.id", index=True)
    page_id: int = Field(foreign_key="page.id", index=True)
    # Denormalized 1-based page number for display without loading the Page.
    page_number: int

    status: PredictionStatus
    # The model's raw response content — retained on both success and failure so a
    # developer can inspect exactly what came back (spec: Runs 19, 21).
    raw_content: str | None = None
    # Parsed JSON as text on success (counting: the per-page counts); None on failure.
    parsed_json: str | None = None
    # The parse error recorded after the one retry failed; None on success.
    parse_error: str | None = None

    result: Result | None = Relationship(back_populates="predictions")
