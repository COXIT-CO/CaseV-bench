"""Persists and replays past detection runs in demo-app's own Postgres database —
entirely separate from src/results_store's shared research store; this table exists
only so the UI's history drawer has something to list and reload.

Stores the original PDF and the parsed detection boxes, not the rendered images:
a run costs a couple MB (mostly the PDF) instead of the tens of MB the original and
annotated PNGs for every page would cost. Opening a history entry re-renders the PDF
and redraws the overlay from the stored boxes (see detect.rebuild_pages) rather than
serving stored images.

If DEMO_APP_DATABASE_URL isn't set, every function here is a documented no-op — local
dev without a Postgres instance should still be able to run detections, just without
history.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("casev.demo.history")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [history] %(message)s", "%H:%M:%S"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

DATABASE_URL_ENV_VAR = "DEMO_APP_DATABASE_URL"


@dataclass(frozen=True, slots=True)
class RunSummary:
    id: str
    created_at: datetime
    filename: str
    model: str
    custom_prompt: bool
    page_count: int
    detection_count: int


@dataclass(frozen=True, slots=True)
class RunDetail:
    id: str
    filename: str
    model: str
    prompt: str
    custom_prompt: bool
    render_px: int
    pdf_bytes: bytes
    pages_meta: list[dict[str, Any]]


def _database_url() -> str | None:
    return os.environ.get(DATABASE_URL_ENV_VAR, "").strip() or None


def _connect() -> Any:
    """Returns a new psycopg connection, or None if history is unconfigured. Imports
    psycopg lazily so the rest of the app never pays for it when history is unused."""
    url = _database_url()
    if url is None:
        return None
    import psycopg

    return psycopg.connect(url)


def ensure_schema() -> None:
    conn = _connect()
    if conn is None:
        logger.info("%s not set; run history is disabled", DATABASE_URL_ENV_VAR)
        return
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id UUID PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    filename TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    custom_prompt BOOLEAN NOT NULL,
                    render_px INTEGER NOT NULL,
                    pdf_bytes BYTEA NOT NULL,
                    page_count INTEGER NOT NULL,
                    detection_count INTEGER NOT NULL,
                    pages_json JSONB NOT NULL
                )
                """
            )
    conn.close()
    logger.info("run history schema ready")


def save_run(
    *,
    filename: str,
    model: str,
    prompt: str,
    custom_prompt: bool,
    render_px: int,
    pdf_bytes: bytes,
    pages_meta: list[dict[str, Any]],
) -> str | None:
    conn = _connect()
    if conn is None:
        return None
    from psycopg.types.json import Jsonb

    run_id = str(uuid.uuid4())
    page_count = len(pages_meta)
    detection_count = sum(len(page.get("boxes", [])) for page in pages_meta)
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runs
                    (id, filename, model, prompt, custom_prompt, render_px, pdf_bytes,
                     page_count, detection_count, pages_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    filename,
                    model,
                    prompt,
                    custom_prompt,
                    render_px,
                    pdf_bytes,
                    page_count,
                    detection_count,
                    Jsonb(pages_meta),
                ),
            )
    conn.close()
    return run_id


def list_runs(limit: int = 50) -> list[RunSummary]:
    conn = _connect()
    if conn is None:
        return []
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at, filename, model, custom_prompt,
                       page_count, detection_count
                FROM runs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    conn.close()
    return [
        RunSummary(
            id=str(row[0]),
            created_at=row[1],
            filename=row[2],
            model=row[3],
            custom_prompt=row[4],
            page_count=row[5],
            detection_count=row[6],
        )
        for row in rows
    ]


def get_run(run_id: str) -> RunDetail | None:
    conn = _connect()
    if conn is None:
        return None
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, model, prompt, custom_prompt, render_px,
                       pdf_bytes, pages_json
                FROM runs
                WHERE id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return RunDetail(
        id=str(row[0]),
        filename=row[1],
        model=row[2],
        prompt=row[3],
        custom_prompt=row[4],
        render_px=row[5],
        pdf_bytes=bytes(row[6]),
        pages_meta=row[7],
    )


def delete_run(run_id: str) -> bool:
    conn = _connect()
    if conn is None:
        return False
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE id = %s", (run_id,))
            deleted = cur.rowcount > 0
    conn.close()
    return deleted
