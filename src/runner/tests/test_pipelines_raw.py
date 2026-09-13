import json
from pathlib import Path

import pytest

from core.parse import ZeroDetectionsError
from core.pipelines.raw import RawPipeline

from .helpers import FailingModelClient, StubModelClient, ok_response, write_smoke_dataset


class TestRawPipeline:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.out_dir = tmp_path / "out"

    def _smoke_dataset_dir(self) -> Path:
        dataset_dir = self.tmp_path / "dataset"
        dataset_dir.mkdir()
        return write_smoke_dataset(dataset_dir)

    def test_execute_run_writes_a_complete_run(self) -> None:
        client = StubModelClient()

        run_dir = RawPipeline(client).execute_run(
            model="test/model",
            dataset_dir=self._smoke_dataset_dir(),
            run_id="r1",
            out_dir=self.out_dir,
            max_px=1024,
        )

        assert run_dir == self.out_dir / "r1"
        metadata = json.loads((run_dir / "run.json").read_text())
        assert metadata["status"] == "complete"
        assert metadata["pages_scored"] == 1
        assert metadata["pages_total"] == 1
        assert len(client.calls) == 1

        run_score = json.loads((run_dir / "scores.json").read_text())
        canonical = run_score["scores"]["0.50"]
        assert canonical["counts"] == {"tp": 2, "fp": 0, "fn": 0}

    def test_execute_run_resumes_without_recalling_the_model(self) -> None:
        dataset_dir = self._smoke_dataset_dir()
        client = StubModelClient()
        pipeline = RawPipeline(client)
        pipeline.execute_run(
            model="test/model",
            dataset_dir=dataset_dir,
            run_id="r1",
            out_dir=self.out_dir,
            max_px=1024,
        )
        assert len(client.calls) == 1

        pipeline.execute_run(
            model="test/model",
            dataset_dir=dataset_dir,
            run_id="r1",
            out_dir=self.out_dir,
            max_px=1024,
        )
        assert len(client.calls) == 1  # the second call resumed from the existing record

    def test_execute_run_records_a_technical_failure_without_raising(self) -> None:
        client = FailingModelClient()

        run_dir = RawPipeline(client).execute_run(
            model="test/model",
            dataset_dir=self._smoke_dataset_dir(),
            run_id="r1",
            out_dir=self.out_dir,
            max_px=1024,
            max_attempts=1,
        )

        metadata = json.loads((run_dir / "run.json").read_text())
        assert metadata["status"] == "partial"
        assert metadata["pages_scored"] == 0
        failure = json.loads((run_dir / "drawing-1" / "p0001.json").read_text())
        assert failure["status"] == "failed"

    def test_execute_run_raises_when_every_scored_page_finds_nothing(self) -> None:
        client = StubModelClient(respond=lambda: ok_response("[]"))
        with pytest.raises(ZeroDetectionsError):
            RawPipeline(client).execute_run(
                model="test/model",
                dataset_dir=self._smoke_dataset_dir(),
                run_id="r1",
                out_dir=self.out_dir,
                max_px=1024,
            )
