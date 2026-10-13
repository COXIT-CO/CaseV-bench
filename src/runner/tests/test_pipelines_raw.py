import json
from pathlib import Path

import pytest

from core.parse import ZeroDetectionsError
from core.pipelines.raw import RawPipeline

from .helpers import (
    ConcurrencyTrackingModelClient,
    FailingModelClient,
    FractionalBox,
    StubModelClient,
    ok_response,
    write_local_dataset,
    write_smoke_dataset,
)

_CABINET: FractionalBox = {
    "label": "cabinet",
    "x_min": 0.1,
    "y_min": 0.1,
    "x_max": 0.3,
    "y_max": 0.3,
}


class TestRawPipeline:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.out_dir = tmp_path / "out"

    def _smoke_dataset_dir(self) -> Path:
        dataset_dir = self.tmp_path / "dataset"
        dataset_dir.mkdir()
        return write_smoke_dataset(dataset_dir)

    def _multi_page_dataset_dir(self, page_count: int) -> Path:
        dataset_dir = self.tmp_path / "dataset"
        dataset_dir.mkdir()
        write_local_dataset(dataset_dir, "d1", pages_objects=[[_CABINET]] * page_count)
        return dataset_dir

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

    def test_execute_run_defaults_to_calling_pages_one_at_a_time(self) -> None:
        client = ConcurrencyTrackingModelClient(hold_seconds=0.05)

        RawPipeline(client).execute_run(
            model="test/model",
            dataset_dir=self._multi_page_dataset_dir(4),
            run_id="r1",
            out_dir=self.out_dir,
            max_px=1024,
        )

        assert client.max_concurrent == 1
        assert len(client.calls) == 4

    def test_execute_run_dispatches_pages_concurrently_up_to_the_thread_count(self) -> None:
        client = ConcurrencyTrackingModelClient(hold_seconds=0.05)

        RawPipeline(client).execute_run(
            model="test/model",
            dataset_dir=self._multi_page_dataset_dir(4),
            run_id="r1",
            out_dir=self.out_dir,
            max_px=1024,
            threads=4,
        )

        assert client.max_concurrent == 4
        assert len(client.calls) == 4

    def test_execute_run_never_exceeds_the_requested_thread_count(self) -> None:
        client = ConcurrencyTrackingModelClient(hold_seconds=0.05)

        RawPipeline(client).execute_run(
            model="test/model",
            dataset_dir=self._multi_page_dataset_dir(4),
            run_id="r1",
            out_dir=self.out_dir,
            max_px=1024,
            threads=2,
        )

        assert client.max_concurrent == 2
        assert len(client.calls) == 4

    def test_execute_run_with_threads_scores_the_same_as_sequential(self) -> None:
        sequential_dir = self.out_dir / "sequential"
        threaded_dir = self.out_dir / "threaded"
        dataset_dir = self._multi_page_dataset_dir(4)

        RawPipeline(StubModelClient()).execute_run(
            model="test/model",
            dataset_dir=dataset_dir,
            run_id="r1",
            out_dir=sequential_dir,
            max_px=1024,
        )
        RawPipeline(StubModelClient()).execute_run(
            model="test/model",
            dataset_dir=dataset_dir,
            run_id="r1",
            out_dir=threaded_dir,
            max_px=1024,
            threads=4,
        )

        sequential_score = json.loads((sequential_dir / "r1" / "scores.json").read_text())
        threaded_score = json.loads((threaded_dir / "r1" / "scores.json").read_text())
        assert sequential_score == threaded_score

    def test_execute_run_rejects_non_positive_threads(self) -> None:
        client = StubModelClient()
        with pytest.raises(ValueError, match="max_workers"):
            RawPipeline(client).execute_run(
                model="test/model",
                dataset_dir=self._smoke_dataset_dir(),
                run_id="r1",
                out_dir=self.out_dir,
                max_px=1024,
                threads=0,
            )
