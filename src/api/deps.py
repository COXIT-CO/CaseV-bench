"""FastAPI dependencies for the web layer (ADR-0015).

The one place the transport layer wires a request to the domain: a per-request DB session
bound to the app's engine, and the service objects the routers depend on. Kept out of the
routers so each router carries no wiring boilerplate, and out of ``core`` so the domain
engine never imports FastAPI (the ``api → core`` direction is one-way).
"""

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlmodel import Session

from core.adapters.openrouter import OpenRouterAdapter, get_openrouter_adapter
from core.services.drawing import DrawingService
from core.services.run import RunService


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session bound to the app's engine (set on ``app.state`` by the factory)."""
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        yield session


def get_run_service(
    session: Session = Depends(get_session),
    adapter: OpenRouterAdapter = Depends(get_openrouter_adapter),
) -> RunService:
    """A ``RunService`` bound to the request session; the adapter is overridable in tests."""
    return RunService(session, adapter)


def get_drawing_service(session: Session = Depends(get_session)) -> DrawingService:
    """The ingestion service for the upload route; override in tests to inject a fast,
    low-DPI service."""
    return DrawingService(session)
