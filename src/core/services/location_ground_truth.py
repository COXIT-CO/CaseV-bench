"""Native ``objects`` importer for LocationGroundTruth (spec: Location Ground Truth;
ADR 0022; ticket 03).

A benchmark author uploads a human-expert's native ``objects`` JSON for a Drawing (ADR 0003 —
no in-app box editor) and imports it here. The file is one document per Drawing::

    {"project_id": ..., "objects": [
        {"id": ..., "category": "elevation", "page": 1,
         "bbox": {"x": 1562, "y": 36, "width": 876, "height": 519}}]}

The importer:

- maps each object onto a Page by its explicit **1-based ``page``** field — no filename
  convention to reverse-engineer;
- converts the object's absolute ``[x, y, width, height]`` pixel box into our normalized
  ``[x_min, y_min, x_max, y_max]`` 0-1 format using the **Page's native point dimensions**
  (the source PDF's ``page.rect``, captured at ingest — ADR 0022), which is the frame the
  expert labeled against; the render-DPI pixel dims are *not* the basis;
- validates each box falls within its page: a corner spilling over ``[0, 1]`` within a small
  tolerance (a flush-to-edge annotation) is clamped and accepted; a gross overflow is reported
  ``out_of_frame`` and that box skipped (per-box, not a whole-file reject);
- rejects a box enclosing no area — zero width/height, or inverted coordinates — as
  ``degenerate_box``: nothing can ever match it, so storing it would silently cap the
  Drawing's recall below 1.0 forever (ADR 0031);
- checks each ``category`` against the fixed singular taxonomy — the label map is the identity
  (ADR 0023), so an off-taxonomy category is a typo and is reported ``unmapped_label``;
- **reports** an off-taxonomy category / an unknown page / a gross overflow / a degenerate box
  instead of silently dropping it, each distinct cause once, while still importing everything
  valid.

``project_id`` is ignored — the Drawing the import was launched from is authoritative.
Re-importing a Drawing replaces its existing location ground truth so an import is the whole
truth for that Drawing rather than an append.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlmodel import Session, select

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.results import OBJECT_LABELS
from core.services.counting_ground_truth import CountingGroundTruthService

# The four reasons an object can't be imported (reported, never silently dropped).
ProblemKind = Literal[
    "unmapped_label", "unknown_page", "out_of_frame", "degenerate_box"
]
UNMAPPED_LABEL: ProblemKind = "unmapped_label"
UNKNOWN_PAGE: ProblemKind = "unknown_page"
OUT_OF_FRAME: ProblemKind = "out_of_frame"
DEGENERATE_BOX: ProblemKind = "degenerate_box"

# The fixed singular taxonomy (ADR 0023). The label map is the identity, so an expert file that
# already speaks these names imports with no configuration; any other ``category`` is a typo.
_TAXONOMY: frozenset[str] = frozenset(OBJECT_LABELS)

# A box corner may spill past [0, 1] by this fraction (~0.5%) and still be accepted, clamped to
# the unit square — this absorbs a legitimate flush-to-edge annotation. Beyond it, the box is
# reported ``out_of_frame`` and skipped: a sign the file was authored against the wrong frame.
_FRAME_TOLERANCE = 0.005


def _degeneracy_reason(width: float, height: float) -> str | None:
    """The named reason a box with these extents encloses no area, or ``None`` when it is
    well-formed. Prose, meant to be read by whoever has to find the box in their source data.

    A zero-area or inverted box can never be matched by any prediction, so storing one would
    make it a permanent false negative that caps the Drawing's recall below 1.0 with nothing
    in the numbers explaining why — an answer-key defect, not a scoring outcome (ADR 0031).

    Takes extents rather than corners so it serves all three callers: a source box's native
    ``[width, height]``, where a negative extent is exactly ``x_min > x_max`` on that axis;
    the same box's extents after clamping to the page frame; and a stored row's normalized
    ``x_max - x_min`` in the audit.
    """
    reasons = []
    for extent, axis, dimension in ((width, "x", "width"), (height, "y", "height")):
        if extent == 0:
            reasons.append(f"zero {dimension}")
        elif extent < 0:
            reasons.append(f"inverted {axis} coordinates (negative {dimension})")
    return " and ".join(reasons) if reasons else None


@dataclass(frozen=True)
class ImportProblem:
    kind: ProblemKind
    detail: str


@dataclass
class LocationImportResult:
    created: int = 0
    problems: list[ImportProblem] = field(default_factory=list)


@dataclass(frozen=True)
class DegenerateBoxFinding:
    """One stored GT box that encloses no area, located well enough to fix at source.

    Rows written before the import guard existed are still in the store, so the audit reads
    what is there rather than replaying an import.
    """

    drawing_id: int
    drawing_name: str
    page_number: int
    box_id: int
    label: str
    reason: str


class LocationGroundTruthService:
    def __init__(self, session: Session):
        self.session = session

    def import_objects(
        self, drawing_id: int, document: Mapping, derive_counting: bool = False
    ) -> LocationImportResult:
        """Import a parsed native ``objects`` document as this Drawing's LocationGroundTruth.

        Replaces any existing ground truth for the Drawing. Returns the count created and a
        list of problems (off-taxonomy categories / unknown pages / gross overflows /
        degenerate boxes) that were reported, not imported. ``project_id`` in the document is
        ignored — ``drawing_id`` is authoritative.

        With ``derive_counting`` set (off by default — ADR 0025), the accepted boxes are
        tallied per label and written as this Drawing's counting GT through the existing
        ``CountingGroundTruthService`` — a convenience writer, never a silent coupling. Only
        the labels these boxes actually cover are written: a taxonomy label with no accepted
        box is left untouched, not asserted as zero, since an objects file need not localize
        every object type and ADR 0025 keeps "boxes" and "count" from silently equating
        (counting totals may legitimately include objects that were not localized). Left off
        (the default), the import touches only ``LocationGroundTruth`` and any existing
        counting total is preserved.
        """
        result = LocationImportResult()
        # Accepted boxes per label, so an opt-in caller can derive counting GT from them.
        tallies: Counter[str] = Counter()

        pages_by_number = {
            page.page_number: page
            for page in self.session.exec(
                select(Page).where(Page.drawing_id == drawing_id)
            ).all()
        }
        self._clear_existing(pages_by_number.values())

        # Report a problem at most once per distinct cause, not once per object.
        reported: set[tuple[str, str]] = set()

        def report(kind: ProblemKind, detail: str) -> None:
            key = (kind, detail)
            if key not in reported:
                reported.add(key)
                result.problems.append(ImportProblem(kind=kind, detail=detail))

        for obj in document.get("objects", []):
            category = obj.get("category")
            if category not in _TAXONOMY:
                report(UNMAPPED_LABEL, f"no taxonomy mapping for label {category!r}")
                continue

            page_number = obj.get("page")
            page = pages_by_number.get(page_number)
            if page is None:
                report(
                    UNKNOWN_PAGE,
                    f"object references page {page_number}, "
                    f"which drawing {drawing_id} does not have",
                )
                continue

            bbox = obj["bbox"]
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            # Before the frame check: an inverted box can also fall outside the unit square
            # once normalized, and ``out_of_frame`` would send the author hunting the wrong
            # defect.
            degeneracy = _degeneracy_reason(w, h)
            if degeneracy is not None:
                report(
                    DEGENERATE_BOX,
                    f"a box on page {page_number} has {degeneracy}, "
                    f"so it encloses no area, and was skipped",
                )
                continue

            corners = (
                x / page.native_width_pt,
                y / page.native_height_pt,
                (x + w) / page.native_width_pt,
                (y + h) / page.native_height_pt,
            )
            if any(c < -_FRAME_TOLERANCE or c > 1 + _FRAME_TOLERANCE for c in corners):
                report(
                    OUT_OF_FRAME,
                    f"a box on page {page_number} exceeds its native frame "
                    f"({page.native_width_pt:g}x{page.native_height_pt:g} pt) and was skipped",
                )
                continue

            x_min, y_min, x_max, y_max = (min(1.0, max(0.0, c)) for c in corners)
            # Clamping can collapse a well-formed box: one lying entirely outside an edge but
            # within the tolerance has both corners pulled onto that edge. What is about to be
            # stored is what has to be non-degenerate, so the check runs on the clamped extents
            # too, not only on the source ones.
            collapsed = _degeneracy_reason(x_max - x_min, y_max - y_min)
            if collapsed is not None:
                report(
                    DEGENERATE_BOX,
                    f"a box on page {page_number} lies outside the page edge and "
                    f"collapses to {collapsed} once clamped to the frame; it was skipped",
                )
                continue

            self.session.add(
                LocationGroundTruth(
                    page_id=page.id,
                    label=category,
                    x_min=x_min,
                    y_min=y_min,
                    x_max=x_max,
                    y_max=y_max,
                )
            )
            result.created += 1
            tallies[category] += 1

        self.session.commit()

        if derive_counting:
            # Upsert a total only for the labels these boxes cover; a label with no accepted
            # box is left as-is rather than zeroed, so we never fabricate a "zero of this
            # object" answer the boxes did not state (ADR 0025).
            CountingGroundTruthService(self.session).save(drawing_id, dict(tallies))

        return result

    def boxes_by_page(self, drawing_id: int) -> dict[int, list[LocationGroundTruth]]:
        """This Drawing's location GT boxes grouped by ``page_id``; empty when none has
        been imported, so callers can tell "unlabeled" from "labeled with no boxes on a
        page" (the empty dict is what makes a location Result read as unscored)."""
        rows = self.session.exec(
            select(LocationGroundTruth)
            .join(Page, LocationGroundTruth.page_id == Page.id)
            .where(Page.drawing_id == drawing_id)
        ).all()
        grouped: dict[int, list[LocationGroundTruth]] = {}
        for row in rows:
            grouped.setdefault(row.page_id, []).append(row)
        return grouped

    def find_degenerate_boxes(self) -> list[DegenerateBoxFinding]:
        """Every stored GT box across **all** Drawings that encloses no area.

        The import guard only stops new ones; this answers whether any already-imported
        Drawing holds one. An empty list is the meaningful "none found" result — after the
        port to ``location-scorer`` such a box raises at score time (ADR 0031), so a clean
        store is what makes the port's parity run trustworthy.
        """
        rows = self.session.exec(
            select(LocationGroundTruth, Page, Drawing)
            .join(Page, LocationGroundTruth.page_id == Page.id)
            .join(Drawing, Page.drawing_id == Drawing.id)
            .order_by(Drawing.id, Page.page_number, LocationGroundTruth.id)
        ).all()
        findings = []
        for box, page, drawing in rows:
            reason = _degeneracy_reason(box.x_max - box.x_min, box.y_max - box.y_min)
            if reason is not None:
                findings.append(
                    DegenerateBoxFinding(
                        drawing_id=drawing.id,
                        drawing_name=drawing.name,
                        page_number=page.page_number,
                        box_id=box.id,
                        label=box.label,
                        reason=reason,
                    )
                )
        return findings

    def _clear_existing(self, pages) -> None:
        page_ids = [page.id for page in pages]
        if not page_ids:
            return
        for row in self.session.exec(
            select(LocationGroundTruth).where(LocationGroundTruth.page_id.in_(page_ids))
        ).all():
            self.session.delete(row)
