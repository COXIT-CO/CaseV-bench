"""COCO importer for LocationGroundTruth (spec: Ground truth; ADR 0003; ticket 10).

A developer exports labeled boxes for a Drawing's pages from an external annotation tool
(ADR 0003 — no in-app box editor) and imports the resulting COCO JSON here. The importer:

- maps each COCO ``image`` onto a Page by the 1-based page number embedded in its
  ``file_name`` — developers annotate the exact rendered page images, named
  ``page_NNNN.png`` at ingest;
- converts COCO's absolute ``[x, y, w, h]`` pixel box into our normalized
  ``[x_min, y_min, x_max, y_max]`` 0-1 format using the **Page's** stored pixel
  dimensions, not the COCO image's reported ones;
- maps external category names onto the fixed object taxonomy via ``label_map``
  (identity over the canonical labels by default);
- **reports** an unmapped label or a reference to a non-existent page instead of
  silently dropping it, while still importing everything that is valid.

Re-importing a Drawing replaces its existing location ground truth so an import is the
whole truth for that Drawing rather than an append.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlmodel import Session, select

from models.drawing import Page
from models.location_ground_truth import LocationGroundTruth
from models.results import OBJECT_LABELS

# The two reasons an annotation can't be imported (reported, never silently dropped).
ProblemKind = Literal["unmapped_label", "unknown_page"]
UNMAPPED_LABEL: ProblemKind = "unmapped_label"
UNKNOWN_PAGE: ProblemKind = "unknown_page"

# Identity map over the fixed taxonomy: a COCO export that already uses our canonical
# label names imports with no configuration. Other vocabularies pass an explicit map.
DEFAULT_LABEL_MAP: dict[str, str] = {label: label for label in OBJECT_LABELS}

# The 1-based page number in a rendered page image name (``page_0001.png`` at ingest).
_PAGE_NUMBER_RE = re.compile(r"page[_-]?0*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ImportProblem:
    kind: ProblemKind
    detail: str


@dataclass
class CocoImportResult:
    created: int = 0
    problems: list[ImportProblem] = field(default_factory=list)


def _page_number_from_filename(file_name: str) -> int | None:
    """The 1-based page number encoded in a COCO image file name, or ``None``.

    Resolves the ``page_NNNN`` render convention that ingest writes; a file name that
    doesn't carry one is treated as an unknown page rather than guessed at.
    """
    match = _PAGE_NUMBER_RE.search(file_name)
    return int(match.group(1)) if match else None


class LocationGroundTruthService:
    def __init__(self, session: Session):
        self.session = session

    def import_coco(
        self,
        drawing_id: int,
        coco: Mapping,
        label_map: Mapping[str, str] | None = None,
    ) -> CocoImportResult:
        """Import a parsed COCO JSON as this Drawing's LocationGroundTruth.

        Replaces any existing ground truth for the Drawing. Returns the count created and
        a list of problems (unmapped labels / unknown pages) that were reported, not
        imported. Rejects a ``label_map`` that targets a label outside the fixed taxonomy.
        """
        label_map = DEFAULT_LABEL_MAP if label_map is None else label_map
        off_taxonomy = set(label_map.values()) - set(OBJECT_LABELS)
        if off_taxonomy:
            raise ValueError(
                f"label_map targets labels outside taxonomy: {sorted(off_taxonomy)}"
            )
        result = CocoImportResult()

        pages_by_number = {
            page.page_number: page
            for page in self.session.exec(
                select(Page).where(Page.drawing_id == drawing_id)
            ).all()
        }
        self._clear_existing(pages_by_number.values())

        categories = {cat["id"]: cat["name"] for cat in coco.get("categories", [])}
        images = {img["id"]: img for img in coco.get("images", [])}

        # Report a problem at most once per distinct cause, not once per annotation.
        reported: set[tuple[str, str]] = set()

        def report(kind: ProblemKind, detail: str) -> None:
            key = (kind, detail)
            if key not in reported:
                reported.add(key)
                result.problems.append(ImportProblem(kind=kind, detail=detail))

        for ann in coco.get("annotations", []):
            category_name = categories.get(ann["category_id"], str(ann["category_id"]))
            label = label_map.get(category_name)
            if label is None:
                report(
                    UNMAPPED_LABEL, f"no taxonomy mapping for label {category_name!r}"
                )
                continue

            image = images.get(ann["image_id"])
            page_number = (
                _page_number_from_filename(image.get("file_name", ""))
                if image
                else None
            )
            page = pages_by_number.get(page_number) if page_number is not None else None
            if page is None:
                detail = (
                    f"annotation references page {page_number}, "
                    f"which drawing {drawing_id} does not have"
                    if page_number is not None
                    else f"annotation references image_id {ann['image_id']} "
                    "with no resolvable page"
                )
                report(UNKNOWN_PAGE, detail)
                continue

            x, y, w, h = ann["bbox"]
            self.session.add(
                LocationGroundTruth(
                    page_id=page.id,
                    label=label,
                    x_min=x / page.width_px,
                    y_min=y / page.height_px,
                    x_max=(x + w) / page.width_px,
                    y_max=(y + h) / page.height_px,
                )
            )
            result.created += 1

        self.session.commit()
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

    def _clear_existing(self, pages) -> None:
        page_ids = [page.id for page in pages]
        if not page_ids:
            return
        for row in self.session.exec(
            select(LocationGroundTruth).where(LocationGroundTruth.page_id.in_(page_ids))
        ).all():
            self.session.delete(row)
