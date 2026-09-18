"""Publish a completed `casev run` run to the `service` schema"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from core.artifacts import RunArtifacts
from core.dataset import resolve_dataset

DATABASE_URL_ENV_VAR = "CASEV_SERVICE_DATABASE_URL"

INSERT_RUN_SQL = """
INSERT INTO service.runs
    (run_id, model, status, pages_total, pages_scored,
     prompt_path, prompt_sha256, render_px_sent, render_provider_cap, render_effective_dpi,
     generation_temperature, generation_max_output_tokens, dataset_dir, dataset_version,
     scorer_version, canonical_iou_threshold, iou_sweep, cost_spent_usd)
VALUES
    (%(run_id)s, %(model)s, %(status)s, %(pages_total)s,
     %(pages_scored)s, %(prompt_path)s, %(prompt_sha256)s, %(render_px_sent)s,
     %(render_provider_cap)s, %(render_effective_dpi)s, %(generation_temperature)s,
     %(generation_max_output_tokens)s, %(dataset_dir)s, %(dataset_version)s, %(scorer_version)s,
     %(canonical_iou_threshold)s, %(iou_sweep)s, %(cost_spent_usd)s)
"""

INSERT_DOCUMENT_SQL = """
INSERT INTO service.run_documents
    (id, run_id, document_id, iou_threshold, tp, fp, fn, precision, recall, f1, scorer_output)
VALUES
    (%(id)s, %(run_id)s, %(document_id)s, %(iou_threshold)s, %(tp)s, %(fp)s, %(fn)s,
     %(precision)s, %(recall)s, %(f1)s, %(scorer_output)s)
"""

INSERT_PAGE_SQL = """
INSERT INTO service.run_pages
    (id, run_id, document_id, page, status, response_text, finish_reason,
     prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_seconds, error, attempts)
VALUES
    (%(id)s, %(run_id)s, %(document_id)s, %(page)s, %(status)s,
     %(response_text)s, %(finish_reason)s, %(prompt_tokens)s, %(completion_tokens)s,
     %(total_tokens)s, %(cost_usd)s, %(latency_seconds)s, %(error)s, %(attempts)s)
"""


def _threshold_key(threshold: float) -> str:
    return f"{threshold:.2f}"


def build_run_row(run_json: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run_json["run_id"],
        "model": run_json["model"],
        "status": run_json["status"],
        "pages_total": run_json["pages_total"],
        "pages_scored": run_json["pages_scored"],
        "prompt_path": run_json["prompt"]["path"],
        "prompt_sha256": run_json["prompt"]["sha256"],
        "render_px_sent": run_json["render"]["px_sent"],
        "render_provider_cap": run_json["render"]["provider_cap"],
        "render_effective_dpi": Decimal(f"{run_json['render']['effective_dpi']:.4f}"),
        "generation_temperature": Decimal(f"{run_json['generation']['temperature']:.3f}"),
        "generation_max_output_tokens": run_json["generation"]["max_output_tokens"],
        "dataset_dir": run_json["dataset"]["dir"],
        "dataset_version": run_json["dataset"]["version"],
        "scorer_version": f"location-scorer-v{run_json['library_versions']['location-scorer']}",
        "canonical_iou_threshold": Decimal(f"{run_json['scoring']['canonical_iou_threshold']:.3f}"),
        "iou_sweep": json.dumps(run_json["scoring"]["iou_sweep"]),
        "cost_spent_usd": Decimal(f"{run_json['cost']['spent_usd']:.6f}"),
    }


def build_document_rows(
    run_dir: Path, drawings: dict[str, Any], *, run_id: str, canonical_threshold: float
) -> list[dict[str, Any]]:
    """One row per drawing that has ground truth for its scored pages."""
    threshold_key = _threshold_key(canonical_threshold)
    rows: list[dict[str, Any]] = []
    for drawing_name, drawing in sorted(drawings.items()):
        score_path = run_dir / drawing_name / "scores.json"
        if not score_path.exists():
            continue
        drawing_score = json.loads(score_path.read_text())
        if drawing_score["unscored"]:
            continue

        threshold_score = drawing_score["scores"][threshold_key]
        counts = threshold_score["counts"]
        metrics = threshold_score["metrics"]
        rows.append(
            {
                "id": uuid.uuid4(),
                "run_id": run_id,
                "document_id": drawing.pdf_path.name,
                "iou_threshold": Decimal(f"{canonical_threshold:.3f}"),
                "tp": counts["tp"],
                "fp": counts["fp"],
                "fn": counts["fn"],
                "precision": Decimal(f"{metrics['precision']:.5f}"),
                "recall": Decimal(f"{metrics['recall']:.5f}"),
                "f1": Decimal(f"{metrics['f1']:.5f}"),
                "scorer_output": json.dumps(drawing_score),
            }
        )
    return rows


def build_page_rows(
    run_dir: Path, drawings: dict[str, Any], *, run_id: str
) -> list[dict[str, Any]]:
    artifacts = RunArtifacts(run_dir)
    rows: list[dict[str, Any]] = []
    for drawing_name, drawing in sorted(drawings.items()):
        for page in drawing.pages:
            record = artifacts.read_call_record_if_exists(drawing_name, page.page)
            if record is None:
                continue

            usage: dict[str, Any] = record.get("usage") or {}
            latency = record.get("latency_seconds")
            cost_usd = usage.get("cost_usd")
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "run_id": run_id,
                    "document_id": drawing.pdf_path.name,
                    "page": page.page,
                    "status": record["status"],
                    "response_text": record.get("response_text"),
                    "finish_reason": record.get("finish_reason"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "cost_usd": Decimal(f"{cost_usd:.6f}") if cost_usd is not None else None,
                    "latency_seconds": (Decimal(f"{latency:.3f}") if latency is not None else None),
                    "error": record.get("error"),
                    "attempts": record.get("attempts"),
                }
            )
    return rows


def publish(
    *,
    run_row: dict[str, Any],
    document_rows: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
    database_url: str,
) -> None:
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(INSERT_RUN_SQL, run_row)
        if page_rows:
            cur.executemany(INSERT_PAGE_SQL, page_rows)
        if document_rows:
            cur.executemany(INSERT_DOCUMENT_SQL, document_rows)
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="a completed run directory")
    parser.add_argument(
        "--dataset-dir",
        default=None,
        type=Path,
        help="override the dataset dir recorded in run.json (default: read from run.json)",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if not database_url:
        print(f"error: {DATABASE_URL_ENV_VAR} is not set", file=sys.stderr)
        return 2

    run_json = json.loads((args.run_dir / "run.json").read_text())
    _, drawings = resolve_dataset(args.dataset_dir or Path(run_json["dataset"]["dir"]))

    run_row = build_run_row(run_json)
    document_rows = build_document_rows(
        args.run_dir,
        drawings,
        run_id=run_row["run_id"],
        canonical_threshold=run_json["scoring"]["canonical_iou_threshold"],
    )
    page_rows = build_page_rows(args.run_dir, drawings, run_id=run_row["run_id"])

    try:
        publish(
            run_row=run_row,
            document_rows=document_rows,
            page_rows=page_rows,
            database_url=database_url,
        )
    except psycopg.Error as exc:
        print(f"error: failed to publish to the service store: {exc}", file=sys.stderr)
        return 1

    for row in document_rows:
        print(f"published {run_row['model']} / {row['document_id']}: f1={row['f1']}")
    print(
        f"published run {run_row['run_id']} ({len(document_rows)} document(s), "
        f"{len(page_rows)} page(s)) to service.runs"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
