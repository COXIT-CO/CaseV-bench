import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from core.client import ApiKeyError, ModelClient, ModelClientError, OpenRouterClient
from core.config import DEFAULT_MAX_PX
from core.dataset import DatasetError, DrawingGroundTruth, resolve_dataset
from core.parse import ZeroDetectionsError
from core.pipelines.raw import RawPipeline

DATASET_DIR_ENV_VAR = "CASEV_DATASET_DIR"
DEFAULT_OUT_DIR = "results"
DEFAULT_THREADS = 1


class Cli:
    def __init__(self, client: ModelClient | None = None) -> None:
        self._client = client

    @staticmethod
    def _fail(exc: Exception, *, exit_code: int = 1) -> int:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code

    @staticmethod
    def _format_report(drawings: dict[str, DrawingGroundTruth]) -> str:
        lines: list[str] = []
        for name, drawing in sorted(drawings.items()):
            lines.append(f"{name} ({drawing.page_count} pages, {drawing.object_count} objects)")
            lines.extend(f"  {label}: {n}" for label, n in sorted(drawing.label_counts().items()))
            if drawing.rejected:
                detail = ", ".join(
                    f"{r}: {n}" for r, n in sorted(drawing.rejected_counts().items())
                )
                lines.append(f"  rejected: {len(drawing.rejected)} ({detail})")

        pages = sum(d.page_count for d in drawings.values())
        objects = sum(d.object_count for d in drawings.values())
        rejected = sum(len(d.rejected) for d in drawings.values())
        lines.append("")
        lines.append(
            f"Total: {len(drawings)} drawings, {pages} pages, "
            f"{objects} objects, {rejected} rejected"
        )
        return "\n".join(lines)

    def _cmd_validate(self, args: argparse.Namespace) -> int:
        try:
            resolved_dir, drawings = resolve_dataset(
                Path(args.dataset_dir) if args.dataset_dir else None
            )
        except DatasetError as exc:
            return self._fail(exc)

        print(f"Dataset OK: {resolved_dir}\n")
        print(self._format_report(drawings))
        return 0

    @staticmethod
    def _positive_int(raw: str) -> int:
        value = int(raw)
        if value < 1:
            raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
        return value

    @staticmethod
    def _default_client() -> ModelClient:
        client = OpenRouterClient.from_env()
        client.check_api_key()
        return client

    def _cmd_run(self, args: argparse.Namespace) -> int:
        try:
            pipeline = RawPipeline(self._client or self._default_client())
            run_dir = pipeline.execute_run(
                model=args.model,
                dataset_dir=Path(args.dataset_dir) if args.dataset_dir else None,
                run_id=args.run_id or f"{args.model.replace('/', '-')}__{date.today():%Y%m%d}",
                out_dir=Path(args.out_dir),
                max_px=args.max_px,
                threads=args.threads,
            )
        except ApiKeyError as exc:
            return self._fail(exc, exit_code=2)
        except (DatasetError, ModelClientError, ZeroDetectionsError) as exc:
            return self._fail(exc)

        print(f"Run written to {run_dir}")
        return 0

    def _cmd_score(self, args: argparse.Namespace) -> int:
        run_dir = Path(args.run_dir)
        run_json_path = run_dir / "run.json"
        if not run_json_path.exists():
            return self._fail(FileNotFoundError(f"no run.json found in {run_dir}"))

        dataset_dir = args.dataset_dir or json.loads(run_json_path.read_text())["dataset"]["dir"]
        try:
            RawPipeline.score_run(run_dir, Path(dataset_dir))
        except (DatasetError, ZeroDetectionsError) as exc:
            return self._fail(exc)

        print(f"Scored {run_dir}")
        return 0

    @staticmethod
    def _add_dataset_arguments(parser: argparse.ArgumentParser, *, verb: str) -> None:
        parser.add_argument(
            "--dataset-dir",
            default=os.environ.get(DATASET_DIR_ENV_VAR),
            help=f"dataset directory to {verb} against (default: ${DATASET_DIR_ENV_VAR})",
        )

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="casev")
        subparsers = parser.add_subparsers(dest="command", required=True)

        validate_parser = subparsers.add_parser(
            "validate", help="check the dataset without calling any model"
        )
        self._add_dataset_arguments(validate_parser, verb="verify")
        validate_parser.set_defaults(func=self._cmd_validate)

        run_parser = subparsers.add_parser(
            "run", help="render, call the model, and write artifacts"
        )
        run_parser.add_argument(
            "--model", required=True, help="model slug, e.g. google/gemini-3-flash"
        )
        self._add_dataset_arguments(run_parser, verb="run")
        run_parser.add_argument(
            "--run-id",
            default=None,
            help="explicit run identifier, for deterministic resume/reruns (default: derived)",
        )
        run_parser.add_argument(
            "--out-dir", default=DEFAULT_OUT_DIR, help="where to write run directories"
        )
        run_parser.add_argument(
            "--max-px",
            type=int,
            default=DEFAULT_MAX_PX,
            help="target long edge in pixels, used when --model has no entry in the roster",
        )
        run_parser.add_argument(
            "--threads",
            type=self._positive_int,
            default=DEFAULT_THREADS,
            help=f"number of pages to call the model for concurrently (default: {DEFAULT_THREADS})",
        )
        run_parser.set_defaults(func=self._cmd_run)

        score_parser = subparsers.add_parser(
            "score", help="parse and score an existing run directory; no network access"
        )
        score_parser.add_argument("--run-dir", required=True, help="an existing run directory")
        score_parser.add_argument(
            "--dataset-dir",
            default=None,
            help="dataset directory (default: read from the run's own run.json)",
        )
        score_parser.set_defaults(func=self._cmd_score)

        return parser

    def run(self, argv: Sequence[str] | None = None) -> int:
        args = self.build_parser().parse_args(argv)
        result: int = args.func(args)
        return result


def main(argv: Sequence[str] | None = None, *, client: ModelClient | None = None) -> int:
    return Cli(client).run(argv)


if __name__ == "__main__":
    sys.exit(main())
