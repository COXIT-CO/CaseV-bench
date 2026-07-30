"""In-memory session/state store.

This tool is a single-user local benchmarking app, not a multi-tenant
service, so an in-memory dict is deliberately simpler than wiring up a
database. Sessions are keyed by an opaque id and live for the life of the
backend process; the rendered PNGs backing them persist under
data/sessions/<id>/ regardless (so re-running docker compose without
re-uploading is possible by re-scanning disk if ever needed).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.models.schemas import PageResult
from app.services.parser import merge_page_results


@dataclass
class PageRecord:
    page_id: str
    page_number: int
    image_path: Path
    # A downscaled copy of image_path (see services/image_utils.py), served
    # to the browser instead of the full-resolution original -- the latter
    # can be too large for HTMLImageElement.decode() to handle in
    # BBoxCanvas.jsx. width/height below stay the TRUE full-resolution
    # values regardless (export-boundary pixel math depends on them).
    display_image_path: Path
    width: int
    height: int


@dataclass
class SessionData:
    session_id: str
    filename: str
    session_dir: Path
    pages: dict[str, PageRecord]
    dpi: int
    results: dict[str, PageResult] = field(default_factory=dict)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}
        self._lock = threading.Lock()

    def create(self, session: SessionData) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> SessionData | None:
        with self._lock:
            return self._sessions.get(session_id)

    def require(self, session_id: str) -> SessionData:
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def set_result(self, session_id: str, result: PageResult, merge: bool = False) -> None:
        """Store a page's result. With `merge=True` (Multi-Prompting mode,
        one call per category), this combines `result` with whatever's
        already stored for that page instead of overwriting it -- see
        parser.merge_page_results -- so concurrent per-category calls for
        the same page don't clobber each other. The read-merge-write happens
        under the same lock as the write, so two concurrent calls for the
        same page can't race and drop one's contribution."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if merge:
                existing = session.results.get(result.page_id)
                session.results[result.page_id] = merge_page_results(existing, result)
            else:
                session.results[result.page_id] = result

    def clear_results(self, session_id: str, page_ids: list[str]) -> None:
        """Drop any stored results for these pages. Multi-Prompting mode
        calls this once before firing its per-category `merge=True` calls,
        so re-running doesn't merge new detections on top of a previous
        run's stale ones."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            for page_id in page_ids:
                session.results.pop(page_id, None)


session_store = SessionStore()
