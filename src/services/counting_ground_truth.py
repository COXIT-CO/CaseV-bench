"""CountingGroundTruth entry — record the correct per-label counting totals for a
Drawing (spec: Ground truth; ADR 0002; ticket 07).

The trusted answer counting scoring compares against. Totals are stored one row per
``(drawing, label)``; ``save`` upserts each provided label so re-entering a Drawing's
totals edits in place, and ``get_totals`` reads them back keyed by label to pre-fill
the form. Labels are validated against the fixed object taxonomy.
"""

from collections.abc import Mapping

from sqlmodel import Session, select

from models.counting_ground_truth import CountingGroundTruth
from models.results import OBJECT_LABELS


class CountingGroundTruthService:
    def __init__(self, session: Session):
        self.session = session

    def get_totals(self, drawing_id: int) -> dict[str, int]:
        """The stored total per label for a Drawing, keyed by label; empty if none
        have been entered yet (so callers can tell "unentered" from "entered zero")."""
        rows = self.session.exec(
            select(CountingGroundTruth).where(
                CountingGroundTruth.drawing_id == drawing_id
            )
        )
        return {row.label: row.total for row in rows}

    def save(self, drawing_id: int, totals: Mapping[str, int]) -> None:
        """Create or update the counting ground truth for a Drawing.

        Upserts one row per provided label; a label already recorded for the Drawing
        is updated in place. Rejects any label outside the fixed object taxonomy.
        """
        unknown = set(totals) - set(OBJECT_LABELS)
        if unknown:
            raise ValueError(f"labels outside taxonomy: {sorted(unknown)}")

        existing = {
            row.label: row
            for row in self.session.exec(
                select(CountingGroundTruth).where(
                    CountingGroundTruth.drawing_id == drawing_id
                )
            )
        }
        for label, total in totals.items():
            row = existing.get(label)
            if row is None:
                self.session.add(
                    CountingGroundTruth(drawing_id=drawing_id, label=label, total=total)
                )
            else:
                row.total = total
                self.session.add(row)
        self.session.commit()
