"""``GET /api/health`` — the platform liveness probe (ticket 03, ADR-0013).

Deliberately the lightest possible route: a static ``{"status": "ok"}`` with **no database
round-trip**, so Railway's health check reports the process as live even while the SQLite
file is briefly locked by a background run (ADR-0006). It answers "is the process up?", not
"is every dependency healthy?" — that richer, DB-touching view is what ``/api/meta`` is for.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["health"])


class Health(BaseModel):
    """Liveness payload — a constant ``status: "ok"`` the platform probe reads to confirm
    the process is up (no dependency health is asserted; that's ``/api/meta``'s job)."""

    status: str


@router.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok")
