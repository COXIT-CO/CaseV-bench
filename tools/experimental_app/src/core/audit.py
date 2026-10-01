"""One-off data audits an operator runs against a live store.

Run against the configured data root (``CASEV_DATA_ROOT``), so the same command answers the
question locally and over a ``railway ssh`` session on the production Volume::

    PYTHONPATH=src poetry run python -m core.audit

Currently one check: degenerate LocationGroundTruth boxes (ADR 0031). It **exits non-zero when
it finds any**, so it can gate the port to ``location-scorer`` — after the port a degenerate GT
box raises at score time rather than silently capping the Drawing's recall, and a store that
still holds one would make the port's parity run fail for a pre-existing reason.

Read-only: it reports what it finds and never edits a row, since correcting a bad box means
re-importing a fixed source file, not patching the store.
"""

import sys

from sqlmodel import Session

from core.db import make_engine
from core.services.location_ground_truth import LocationGroundTruthService


def audit_degenerate_location_gt(session: Session) -> int:
    """Print every stored GT box enclosing no area. Returns the number found."""
    findings = LocationGroundTruthService(session).find_degenerate_boxes()

    if not findings:
        print("Degenerate location ground truth: none found.")
        return 0

    print(f"Degenerate location ground truth: {len(findings)} box(es) found.")
    for finding in findings:
        print(
            f"  drawing {finding.drawing_id} ({finding.drawing_name}) "
            f"page {finding.page_number} box {finding.box_id} "
            f"[{finding.label}]: {finding.reason}"
        )
    print(
        "\nRe-import each Drawing above from a corrected source file — these boxes can "
        "never be matched, so they cap its recall below 1.0."
    )
    return len(findings)


def main() -> None:
    # Deliberately no ``init_db``, unlike ``core.cli``: creating the schema here would turn a
    # wrong ``CASEV_DATA_ROOT`` — a typo, an unmounted Volume — into a fresh empty store that
    # audits clean, and "none found" is exactly the answer that must never be fabricated. A
    # missing store raises instead.
    engine = make_engine()
    with Session(engine) as session:
        found = audit_degenerate_location_gt(session)
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
