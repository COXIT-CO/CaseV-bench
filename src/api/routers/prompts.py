"""Prompts (spec §A.5): the Task-grouped family list, authoring, version history, and the
"edit" that appends the next immutable version (ADR 0009). Same ``PromptService`` methods,
so authoring never mutates a prior version and Task-scoping is preserved."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from api.deps import get_session
from core.models.prompt import Task
from core.services.prompt import PromptService

router = APIRouter(prefix="/api", tags=["prompts"])


class PromptFamilyOut(BaseModel):
    """One family in the Task-grouped list: its name, its newest version, and how many
    versions it has (so the list shows ``latest vN · N versions`` without a follow-up).
    """

    name: str
    latest_version: int
    count: int


class PromptGroupOut(BaseModel):
    """All of one Task's families (Task-scoping, ADR 0009)."""

    task: str
    families: list[PromptFamilyOut]


class PromptsResponse(BaseModel):
    """``GET /api/prompts``: the fixed Task taxonomy plus each Task's families, so the SPA
    renders a group per Task even when a Task has no families yet."""

    tasks: list[str]
    groups: list[PromptGroupOut]


class PromptVersionOut(BaseModel):
    """One immutable version in a family's history: its number, full text, and authored
    time. The text rides along so the compare/read view needs no extra fetch (spec §A.5).
    """

    version: int
    text: str
    created_at: datetime


class PromptHistoryResponse(BaseModel):
    """``GET /api/prompts/{task}/{family}``: the family's versions newest-first."""

    task: str
    family: str
    versions: list[PromptVersionOut]


class PromptCreateRequest(BaseModel):
    """``POST /api/prompts`` body: author a new family's v1 for a Task."""

    task: Task
    family: str
    text: str


class PromptVersionCreateRequest(BaseModel):
    """``POST /api/prompts/{task}/{family}/versions`` body: the "edit" that appends the
    next immutable version. Only the text changes; the ``(task, family)`` come from the URL.
    """

    text: str


class PromptVersionRef(BaseModel):
    """The just-written version returned from create/append so the SPA routes to it."""

    task: str
    family: str
    version: int


@router.get("/prompts", response_model=PromptsResponse)
def list_prompts(session: Session = Depends(get_session)) -> PromptsResponse:
    """Families grouped by Task with each family's latest version + count (spec §A.5).
    Every Task appears even with no families, so the SPA renders an empty group rather
    than dropping the Task."""
    service = PromptService(session)
    groups: list[PromptGroupOut] = []
    for task in Task:
        families: list[PromptFamilyOut] = []
        for family in service.families(task):
            versions = service.history(task, family)
            families.append(
                PromptFamilyOut(
                    name=family,
                    latest_version=versions[0].version,
                    count=len(versions),
                )
            )
        groups.append(PromptGroupOut(task=task.value, families=families))
    return PromptsResponse(tasks=[t.value for t in Task], groups=groups)


@router.post("/prompts", response_model=PromptVersionRef, status_code=201)
def create_prompt(
    payload: PromptCreateRequest,
    session: Session = Depends(get_session),
) -> PromptVersionRef:
    """Author a new family at v1 (spec §A.5). A duplicate family is the service's
    ``ValueError`` surfaced as ``400`` with its message (mirrors the Jinja handler)."""
    family = payload.family.strip()
    try:
        prompt = PromptService(session).create(
            payload.task, family=family, text=payload.text
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PromptVersionRef(
        task=prompt.task.value, family=prompt.family, version=prompt.version
    )


@router.get("/prompts/{task}/{family}", response_model=PromptHistoryResponse)
def prompt_history(
    task: Task, family: str, session: Session = Depends(get_session)
) -> PromptHistoryResponse:
    """A family's immutable version history newest-first, each version's text included so
    the compare/read view needs no extra round-trip (spec §A.5). An unknown family (no
    versions) is a ``404``."""
    versions = PromptService(session).history(task, family)
    if not versions:
        raise HTTPException(
            status_code=404,
            detail=f"no prompt family {family!r} for task {task.value}",
        )
    return PromptHistoryResponse(
        task=task.value,
        family=family,
        versions=[
            PromptVersionOut(version=p.version, text=p.text, created_at=p.created_at)
            for p in versions
        ],
    )


@router.post(
    "/prompts/{task}/{family}/versions",
    response_model=PromptVersionRef,
    status_code=201,
)
def append_prompt_version(
    task: Task,
    family: str,
    payload: PromptVersionCreateRequest,
    session: Session = Depends(get_session),
) -> PromptVersionRef:
    """ "Edit" = append the next immutable version (ADR 0009); prior versions are never
    mutated (spec §A.5). An unknown family is the service's ``ValueError`` surfaced as a
    ``404`` (mirrors the Jinja edit handler)."""
    try:
        prompt = PromptService(session).edit(task, family=family, text=payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PromptVersionRef(
        task=prompt.task.value, family=prompt.family, version=prompt.version
    )
