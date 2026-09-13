from pathlib import Path

from core.config import RunConfig, library_versions


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
