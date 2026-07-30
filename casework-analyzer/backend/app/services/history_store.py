"""Persisted log of past detection runs.

Written once per `POST /api/analyze` call, not once per page: every page in
one request shares the same prompts/model/temperature/dpi, and that shared
configuration is exactly what a user would want to reload later. One JSON
file per run under `data_dir/history/` -- several analyze requests can be
in flight concurrently (see the semaphore in api/analyze.py), and a single
shared append-only file would need extra locking to stay safe, while
independent per-run files don't.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import Settings


def _history_dir(settings: Settings) -> Path:
    path = settings.data_dir / "history"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_run(settings: Settings, record: dict) -> str:
    """Write one run record, stamping it with a fresh id + timestamp.

    `record` should NOT include "id" or "timestamp" -- those are added here
    so every record's format is consistent regardless of caller.
    """
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:6]}"
    full_record = {
        "id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    path = _history_dir(settings) / f"{run_id}.json"
    path.write_text(json.dumps(full_record, indent=2), encoding="utf-8")
    return run_id


def list_runs(settings: Settings) -> list[dict]:
    """All saved run records, most recent first."""
    records = []
    for path in _history_dir(settings).glob("*.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue  # skip a corrupt/partial file rather than fail the whole list
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records
