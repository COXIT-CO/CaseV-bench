"""Prompts (spec §A.5): the Task-grouped family list, authoring, version history, and the
"edit" that appends the next immutable version (ADR 0009). Same ``PromptService`` methods,
so authoring never mutates a prior version and Task-scoping is preserved."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_prompt_service
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
    time. The text rides along so the compare/read view needs no extra fetch (spec §A.5). It
    also carries this version's delete collateral (the Runs + Results that pinned it) so the
    per-version delete confirm can state the blast radius up front (ADR-0016, ticket 09).
    """

    version: int
    text: str
    created_at: datetime
    run_count: int
    result_count: int


class PromptHistoryResponse(BaseModel):
    """``GET /api/prompts/{task}/{family}``: the family's versions newest-first. The
    family-level ``run_count``/``result_count`` are the whole-family delete's collateral (the
    Runs + Results pinning any version — the sum of the per-version counts, since a Run pins
    exactly one version) so the "delete family" confirm states the blast radius (ADR-0016).
    """

    task: str
    family: str
    versions: list[PromptVersionOut]
    run_count: int
    result_count: int


class PromptDeletedOut(BaseModel):
    """The collateral a prompt delete removed (ADR-0016, ticket 09): the Runs + Results the
    cascade took with the deleted version(s) — the delete's receipt, the same ``(runs,
    results)`` shape the Run and Drawing deletes return."""

    runs: int
    results: int


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
def list_prompts(
    service: PromptService = Depends(get_prompt_service),
) -> PromptsResponse:
    """Families grouped by Task with each family's latest version + count (spec §A.5).
    Every Task appears even with no families, so the SPA renders an empty group rather
    than dropping the Task."""
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
    service: PromptService = Depends(get_prompt_service),
) -> PromptVersionRef:
    """Author a new family at v1 (spec §A.5). A duplicate family is the service's
    ``ValueError`` surfaced as ``400`` with its message (mirrors the Jinja handler)."""
    family = payload.family.strip()
    try:
        prompt = service.create(payload.task, family=family, text=payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PromptVersionRef(
        task=prompt.task.value, family=prompt.family, version=prompt.version
    )


@router.get("/prompts/{task}/{family}", response_model=PromptHistoryResponse)
def prompt_history(
    task: Task,
    family: str,
    service: PromptService = Depends(get_prompt_service),
) -> PromptHistoryResponse:
    """A family's immutable version history newest-first, each version's text included so
    the compare/read view needs no extra round-trip (spec §A.5). Each version and the family
    as a whole carry their delete-collateral counts (the Runs + Results that pinned them) so
    the delete confirms can state the blast radius (ADR-0016). An unknown family (no versions)
    is a ``404``."""
    versions = service.history(task, family)
    if not versions:
        raise HTTPException(
            status_code=404,
            detail=f"no prompt family {family!r} for task {task.value}",
        )
    collateral = service.collateral_by_version(task, family)
    return PromptHistoryResponse(
        task=task.value,
        family=family,
        versions=[
            PromptVersionOut(
                version=p.version,
                text=p.text,
                created_at=p.created_at,
                run_count=collateral[p.version].runs,
                result_count=collateral[p.version].results,
            )
            for p in versions
        ],
        run_count=sum(c.runs for c in collateral.values()),
        result_count=sum(c.results for c in collateral.values()),
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
    service: PromptService = Depends(get_prompt_service),
) -> PromptVersionRef:
    """ "Edit" = append the next immutable version (ADR 0009); prior versions are never
    mutated (spec §A.5). An unknown family is the service's ``ValueError`` surfaced as a
    ``404`` (mirrors the Jinja edit handler)."""
    try:
        prompt = service.edit(task, family=family, text=payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PromptVersionRef(
        task=prompt.task.value, family=prompt.family, version=prompt.version
    )


@router.delete(
    "/prompts/{task}/{family}/versions/{version}",
    response_model=PromptDeletedOut,
)
def delete_prompt_version(
    task: Task,
    family: str,
    version: int,
    service: PromptService = Depends(get_prompt_service),
) -> PromptDeletedOut:
    """Permanently delete one immutable version, cascading the Runs that pinned it — their
    Results, Predictions, Scores, and overlay files — so they leave the Leaderboard (ADR-0016,
    ticket 09). The family's other versions and their Runs are untouched, even mid-lineage.
    Returns the collateral counts the confirm dialog showed; an unknown version is a ``404``.
    """
    try:
        counts = service.delete_version(task, family, version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PromptDeletedOut(runs=counts.runs, results=counts.results)


@router.delete("/prompts/{task}/{family}", response_model=PromptDeletedOut)
def delete_prompt_family(
    task: Task,
    family: str,
    service: PromptService = Depends(get_prompt_service),
) -> PromptDeletedOut:
    """Permanently delete a whole family — every version and every Run pinning any of them
    (so they leave the Leaderboard too) (ADR-0016, ticket 09). Returns the collateral counts
    the confirm dialog showed; an unknown family is a ``404``."""
    try:
        counts = service.delete_family(task, family)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PromptDeletedOut(runs=counts.runs, results=counts.results)
