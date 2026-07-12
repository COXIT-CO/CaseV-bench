"""Run execution — the shared service that launches a counting Run and persists
its Results + Predictions (spec: Runs & execution; ticket 05).

This is the single primary test seam (spec: Testing Decisions): it takes an
``OpenRouterAdapter`` so tests stub the only external I/O boundary. Execution here
is **synchronous** — ``launch`` creates the Run and runs it to completion inline so
the whole vertical path (launch → OpenRouter call → parse → persist) is provable end
to end. Ticket 06 keeps ``create_run`` / ``execute_run`` split so it can move
``execute_run`` onto an in-process background task without touching this logic.

Models each become a ``Result``; every Page becomes a ``Prediction`` under it —
counting counts parsed from the model's JSON, or a failure record. JSON is obtained
via the existing prefill + strip-fence approach and retried once before a failure is
recorded; a model failure never aborts the Run (spec: Runs 20, 21).
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from sqlmodel import Session

from adapters.openrouter import DEFAULT_MAX_TOKENS, OpenRouterAdapter
from models.drawing import Drawing, Page
from models.prompt import Prompt, Task
from models.results import CountResult
from models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from services.pdf_processing import DEFAULT_DPI
from utils import DEFAULT_DOWNSAMPLE_PX, parse_json

# A Run pins a fixed temperature for reproducibility (spec: knobs recorded but fixed).
DEFAULT_TEMPERATURE = 0.0


@dataclass(frozen=True)
class RunKnobs:
    """The fixed, non-tunable knobs a Run snapshots (glossary: Configuration is
    ``(prompt version, model)`` only; everything here is recorded but fixed in v1).
    Field names match the ``Run`` columns so the snapshot copies by ``asdict``."""

    dpi: int = DEFAULT_DPI
    downsample_px: int = DEFAULT_DOWNSAMPLE_PX
    max_tokens: int = DEFAULT_MAX_TOKENS
    prefill: bool = True
    temperature: float = DEFAULT_TEMPERATURE


class RunService:
    def __init__(
        self,
        session: Session,
        adapter: OpenRouterAdapter,
        knobs: RunKnobs = RunKnobs(),
    ):
        self.session = session
        self.adapter = adapter
        self.knobs = knobs

    def launch(
        self, task: Task, prompt_id: int, drawing_id: int, models: list[str]
    ) -> Run:
        """Create a Run and execute it synchronously to completion (ticket 05)."""
        run = self.create_run(task, prompt_id, drawing_id, models)
        self.execute_run(run.id)
        self.session.refresh(run)
        return run

    def create_run(
        self, task: Task, prompt_id: int, drawing_id: int, models: list[str]
    ) -> Run:
        """Insert a ``queued`` Run with one Result per model and the knob snapshot."""
        if task != Task.counting:
            raise ValueError(f"only counting Runs are supported (got {task.value})")
        prompt = self.session.get(Prompt, prompt_id)
        if prompt is None or prompt.task != task:
            raise ValueError(f"no {task.value} prompt with id {prompt_id}")
        drawing = self.session.get(Drawing, drawing_id)
        if drawing is None:
            raise ValueError(f"no drawing with id {drawing_id}")
        if not models:
            raise ValueError("a Run needs at least one model")

        run = Run(
            task=task,
            prompt_id=prompt_id,
            drawing_id=drawing_id,
            status=RunStatus.queued,
            total_units=len(models) * len(drawing.pages),
            **asdict(self.knobs),
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        for model in models:
            self.session.add(Result(run_id=run.id, model=model))
        self.session.commit()
        self.session.refresh(run)
        return run

    def execute_run(self, run_id: int) -> None:
        """Call OpenRouter for each (Result, Page), persisting a Prediction each and
        advancing progress. Any model failure — a parse failure or the OpenRouter
        call itself raising — is recorded as a failure Prediction and never aborts the
        Run, so other models' results stay usable (spec: Runs 21). Only a genuine
        persistence error marks the Run ``failed``."""
        run = self.session.get(Run, run_id)
        prompt = self.session.get(Prompt, run.prompt_id)
        drawing = self.session.get(Drawing, run.drawing_id)

        run.status = RunStatus.running
        self.session.commit()

        try:
            for result in run.results:
                for page in drawing.pages:
                    self.session.add(
                        self._predict_counting(run, result, page, prompt.text)
                    )
                    run.progress += 1
                    self.session.commit()
            run.status = RunStatus.done
        except Exception:
            run.status = RunStatus.failed
            self.session.commit()
            raise

        self.session.commit()

    def _predict_counting(
        self, run: Run, result: Result, page: Page, prompt_text: str
    ) -> Prediction:
        """One page's counting Prediction: prefill + strip-fence parse, retried once
        before recording a failure (spec: Runs 20). A failing OpenRouter call is
        recorded like a parse failure rather than propagated, so one model's error
        doesn't abort the Run (spec: Runs 21)."""
        raw_content: str | None = None
        error: str | None = None

        # Initial attempt plus a single retry.
        for _ in range(2):
            try:
                response = self.adapter.send_image_prompt(
                    Path(page.image_path),
                    result.model,
                    prompt_text,
                    prefill_json=run.prefill,
                    max_tokens=run.max_tokens,
                    temperature=run.temperature,
                )
                raw_content = response["choices"][0]["message"]["content"]
                counts = CountResult(**parse_json(raw_content))
            except Exception as exc:
                error = str(exc)
                continue
            return Prediction(
                result_id=result.id,
                page_id=page.id,
                page_number=page.page_number,
                status=PredictionStatus.ok,
                raw_content=raw_content,
                parsed_json=counts.model_dump_json(),
            )

        return Prediction(
            result_id=result.id,
            page_id=page.id,
            page_number=page.page_number,
            status=PredictionStatus.error,
            raw_content=raw_content,
            parse_error=error,
        )
