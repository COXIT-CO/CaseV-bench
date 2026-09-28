"""Everything about describing a run in a cli-friendly way"""

import json
from pathlib import Path
from typing import Protocol


class RunObserver(Protocol):
    def run_started(
        self, *, run_id: str, model: str, dataset_dir: Path, drawings_total: int, pages_total: int
    ) -> None: ...

    def drawing_started(self, drawing: str, *, total_pages: int, cached_pages: int) -> None: ...

    def page_done(
        self,
        drawing: str,
        page: int,
        *,
        index: int,
        total: int,
        cost_usd: float | None,
        latency_seconds: float | None,
        error: str | None,
    ) -> None: ...

    def scoring_started(self) -> None: ...


class NullRunObserver:
    """Default observer: execute_run stays silent unless a caller opts in."""

    def run_started(
        self, *, run_id: str, model: str, dataset_dir: Path, drawings_total: int, pages_total: int
    ) -> None:
        pass

    def drawing_started(self, drawing: str, *, total_pages: int, cached_pages: int) -> None:
        pass

    def page_done(
        self,
        drawing: str,
        page: int,
        *,
        index: int,
        total: int,
        cost_usd: float | None,
        latency_seconds: float | None,
        error: str | None,
    ) -> None:
        pass

    def scoring_started(self) -> None:
        pass


class PrintingRunObserver:
    """Prints each step of a run as it happens."""

    def run_started(
        self, *, run_id: str, model: str, dataset_dir: Path, drawings_total: int, pages_total: int
    ) -> None:
        print(
            f"Run {run_id}: {model} on {drawings_total} drawing(s), "
            f"{pages_total} page(s) — dataset {dataset_dir}"
        )

    def drawing_started(self, drawing: str, *, total_pages: int, cached_pages: int) -> None:
        if cached_pages == total_pages:
            print(f"  {drawing}: {total_pages} page(s), already cached")
        elif cached_pages:
            print(
                f"  {drawing}: {total_pages} page(s) "
                f"({cached_pages} cached, {total_pages - cached_pages} to call)"
            )
        else:
            print(f"  {drawing}: {total_pages} page(s)")

    def page_done(
        self,
        drawing: str,
        page: int,
        *,
        index: int,
        total: int,
        cost_usd: float | None,
        latency_seconds: float | None,
        error: str | None,
    ) -> None:
        if error is not None:
            print(f"    page {index}/{total}: failed — {error}")
            return
        print(f"    page {index}/{total}: ok ({latency_seconds:.1f}s, ${cost_usd or 0.0:.4f})")

    def scoring_started(self) -> None:
        print("Scoring...")


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def format_run_summary(run_dir: Path, *, elapsed_seconds: float) -> str:
    """The short report printed once a run (or a rescore) finishes, read
    back from what execute_run/score_run already wrote to run_dir."""
    metadata = json.loads((run_dir / "run.json").read_text())
    lines = [
        f"Run {metadata['run_id']} ({metadata['status']}): {metadata['model']}",
        f"  pages scored: {metadata['pages_scored']}/{metadata['pages_total']}",
        f"  cost: ${metadata['cost']['spent_usd']:.4f}",
        f"  took: {_format_duration(elapsed_seconds)}",
    ]

    scores_path = run_dir / "scores.json"
    if scores_path.exists():
        threshold = f"{metadata['scoring']['canonical_iou_threshold']:.2f}"
        canonical = json.loads(scores_path.read_text())["scores"].get(threshold)
        if canonical is not None:
            metrics = canonical["metrics"]
            lines.append(
                f"  precision/recall/F1 @ IoU {threshold}: "
                f"{metrics['precision']:.3f}/{metrics['recall']:.3f}/{metrics['f1']:.3f}"
            )

    lines.append(f"  written to {run_dir}")
    return "\n".join(lines)
