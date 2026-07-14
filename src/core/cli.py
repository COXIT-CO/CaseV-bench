"""CLI entry point, refactored onto the shared service layer (ADR 0007).

The CLI drives the *same* shared services as the UI and writes to the same SQLite store,
so a CLI run and an equivalent UI run are the same Run / Results / Predictions in the one
store, with no second source of truth to drift. Its args map onto that model: a PDF to
ingest as a Drawing, a Task, the models, and which prompt version to pin. The legacy
``object_counting`` / ``object_location`` task names still parse so existing invocations
keep working.

``execute_cli_run`` is the injectable seam: it takes an open ``Session`` and an
``OpenRouterAdapter`` so a test can drive the whole path against a temp DB with a stubbed
adapter. ``main`` wires the real engine + HTTP adapter and seeds the store exactly as the
web app's startup does, so the two entry points share identical seed data.
"""

import argparse
from pathlib import Path

from sqlmodel import Session

from core.adapters.openrouter import HttpxOpenRouterAdapter, OpenRouterAdapter
from core.db import init_db, make_engine
from core.models.prompt import Prompt, Task
from core.models.run import PredictionStatus, Run
from core.services.drawing import DEFAULT_CACHE_ROOT, DrawingService
from core.services.model_catalog import ModelCatalogService
from core.services.prompt import DEFAULT_FAMILY, PromptService, seed_default_prompts
from core.services.run import (
    DEFAULT_OVERLAY_ROOT,
    RunService,
    reconcile_orphaned_runs,
)

DEFAULT_MODELS = [
    "anthropic/claude-sonnet-4.5",
    "openai/gpt-5-mini",
    "google/gemini-2.5-flash",
]

# Accept the current Task names and the POC's ``object_*`` spellings, both mapped to the
# canonical Task the shared services speak.
TASK_ALIASES = {
    "counting": Task.counting,
    "object_counting": Task.counting,
    "location": Task.location,
    "object_location": Task.location,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run object counting / location detection over a PDF's pages, "
        "persisting to the shared SQLite store."
    )
    parser.add_argument("--project", default="prj0001")
    parser.add_argument("--pdf-path", type=Path, default=None)
    parser.add_argument(
        "--name", default=None, help="Drawing name (defaults to the PDF stem)."
    )
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--task", choices=sorted(TASK_ALIASES), default="counting")
    parser.add_argument(
        "--prompt-family",
        default="default",
        help="Prompt family to run (its version is pinned on the Run).",
    )
    parser.add_argument(
        "--prompt-version",
        type=int,
        default=None,
        help="Prompt version to pin; defaults to the family's latest.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)

    if args.pdf_path is None:
        args.pdf_path = Path(f"data/input/{args.project}.pdf")
    if args.name is None:
        args.name = args.pdf_path.stem
    args.task = TASK_ALIASES[args.task]

    return args


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
        # ...and its startup reconciliation (ticket 04): clear any run orphaned by a
        # prior interrupted process before launching this one.
        swept = reconcile_orphaned_runs(session)
        if swept:
            print(
                f"Reconciled {swept} orphaned run(s) left 'running' by a prior process."
            )
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
