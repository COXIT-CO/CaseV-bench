"""Database access layer: a SQLModel engine + session over SQLite.

Kept DB-agnostic (no SQLite-only constructs) so a later Postgres swap stays small
(ADR 0007, 0008). The schema is created via ``create_all()`` for now; Alembic comes
once it stabilizes. Delete the ``.sqlite`` file to reset during early dev.
"""

import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine

DEFAULT_DATABASE_URL = "sqlite:///data/casev.sqlite"


def get_database_url() -> str:
    """The database URL, overridable via ``CASEV_DATABASE_URL`` (e.g. a temp DB in tests)."""
    return os.environ.get("CASEV_DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(database_url: str | None = None) -> Engine:
    url = make_url(database_url or get_database_url())
    connect_args: dict = {}
    if url.get_backend_name() == "sqlite":
        # Allow the connection to be shared across threads (background runs, ADR 0006).
        connect_args["check_same_thread"] = False
        # SQLite is a single writer; concurrent background runs briefly contend, so
        # wait on the lock instead of erroring out immediately (ADR 0006).
        connect_args["timeout"] = 30
        _ensure_sqlite_parent_dir(url.database)
    return create_engine(url, connect_args=connect_args)


def init_db(engine: Engine) -> None:
    """Create every registered table. Idempotent."""
    # Import table models so they register on SQLModel.metadata before create_all.
    import models.counting_ground_truth  # noqa: F401
    import models.drawing  # noqa: F401
    import models.location_ground_truth  # noqa: F401
    import models.meta  # noqa: F401
    import models.model_catalog  # noqa: F401
    import models.prompt  # noqa: F401
    import models.run  # noqa: F401
    import models.score  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to the app's engine."""
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        yield session


def _ensure_sqlite_parent_dir(database: str | None) -> None:
    # ``database`` is None for a bare in-memory URL (``sqlite://``); nothing to create.
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
