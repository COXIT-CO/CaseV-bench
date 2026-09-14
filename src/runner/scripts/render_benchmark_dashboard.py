"""Render the weekly-benchmark dashboard section of the root README from the `service` schema"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from core.config import MODEL_ROSTER

DATABASE_URL_ENV_VAR = "CASEV_SERVICE_DATABASE_URL"
START_MARKER = "<!-- BENCHMARK_DASHBOARD:START -->"
END_MARKER = "<!-- BENCHMARK_DASHBOARD:END -->"
DEFAULT_README_PATH = Path(__file__).resolve().parents[3] / "README.md"

QUERY = """
SELECT r.model, (r.created_at AT TIME ZONE 'UTC')::date AS run_date,
       SUM(d.tp) AS tp, SUM(d.fp) AS fp, SUM(d.fn) AS fn
FROM service.run_documents d
JOIN service.runs r ON r.run_id = d.run_id
WHERE d.iou_threshold = %(iou_threshold)s
GROUP BY r.model, run_date
ORDER BY r.model, run_date DESC
"""


@dataclass(frozen=True, slots=True)
class WeekScore:
    run_date: date
    f1: float


def _rates(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def fetch_weekly_scores(database_url: str, *, iou_threshold: Decimal) -> dict[str, list[WeekScore]]:
    by_model: dict[str, list[WeekScore]] = {}
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(QUERY, {"iou_threshold": iou_threshold})
        for model, run_date, tp, fp, fn in cur.fetchall():
            _, _, f1 = _rates(tp, fp, fn)
            by_model.setdefault(model, []).append(WeekScore(run_date, f1))
    return by_model


def render_table(models: list[str], by_model: dict[str, list[WeekScore]]) -> str:
    lines = [
        "| Model | F1 @ IoU 0.5 | Δ vs. previous run | Last run |",
        "|---|---|---|---|",
    ]
    for model in models:
        weeks = by_model.get(model, [])
        if not weeks:
            lines.append(f"| `{model}` | — | — | no runs yet |")
            continue
        latest, *rest = weeks
        delta = latest.f1 - rest[0].f1 if rest else 0.0
        arrow = "▲" if delta > 0 else "▼" if delta < 0 else "•"
        run_date = latest.run_date.isoformat()
        lines.append(f"| `{model}` | {latest.f1:.3f} | {arrow} {delta:+.3f} | {run_date} |")
    return "\n".join(lines)


def update_readme(readme_path: Path, table: str) -> bool:
    text = readme_path.read_text()
    start = text.index(START_MARKER)
    end = text.index(END_MARKER)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    section = (
        f"{START_MARKER}\n"
        f"_Last updated {generated_at} by the weekly benchmark workflow._\n\n"
        f"{table}\n\n"
        f"{END_MARKER}"
    )
    new_text = text[:start] + section + text[end + len(END_MARKER) :]
    if new_text == text:
        return False
    readme_path.write_text(new_text)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iou-threshold", default="0.500")
    parser.add_argument("--readme-path", type=Path, default=DEFAULT_README_PATH)
    args = parser.parse_args(argv)

    database_url = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if not database_url:
        print(f"error: {DATABASE_URL_ENV_VAR} is not set", file=sys.stderr)
        return 2

    by_model = fetch_weekly_scores(database_url, iou_threshold=Decimal(args.iou_threshold))
    table = render_table(sorted(MODEL_ROSTER), by_model)
    changed = update_readme(args.readme_path, table)
    print("README dashboard updated" if changed else "README dashboard unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
