import random
import sys
import time
from pathlib import Path

import pymupdf

from core.artifacts import RunArtifacts
from core.client import GenerationParams, ModelClient, ModelClientError, ModelResponse
from core.config import MODEL_ROSTER, RunConfig
from core.dataset import DrawingGroundTruth, LocalDatasetSource, resolve_dataset
from core.parse import ResponseParser, ZeroDetectionsError
from core.render import PageRenderer
from core.scoring import Box, DrawingScore, ScorerWrapper

# Technical failures are retried this many times in total before the page
# is recorded with its error and excluded from scoring.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_MAX_SECONDS = 60.0
# Beneath the run's own out-dir, so renders are reused across runs and
# across models sharing a provider cap, without a dedicated flag.
RENDER_CACHE_DIRNAME = ".render-cache"


class RawPipeline:
    """Renders each page once, sends it to the model with a single prompt,
    and writes call records, predictions, and scores under the run
    directory."""

    def __init__(self, client: ModelClient) -> None:
        self._client = client

    @staticmethod
    def score_run(run_dir: Path, dataset_dir: Path) -> None:
        """Parse and score an existing run directory; makes no model calls,
        so it needs no client and can be called without an instance."""
        drawings = LocalDatasetSource(dataset_dir).load()
        artifacts = RunArtifacts(run_dir)
        scorer = ScorerWrapper()
        parser = ResponseParser()
        total_scored_pages = 0
        total_predictions = 0
        drawing_scores: list[DrawingScore] = []

        for drawing_name, drawing in sorted(drawings.items()):
            if not (run_dir / drawing_name).is_dir():
                continue

            predictions: list[Box] = []
            scored_pages: set[int] = set()
            for page in drawing.pages:
                record = artifacts.read_call_record_if_exists(drawing_name, page.page)
                if not RunArtifacts.call_succeeded(record):
                    continue
                scored_pages.add(page.page)
                parsed = parser.parse_response(record["response_text"], page.page)
                predictions.extend(parsed.boxes)
                artifacts.write_call_parse_accounting(
                    drawing_name, page.page, dropped=parsed.dropped, complete=parsed.complete
                )

            total_scored_pages += len(scored_pages)
            total_predictions += len(predictions)
            ground_truth = [
                box
                for box in RawPipeline._ground_truth_boxes(drawing)
                if box["page"] in scored_pages
            ]

            artifacts.write_predictions(drawing_name, predictions)
            drawing_score = scorer.score_drawing(drawing_name, predictions, ground_truth)
            artifacts.write_drawing_score(drawing_score)
            drawing_scores.append(drawing_score)

        if drawing_scores:
            artifacts.write_run_score(scorer.aggregate_run_score(drawing_scores))

        if total_scored_pages > 0 and total_predictions == 0:
            raise ZeroDetectionsError(
                f"every one of {total_scored_pages} scored page(s) in {run_dir} produced zero "
                "boxes — this usually means a parser/prompt format mismatch, not a model that "
                "found nothing"
            )

    @staticmethod
    def _ground_truth_boxes(drawing: DrawingGroundTruth) -> list[Box]:
        return [
            {
                "object_type": box.label,
                "bbox": [box.x_min, box.y_min, box.x_max, box.y_max],
                "page": page.page,
            }
            for page in drawing.pages
            for box in page.boxes
        ]

    @staticmethod
    def _backoff_delay(retry_after: float | None, attempt: int) -> float:
        if retry_after is not None:
            return max(retry_after, 0.0)
        cap = min(DEFAULT_BACKOFF_MAX_SECONDS, DEFAULT_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        return random.uniform(0.0, cap)

    def _generate_with_retries(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        params: GenerationParams,
        max_attempts: int,
    ) -> ModelResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._client.generate(
                    model=model,
                    prompt=prompt,
                    image_bytes=image_bytes,
                    image_mime_type="image/png",
                    params=params,
                )
            except ModelClientError as exc:
                if attempt >= max_attempts:
                    raise
                time.sleep(self._backoff_delay(exc.retry_after, attempt))

    def execute_run(
        self,
        *,
        model: str,
        dataset_dir: Path | None,
        run_id: str,
        out_dir: Path,
        max_px: int,
        render_cache_dir: Path | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        cost_cap_usd: float | None = None,
    ) -> Path:
        dataset_dir, drawings = resolve_dataset(dataset_dir)
        provider_cap = MODEL_ROSTER.get(model)
        if provider_cap is None:
            print(
                f"warning: no image-size entry for model {model!r} in the roster; "
                f"using the default {max_px}px",
                file=sys.stderr,
            )
        config = RunConfig.build(
            model=model,
            run_id=run_id,
            dataset_dir=dataset_dir,
            max_px=provider_cap or max_px,
            provider_cap=provider_cap,
            requested_max_px=max_px,
        )
        run_dir = out_dir / run_id
        cache_dir = render_cache_dir or out_dir / RENDER_CACHE_DIRNAME
        artifacts = RunArtifacts(run_dir)
        renderer = PageRenderer(cache_dir)

        pages_scored = 0
        cost_spent_usd = 0.0
        cost_cap_hit = False
        # First page's achieved DPI, representative for the run — see
        # write_run_metadata's docstring for when a later page's own record differs.
        run_effective_dpi: float | None = None

        for drawing_name, drawing in sorted(drawings.items()):
            with pymupdf.open(drawing.pdf_path) as document:
                for page in drawing.pages:
                    existing = artifacts.read_call_record_if_exists(drawing_name, page.page)
                    if RunArtifacts.call_succeeded(existing):
                        pages_scored += 1
                        run_effective_dpi = run_effective_dpi or existing["render"]["effective_dpi"]
                        cost_spent_usd += existing["usage"].get("cost_usd") or 0.0
                        continue

                    if cost_cap_usd is not None and cost_spent_usd >= cost_cap_usd:
                        cost_cap_hit = True
                        continue

                    rendered = renderer.render(
                        document, drawing_name, page.page, target_long_edge=config.max_px
                    )
                    run_effective_dpi = run_effective_dpi or rendered.effective_dpi

                    try:
                        response = self._generate_with_retries(
                            model=model,
                            prompt=config.prompt_text,
                            image_bytes=rendered.png_bytes,
                            params=config.generation,
                            max_attempts=max_attempts,
                        )
                    except ModelClientError as exc:
                        artifacts.write_call_failure(
                            drawing=drawing_name,
                            page=page.page,
                            source_page=page.source_page,
                            model=model,
                            error=str(exc),
                            attempts=max_attempts,
                        )
                        continue

                    cost_spent_usd += response.usage.cost_usd or 0.0
                    pages_scored += 1
                    artifacts.write_call_record(
                        drawing=drawing_name,
                        page=page.page,
                        source_page=page.source_page,
                        model=model,
                        response=response,
                        rendered=rendered,
                    )

        artifacts.write_run_metadata(
            config=config,
            effective_dpi=run_effective_dpi or 0.0,
            pages_total=sum(d.page_count for d in drawings.values()),
            pages_scored=pages_scored,
            cost_spent_usd=cost_spent_usd,
            cost_cap_usd=cost_cap_usd,
            cost_cap_hit=cost_cap_hit,
        )
        RawPipeline.score_run(run_dir, dataset_dir)
        return run_dir
