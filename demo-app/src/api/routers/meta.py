"""``GET /api/meta`` — app-level facts the SPA shell fetches to prove it is talking to the
live API (the Vite → JSON → shadcn proof endpoint, spec slice 0)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, SQLModel, select

from api.deps import get_session
from core.models.drawing import Drawing
from core.models.prompt import Task
from core.models.results import OBJECT_LABELS
from core.models.run import Result, Run

# Mirrors ``api.app.APP_TITLE`` (app.py stays the composition root; duplicating one string
# avoids an import cycle between the app factory and this router).
APP_TITLE = "Prompt & Config Lab"

router = APIRouter(prefix="/api", tags=["meta"])


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


@router.get("/meta", response_model=ApiMeta)
def meta(session: Session = Depends(get_session)) -> ApiMeta:
    return ApiMeta(
        app=APP_TITLE,
        tasks=[t.value for t in Task],
        labels=list(OBJECT_LABELS),
        drawing_count=_count(session, Drawing),
        run_count=_count(session, Run),
        result_count=_count(session, Result),
    )
