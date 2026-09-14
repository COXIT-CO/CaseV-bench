"""Library — Models (spec §A.6): the user-editable model catalog. A user adds a model once
(slug + label) and it then appears as a labeled checkbox on every future Run launch; entries
can also be removed (ticket 10). Model *selection* still happens inline at Run launch; this
router owns catalog *viewing and editing* (ADR 0011 as amended by ticket 10)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_model_catalog_service
from api.routers.common import CatalogEntryOut
from core.services.model_catalog import ModelCatalogService

router = APIRouter(prefix="/api", tags=["models"])


class ModelsResponse(BaseModel):
    """``GET /api/models``: the curated catalog. Model *selection* still happens inline at
    Run launch; this is catalog *viewing* only (ADR 0011)."""

    catalog: list[CatalogEntryOut]


class ModelUpsertRequest(BaseModel):
    """``POST /api/models`` body: add a model to the catalog by its OpenRouter slug and a
    friendly label. Re-posting a known slug re-labels it (upsert), so this is never a
    duplicate-key error (ticket 10)."""

    slug: str
    label: str


@router.get("/models", response_model=ModelsResponse)
def list_models(
    service: ModelCatalogService = Depends(get_model_catalog_service),
) -> ModelsResponse:
    """The curated model catalog (spec §A.6). The free-text escape hatch is a client-side
    input resolved at Run launch, not part of this list."""
    catalog = service.list_catalog()
    return ModelsResponse(
        catalog=[CatalogEntryOut(slug=e.slug, label=e.label) for e in catalog]
    )


@router.post("/models", response_model=CatalogEntryOut, status_code=201)
def add_model(
    payload: ModelUpsertRequest,
    service: ModelCatalogService = Depends(get_model_catalog_service),
) -> CatalogEntryOut:
    """Add a model to the catalog, or re-label it if the slug already exists (upsert,
    ticket 10). The slug is not validated against OpenRouter — a bad slug just fails
    per-model at run time. A blank slug or label is the service's ``ValueError`` surfaced
    as ``400``."""
    try:
        entry = service.add(payload.slug, payload.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CatalogEntryOut(slug=entry.slug, label=entry.label)


@router.delete("/models/{slug:path}", response_model=CatalogEntryOut)
def remove_model(
    slug: str,
    service: ModelCatalogService = Depends(get_model_catalog_service),
) -> CatalogEntryOut:
    """Remove a catalog entry by slug (ticket 10). Safe by construction: a Run stores the
    chosen slug string, not a reference to this table, so removing an entry never affects
    past Runs (ADR 0016). The slug rides the path (``:path`` so its embedded ``/`` is kept);
    an unknown slug is a ``404``."""
    try:
        removed = service.remove(slug)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return CatalogEntryOut(slug=removed.slug, label=removed.label)
