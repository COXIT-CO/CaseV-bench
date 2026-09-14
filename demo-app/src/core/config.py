"""Single typed home for runtime configuration (ADR-0014).

Everything the app needs from the environment lands on one ``Settings`` object with a
``CASEV_`` prefix, and every piece of filesystem state derives from a single
``CASEV_DATA_ROOT`` — so pointing the app at a mounted Volume is two env vars and a mount,
with nothing writing relative to the current working directory.

The module-level ``settings`` singleton supplies the *production defaults* that service
constructors fall back to (``DrawingService(cache_root=...)`` etc.); the injectable path
seams stay, so tests keep passing temp dirs and a temp DB and never read this object.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local-dev convenience: load secrets from ``src/core/.env`` (git-ignored, alongside this
# module). In the container the vars come straight from the environment and this file is
# simply absent.
_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CASEV_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The one root everything derives from. Defaults to ``data/`` for local dev; set to
    # ``/data`` (the mounted Volume) in the container.
    data_root: Path = Path("data")

    # Explicit DB override — the single knob the Postgres follow-up flips (ADR-0014).
    # ``None`` means "derive a SQLite file under the data root" (see ``database_url``).
    database_url_override: str | None = Field(default=None, alias="CASEV_DATABASE_URL")

    # The provider secret keeps its conventional un-prefixed name (not ``CASEV_``-prefixed);
    # optional at construction so importing settings never fails, validated at call time.
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")

    @property
    def drawings_root(self) -> Path:
        """Where ingested page images are cached."""
        return self.data_root / "drawings"

    @property
    def overlays_root(self) -> Path:
        """Where location prediction-overlay PNGs are cached."""
        return self.data_root / "overlays"

    @property
    def output_root(self) -> Path:
        """Scratch dir for PDF page rendering."""
        return self.data_root / "output"

    @property
    def database_url(self) -> str:
        """The DB URL: the explicit override if set, else a SQLite file under the data root."""
        if self.database_url_override:
            return self.database_url_override
        return f"sqlite:///{self.data_root}/casev.sqlite"

    def require_openrouter_api_key(self) -> str:
        """The OpenRouter key, or a clear failure if it is not configured. Called at the
        point a real request is about to be made so a missing secret fails fast."""
        if not self.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set — export it (or add it to src/core/.env for "
                "local dev) before launching a run."
            )
        return self.openrouter_api_key


# Instantiated once at import: the production defaults the rest of the app reads from.
settings = Settings()
