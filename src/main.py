"""CLI entry point, refactored onto the shared service layer (ticket 13, ADR 0007).

The CLI no longer writes JSON run logs. It ingests the PDF as a Drawing, resolves the
prompt version to pin, and launches through the same ``RunService`` the UI uses — so a
CLI run and an equivalent UI run are the same Run / Results / Predictions in the one
SQLite store, with no second source of truth to drift.

``execute_cli_run`` is the injectable seam: it takes an open ``Session`` and an
``OpenRouterAdapter`` so a test can drive the whole path against a temp DB with a stubbed
adapter. ``main`` wires the real engine + HTTP adapter and seeds the store exactly as the
web app's startup does, so the two entry points share identical seed data.
"""

from pathlib import Path

from sqlmodel import Session

from adapters.openrouter import HttpxOpenRouterAdapter, OpenRouterAdapter
from cli import parse_args
from db import init_db, make_engine
from models.prompt import Prompt, Task
from models.run import PredictionStatus, Run
from services.drawing import DEFAULT_CACHE_ROOT, DrawingService
from services.model_catalog import ModelCatalogService
from services.prompt import DEFAULT_FAMILY, PromptService, seed_default_prompts
from services.run import DEFAULT_OVERLAY_ROOT, RunService


def resolve_prompt(
    session: Session, task: Task, family: str, version: int | None
) -> Prompt:
    """The prompt version a Run pins: an explicit ``version`` within ``(task, family)``,
    or the family's latest when none is given. Raises if it doesn't exist."""
    service = PromptService(session)
    if version is None:
        prompt = service.latest(task, family)
        if prompt is None:
            raise ValueError(
                f"no {task.value} prompt family {family!r} to run; author one first"
            )
        return prompt

    prompt = service.get(task, family, version)
    if prompt is None:
        raise ValueError(f"no {task.value} prompt {family!r} v{version}")
    return prompt


def execute_cli_run(
    session: Session,
    adapter: OpenRouterAdapter,
    *,
    pdf_path: Path,
    name: str,
    task: Task,
    models: list[str],
    prompt_family: str = DEFAULT_FAMILY,
    prompt_version: int | None = None,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    overlay_root: Path = DEFAULT_OVERLAY_ROOT,
) -> Run:
    """Ingest the PDF, resolve the prompt, and launch a Run to completion through the
    shared services — landing the same rows a UI run produces. Returns the finished Run.
    """
    drawing = DrawingService(session, cache_root=cache_root).ingest(pdf_path, name=name)
    prompt = resolve_prompt(session, task, prompt_family, prompt_version)
    slugs = ModelCatalogService.resolve_selection(models)

    run = RunService(session, adapter, overlay_root=overlay_root).launch(
        task, prompt.id, drawing.id, slugs
    )
    _print_summary(run)
    return run


def _print_summary(run: Run) -> None:
    """A concise post-run report to stdout — the CLI's view of what landed in the DB."""
    print(f"\nRun {run.id}: {run.task.value} — {run.status.value}")
    print(
        f"drawing {run.drawing_id}, prompt {run.prompt_id}, {run.progress}/{run.total_units} units"
    )
    for result in run.results:
        ok = sum(1 for p in result.predictions if p.status == PredictionStatus.ok)
        total = len(result.predictions)
        print(f"  {result.model}: {ok}/{total} pages ok")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    engine = make_engine()
    init_db(engine)
    adapter = HttpxOpenRouterAdapter()
    with Session(engine) as session:
        # Mirror the web app's startup seeding so both entry points share seed data.
        seed_default_prompts(session)
        ModelCatalogService(session).seed_defaults()
        execute_cli_run(
            session,
            adapter,
            pdf_path=args.pdf_path,
            name=args.name,
            task=args.task,
            models=args.models,
            prompt_family=args.prompt_family,
            prompt_version=args.prompt_version,
        )


if __name__ == "__main__":
    main()
