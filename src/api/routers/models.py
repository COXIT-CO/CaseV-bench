"""Library — Models (spec §A.6): the curated model catalog for viewing. Model *selection*
still happens inline at Run launch; this is catalog *viewing* only (ADR 0011)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from api.deps import get_session
from api.routers.common import CatalogEntryOut
from core.services.model_catalog import ModelCatalogService

router = APIRouter(prefix="/api", tags=["models"])


class ModelsResponse(BaseModel):
    """``GET /api/models``: the curated catalog. Model *selection* still happens inline at
    Run launch; this is catalog *viewing* only (ADR 0011)."""

    catalog: list[CatalogEntryOut]


@router.get("/models", response_model=ModelsResponse)
def list_models(session: Session = Depends(get_session)) -> ModelsResponse:
    """The curated model catalog (spec §A.6). The free-text escape hatch is a client-side
    input resolved at Run launch, not part of this list."""
    catalog = ModelCatalogService(session).list_catalog()
    return ModelsResponse(
        catalog=[CatalogEntryOut(slug=e.slug, label=e.label) for e in catalog]
    )
