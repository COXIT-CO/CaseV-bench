import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypeGuard

from core.client import ModelResponse
from core.config import RunConfig
from core.render import RenderedPage
from core.scoring import CANONICAL_IOU_THRESHOLD, IOU_SWEEP, Box, DrawingScore, RunScore


class RunArtifacts:
    """Reads and writes everything a run leaves on disk under one run
    directory: per-page call records, per-drawing predictions and scores,
    the run-level score, and run.json."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    @staticmethod
    def call_succeeded(record: dict[str, Any] | None) -> TypeGuard[dict[str, Any]]:
        return record is not None and record.get("status") == "ok"

    @staticmethod
    def _write_json(path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(path.read_text())
        return result

    def _call_record_path(self, drawing: str, page: int) -> Path:
        return self._run_dir / drawing / f"p{page:04d}.json"

    def _predictions_path(self, drawing: str) -> Path:
        return self._run_dir / drawing / "predictions.jsonl"

    def _drawing_score_path(self, drawing: str) -> Path:
        return self._run_dir / drawing / "scores.json"

    def _run_score_path(self) -> Path:
        return self._run_dir / "scores.json"

    def _run_metadata_path(self) -> Path:
        return self._run_dir / "run.json"

    def read_call_record(self, drawing: str, page: int) -> dict[str, Any]:
        return self._read_json(self._call_record_path(drawing, page))

    def read_call_record_if_exists(self, drawing: str, page: int) -> dict[str, Any] | None:
        path = self._call_record_path(drawing, page)
        if not path.exists():
            return None
        return self._read_json(path)

    def write_call_record(
        self,
        *,
        drawing: str,
        page: int,
        model: str,
        response: ModelResponse,
        rendered: RenderedPage,
    ) -> Path:
        render = {
            "requested_long_edge": rendered.requested_long_edge,
            "long_edge": rendered.long_edge,
            "effective_dpi": rendered.effective_dpi,
            "width": rendered.width,
            "height": rendered.height,
        }
        record = {
            "drawing": drawing,
            "page": page,
            "model": model,
            "status": "ok",
            "response_text": response.text,
            "finish_reason": response.finish_reason,
            "usage": asdict(response.usage),
            "latency_seconds": response.latency_seconds,
            "temperature_sent": response.temperature_sent,
            "render": render,
        }
        return self._write_json(self._call_record_path(drawing, page), record)

    def write_call_failure(
        self,
        *,
        drawing: str,
        page: int,
        model: str,
        error: str,
        attempts: int,
    ) -> Path:
        """Record a page whose call permanently failed technically. Excluded
        from scoring by score_run, and never scored as zero — a rate limit
        must never look like a model that found nothing."""
        record = {
            "drawing": drawing,
            "page": page,
            "model": model,
            "status": "failed",
            "error": error,
            "attempts": attempts,
        }
        return self._write_json(self._call_record_path(drawing, page), record)

    def write_call_parse_accounting(
        self, drawing: str, page: int, *, dropped: int, complete: bool
    ) -> Path:
        """Merge parse accounting into an already-written call record: a
        call's raw response is written before it is ever parsed, so this
        amends the existing p<page>.json in place rather than writing it."""
        record = self.read_call_record(drawing, page)
        record.update(dropped=dropped, complete=complete)
        return self._write_json(self._call_record_path(drawing, page), record)

    def write_predictions(self, drawing: str, boxes: list[Box]) -> Path:
        path = self._predictions_path(drawing)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(box) + "\n" for box in boxes))
        return path

    def write_drawing_score(self, drawing_score: DrawingScore) -> Path:
        path = self._drawing_score_path(drawing_score["drawing"])
        return self._write_json(path, drawing_score)

    def write_run_score(self, run_score: RunScore) -> Path:
        return self._write_json(self._run_score_path(), run_score)

    def write_run_metadata(
        self,
        *,
        config: RunConfig,
        effective_dpi: float,
        pages_total: int,
        pages_scored: int,
        cost_spent_usd: float,
    ) -> Path:
        """Write run.json.

        ``render.effective_dpi`` is the first page's achieved DPI, representative
        whenever every page in the run shares a size. If a page's own raster
        content forces a lower native-resolution cap, that page's own call
        record under <drawing>/pNNNN.json carries its actual value instead — that
        record, not this one, is authoritative per page.

        ``status`` is "partial" whenever pages_scored < pages_total: a page
        permanently failed technically and has its own record with status
        "failed" and an error, so it's excluded from scoring but resumable.
        """
        metadata: dict[str, Any] = {
            "run_id": config.run_id,
            "model": config.model,
            "compatibility_key": config.compatibility_key,
            "prompt": {"path": str(config.prompt_path), "sha256": config.prompt_hash},
            "render": {
                "px_sent": config.max_px,
                "provider_cap": config.provider_cap,
                "effective_dpi": effective_dpi,
            },
            "generation": asdict(config.generation),
            "dataset": {
                "dir": str(config.dataset_dir),
                "version": config.dataset_version,
            },
            "library_versions": config.library_versions,
            "scoring": {
                "canonical_iou_threshold": CANONICAL_IOU_THRESHOLD,
                "iou_sweep": list(IOU_SWEEP),
            },
            "cost": {
                "spent_usd": cost_spent_usd,
            },
            "pages_total": pages_total,
            "pages_scored": pages_scored,
            "status": "partial" if pages_scored < pages_total else "complete",
        }
        return self._write_json(self._run_metadata_path(), metadata)
