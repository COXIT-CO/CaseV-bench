import json
from pathlib import Path

import pytest

from core.artifacts import RunArtifacts
from core.client import ModelResponse, Usage
from core.config import RunConfig
from core.render import RenderedPage, Transform
from core.scoring import Box, DrawingScore, RunScore

_BOX: Box = {"object_type": "cabinet", "bbox": [0.0, 0.0, 1.0, 1.0], "page": 1}


def _response() -> ModelResponse:
    return ModelResponse(
        text="[]",
        finish_reason="stop",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.01),
        latency_seconds=0.1,
        temperature_sent=0.0,
    )


def _rendered() -> RenderedPage:
    return RenderedPage(
        png_bytes=b"\x89PNG",
        requested_long_edge=2048,
        long_edge=2048,
        effective_dpi=150.0,
        transform=Transform(image_width=2048, image_height=1536),
    )


def _drawing_score(drawing: str) -> DrawingScore:
    return {
        "drawing": drawing,
        "canonical_iou_threshold": 0.5,
        "unscored": False,
        "scorer_version": "0.1.0",
        "scores": {},
    }


def _run_score(drawings: list[str]) -> RunScore:
    return {
        "canonical_iou_threshold": 0.5,
        "scorer_version": "0.1.0",
        "drawings": drawings,
        "scores": {},
    }


class TestRunArtifacts:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.run_dir = tmp_path
        self.artifacts = RunArtifacts(tmp_path)

    def test_read_call_record_if_exists_is_none_before_any_write(self) -> None:
        assert self.artifacts.read_call_record_if_exists("d1", 1) is None

    def test_call_record_roundtrips(self) -> None:
        self.artifacts.write_call_record(
            drawing="d1",
            page=1,
            source_page=1,
            model="m",
            response=_response(),
            rendered=_rendered(),
        )
        record = self.artifacts.read_call_record_if_exists("d1", 1)
        assert RunArtifacts.call_succeeded(record)
        assert record["response_text"] == "[]"
        assert record["render"]["effective_dpi"] == 150.0

    def test_a_failed_call_does_not_count_as_succeeded(self) -> None:
        self.artifacts.write_call_failure(
            drawing="d1", page=1, source_page=1, model="m", error="boom", attempts=5
        )
        record = self.artifacts.read_call_record_if_exists("d1", 1)
        assert not RunArtifacts.call_succeeded(record)
        assert record is not None
        assert record["status"] == "failed"

    def test_parse_accounting_amends_the_existing_record(self) -> None:
        self.artifacts.write_call_record(
            drawing="d1",
            page=1,
            source_page=1,
            model="m",
            response=_response(),
            rendered=_rendered(),
        )
        self.artifacts.write_call_parse_accounting("d1", 1, dropped=2, complete=False)
        record = self.artifacts.read_call_record_if_exists("d1", 1)
        assert record is not None
        assert record["dropped"] == 2
        assert record["complete"] is False
        assert record["status"] == "ok"  # untouched by the amendment

    def test_write_predictions_writes_one_json_line_per_box(self) -> None:
        path = self.artifacts.write_predictions("d1", [_BOX, _BOX])
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == _BOX

    def test_write_drawing_score_lands_under_the_drawing_directory(self) -> None:
        score = _drawing_score("d1")
        path = self.artifacts.write_drawing_score(score)
        assert path == self.run_dir / "d1" / "scores.json"
        assert json.loads(path.read_text()) == score

    def test_write_run_score_lands_at_the_run_root(self) -> None:
        score = _run_score(["d1"])
        path = self.artifacts.write_run_score(score)
        assert path == self.run_dir / "scores.json"
        assert json.loads(path.read_text()) == score

    def test_run_metadata_is_complete_when_every_page_scored(self) -> None:
        config = RunConfig.build(model="m", run_id="r1", dataset_dir=self.run_dir / "smoke")
        self.artifacts.write_run_metadata(
            config=config,
            effective_dpi=150.0,
            pages_total=2,
            pages_scored=2,
            cost_spent_usd=0.02,
        )
        metadata = json.loads((self.run_dir / "run.json").read_text())
        assert metadata["status"] == "complete"

    def test_run_metadata_is_partial_when_pages_are_missing(self) -> None:
        config = RunConfig.build(model="m", run_id="r1", dataset_dir=self.run_dir / "smoke")
        self.artifacts.write_run_metadata(
            config=config,
            effective_dpi=150.0,
            pages_total=2,
            pages_scored=1,
            cost_spent_usd=0.01,
        )
        metadata = json.loads((self.run_dir / "run.json").read_text())
        assert metadata["status"] == "partial"
