"""JSON API router (ADR 0010), mounted under ``/api`` alongside the untouched Jinja/HTMX
routes so the old app stays live while the React SPA is built slice by slice.

Slice 0 (ticket 01) provides a single proof endpoint, ``GET /api/meta``, that the SPA
shell fetches to verify the Vite -> JSON -> shadcn pipeline end to end. A JSON route and
its HTMX twin call the **same service methods**; the feature endpoints (leaderboard,
results, runs, prompts, library) land in their own later slices (spec Part A).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, SQLModel, select

from db import get_session
from models.drawing import Drawing
from models.prompt import Task
from models.results import OBJECT_LABELS
from models.run import Result, Run

# Mirrors ``web.app.APP_TITLE`` (app.py stays the composition root; duplicating one
# string avoids an import cycle between the app factory and this router).
APP_TITLE = "Prompt & Config Lab"

api_router = APIRouter(prefix="/api")


class ApiMeta(BaseModel):
    """App-level facts the SPA shell renders to prove it is talking to the live API:
    the app name, the fixed Task/ObjectType taxonomy, and current domain-row counts."""

    app: str
    tasks: list[str]
    labels: list[str]
    drawing_count: int
    run_count: int
    result_count: int


def _count(session: Session, model: type[SQLModel]) -> int:
    """Row count for ``model`` without materializing the rows."""
    return session.exec(select(func.count()).select_from(model)).one()


@api_router.get("/meta", response_model=ApiMeta)
def meta(session: Session = Depends(get_session)) -> ApiMeta:
    return ApiMeta(
        app=APP_TITLE,
        tasks=[t.value for t in Task],
        labels=list(OBJECT_LABELS),
        drawing_count=_count(session, Drawing),
        run_count=_count(session, Run),
        result_count=_count(session, Result),
    )
