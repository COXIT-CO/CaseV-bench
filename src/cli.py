"""Argparse front-end for the CLI run path (ticket 13).

The CLI now drives the *same* shared services as the UI and writes to the same SQLite
store (ADR 0007), so its args map onto that model: a PDF to ingest as a Drawing, a Task,
the models, and which prompt version to pin. The legacy ``object_counting`` /
``object_location`` task names still parse so existing invocations keep working.
"""

import argparse
from pathlib import Path

from models.prompt import Task

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
