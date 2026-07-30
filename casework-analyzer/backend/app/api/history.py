"""Read access to the persisted run history (see services/history_store.py).

Records themselves are written as a side effect of POST /analyze, not from
an endpoint here -- this router is read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.models.schemas import HistoryRecord
from app.services.history_store import list_runs

router = APIRouter(tags=["history"])


@router.get("/history", response_model=list[HistoryRecord])
def get_history(settings: Settings = Depends(get_settings)) -> list[dict]:
    return list_runs(settings)
