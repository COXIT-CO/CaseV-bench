"""Settings resolution — env values drive the single data root and derived paths.

The production defaults live here (ticket 01, ADR-0014); service constructors still take
explicit paths, so these assertions build fresh ``Settings`` from a controlled environment
(``_env_file=None`` ignores any local ``.env``) rather than the module-level singleton.
"""

from pathlib import Path

import pytest

from config import Settings


def _settings(monkeypatch, **env: str) -> Settings:
    """A ``Settings`` built from an explicit environment, hermetic from any ``.env`` file."""
    for key in ("CASEV_DATA_ROOT", "CASEV_DATABASE_URL", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_data_root_drives_derived_paths(monkeypatch):
    settings = _settings(monkeypatch, CASEV_DATA_ROOT="/srv/casev")

    assert settings.data_root == Path("/srv/casev")
    assert settings.drawings_root == Path("/srv/casev/drawings")
    assert settings.overlays_root == Path("/srv/casev/overlays")
    assert settings.output_root == Path("/srv/casev/output")


def test_database_url_defaults_under_data_root(monkeypatch):
    settings = _settings(monkeypatch, CASEV_DATA_ROOT="/srv/casev")

    assert settings.database_url == "sqlite:////srv/casev/casev.sqlite"


def test_database_url_override_is_independent_of_data_root(monkeypatch):
    settings = _settings(
        monkeypatch,
        CASEV_DATA_ROOT="/srv/casev",
        CASEV_DATABASE_URL="postgresql://user:pw@host/db",
    )

    assert settings.database_url == "postgresql://user:pw@host/db"
    # The override leaves the filesystem roots untouched.
    assert settings.drawings_root == Path("/srv/casev/drawings")


def test_default_data_root_is_relative_data_dir(monkeypatch):
    settings = _settings(monkeypatch)

    assert settings.data_root == Path("data")
    assert settings.database_url == "sqlite:///data/casev.sqlite"


def test_openrouter_key_read_without_prefix(monkeypatch):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY="sk-test-123")

    assert settings.openrouter_api_key == "sk-test-123"
    assert settings.require_openrouter_api_key() == "sk-test-123"


def test_missing_openrouter_key_fails_fast_with_clear_message(monkeypatch):
    settings = _settings(monkeypatch)

    assert settings.openrouter_api_key is None
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        settings.require_openrouter_api_key()
