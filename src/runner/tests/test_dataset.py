from pathlib import Path

import pytest

from core.dataset import DatasetMalformedError, DatasetNotFoundError, LocalDatasetSource

from .helpers import FractionalBox, write_local_dataset


class TestLocalDatasetSource:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.root = tmp_path

    def _write_drawing(
        self, name: str, objects: list[FractionalBox], *, under: Path | None = None
    ) -> None:
        write_local_dataset(under or self.root, name, pages_objects=[objects])

    def test_loads_a_single_drawing_directory(self) -> None:
        self._write_drawing(
            "d1",
            objects=[{"label": "cabinet", "x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.3}],
        )
        drawings = LocalDatasetSource(self.root).load()
        assert set(drawings) == {"d1"}
        assert drawings["d1"].object_count == 1

    def test_loads_one_drawing_per_subdirectory(self) -> None:
        p1, p2 = self.root / "p1", self.root / "p2"
        p1.mkdir()
        p2.mkdir()
        self._write_drawing(
            "a",
            objects=[{"label": "cabinet", "x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.3}],
            under=p1,
        )
        self._write_drawing(
            "b",
            objects=[
                {"label": "countertop", "x_min": 0.05, "y_min": 0.05, "x_max": 0.15, "y_max": 0.15}
            ],
            under=p2,
        )
        drawings = LocalDatasetSource(self.root).load()
        assert set(drawings) == {"a", "b"}

    def test_missing_dataset_dir_raises(self) -> None:
        with pytest.raises(DatasetNotFoundError):
            LocalDatasetSource(self.root / "does-not-exist").load()

    def test_root_with_no_drawing_directories_raises(self) -> None:
        (self.root / "empty").mkdir()
        with pytest.raises(DatasetMalformedError):
            LocalDatasetSource(self.root).load()

    def test_degenerate_and_off_taxonomy_boxes_are_rejected_not_raised(self) -> None:
        self._write_drawing(
            "d1",
            objects=[
                {"label": "cabinet", "x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.3},
                # degenerate: zero-width box
                {"label": "cabinet", "x_min": 0.1, "y_min": 0.1, "x_max": 0.1, "y_max": 0.3},
                # off taxonomy: not in ALLOWED_LABELS
                {"label": "window", "x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.3},
            ],
        )
        drawings = LocalDatasetSource(self.root).load()
        drawing = drawings["d1"]
        assert drawing.object_count == 1
        assert len(drawing.rejected) == 2
        assert {r.reason for r in drawing.rejected} == {"degenerate", "off_taxonomy"}

    def test_floor_plan_alias_is_normalized(self) -> None:
        self._write_drawing(
            "d1",
            objects=[
                {"label": "floor plan", "x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.3}
            ],
        )
        drawings = LocalDatasetSource(self.root).load()
        assert drawings["d1"].pages[0].boxes[0].label == "floor_plan"
