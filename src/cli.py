import argparse
from pathlib import Path

DEFAULT_MODELS = [
    "anthropic/claude-sonnet-4.5",
    "openai/gpt-5-mini",
    "google/gemini-2.5-flash",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run object counting/location detection over a PDF's pages."
    )
    parser.add_argument("--project", default="prj0001")
    parser.add_argument("--pdf-path", type=Path, default=None)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--counting-prompt-version", default="v0001")
    parser.add_argument("--location-prompt-version", default="v0001")
    parser.add_argument("--overlay-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("data/output"))
    parser.add_argument("--logs-dir", type=Path, default=Path("src/logs"))
    parser.add_argument(
        "--task",
        choices=["object_counting", "object_location"],
        default="object_counting",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)

    if args.pdf_path is None:
        args.pdf_path = Path(f"data/input/{args.project}.pdf")
    if args.overlay_dir is None:
        args.overlay_dir = Path(f"data/output/{args.project}/object_location")

    args.counting_prompt_path = Path(
        f"src/prompts/object_counting/{args.counting_prompt_version}.md"
    )
    args.location_prompt_path = Path(
        f"src/prompts/object_location/{args.location_prompt_version}.md"
    )

    return args
