import importlib
from pathlib import Path

import pytest

import core.config as config_module
from core.config import RunConfig, library_versions


class TestModelRoster:
    def test_falls_back_to_default_when_env_var_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(config_module.MODEL_ROSTER_ENV_VAR, raising=False)
        assert config_module._load_model_roster() == config_module.DEFAULT_MODEL_ROSTER

    def test_env_var_replaces_the_default_roster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config_module.MODEL_ROSTER_ENV_VAR, '{"vendor/model-x": 1234}')
        assert config_module._load_model_roster() == {"vendor/model-x": 1234}

    def test_module_level_roster_reflects_env_var_at_import_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(config_module.MODEL_ROSTER_ENV_VAR, '{"vendor/model-x": 1234}')
        reloaded = importlib.reload(config_module)
        try:
            assert reloaded.MODEL_ROSTER == {"vendor/model-x": 1234}
        finally:
            monkeypatch.delenv(config_module.MODEL_ROSTER_ENV_VAR, raising=False)
            importlib.reload(config_module)


class TestRunConfig:
    def test_build_sets_dataset_version_from_dataset_dir_name(self) -> None:
        config = RunConfig.build(model="m", run_id="r", dataset_dir=Path("dataset/project-0001"))
        assert config.dataset_version == "project-0001"

    def test_compatibility_key_changes_with_requested_max_px(self) -> None:
        versions = library_versions()
        a = RunConfig.compute_compatibility_key(
            prompt_hash="h", dataset_version="v", requested_max_px=1000, library_versions=versions
        )
        b = RunConfig.compute_compatibility_key(
            prompt_hash="h", dataset_version="v", requested_max_px=2000, library_versions=versions
        )
        assert a != b
