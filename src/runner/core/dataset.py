import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pymupdf

LOCATION_GLOB = "*obj-location.json"
ALLOWED_LABELS = frozenset({"cabinet", "countertop", "elevation", "floor_plan", "callout"})
LABEL_ALIASES: dict[str, str] = {"floor plan": "floor_plan"}

RejectionReason = Literal["degenerate", "out_of_frame", "off_taxonomy"]


class DatasetError(Exception):
    pass


class DatasetNotFoundError(DatasetError):
    pass


class DatasetMalformedError(DatasetError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetMalformedError(message)


@dataclass(frozen=True, slots=True)
class Box:
    label: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(frozen=True, slots=True)
class RejectedBox:
    page: int
    label: str
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class PageGroundTruth:
    page: int
    boxes: tuple[Box, ...]


@dataclass(frozen=True, slots=True)
class DrawingGroundTruth:
    name: str
    pdf_path: Path
    pages: tuple[PageGroundTruth, ...]
    rejected: tuple[RejectedBox, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def object_count(self) -> int:
        return sum(len(page.boxes) for page in self.pages)

    def label_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(ALLOWED_LABELS, 0)
        for page in self.pages:
            for box in page.boxes:
                counts[box.label] += 1
        return counts

    def rejected_counts(self) -> dict[RejectionReason, int]:
        return Counter(rejected.reason for rejected in self.rejected)


class LocalDatasetSource:
    """Loads one or more drawings from disk, in the native `objects` ground-truth format"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @staticmethod
    def _rejection_reason(
        label: str,
        x_min: float,
        y_min: float,
        x_max: float,
        y_max: float,
        page_width: float,
        page_height: float,
    ) -> RejectionReason | None:
        if label not in ALLOWED_LABELS:
            return "off_taxonomy"
        if x_max <= x_min or y_max <= y_min:
            return "degenerate"
        if x_min < 0 or y_min < 0 or x_max > page_width or y_max > page_height:
            return "out_of_frame"
        return None

    @staticmethod
    def _is_drawing_dir(path: Path) -> bool:
        return (
            path.is_dir()
            and len(list(path.glob("*.pdf"))) == 1
            and len(list(path.glob(LOCATION_GLOB))) == 1
        )

    def _discover_drawing_dirs(self) -> list[Path]:
        if self._is_drawing_dir(self.root):
            return [self.root]
        drawing_dirs = [
            child for child in sorted(self.root.iterdir()) if self._is_drawing_dir(child)
        ]
        _require(
            len(drawing_dirs) > 0,
            f"no drawing directories (one *.pdf and one {LOCATION_GLOB} each) found under "
            f"{self.root}",
        )
        return drawing_dirs

    def load(self) -> dict[str, DrawingGroundTruth]:
        if not self.root.exists():
            raise DatasetNotFoundError(f"dataset directory not found: {self.root}")
        if not self.root.is_dir():
            raise DatasetMalformedError(f"dataset path is not a directory: {self.root}")

        drawings: dict[str, DrawingGroundTruth] = {}
        for drawing_dir in self._discover_drawing_dirs():
            drawing = self._load_drawing(drawing_dir)
            drawings[drawing.name] = drawing
        return drawings

    def _load_drawing(self, drawing_dir: Path) -> DrawingGroundTruth:
        pdf_path = next(drawing_dir.glob("*.pdf"))
        location_path = next(drawing_dir.glob(LOCATION_GLOB))

        try:
            raw_text = location_path.read_text()
        except OSError as exc:
            raise DatasetMalformedError(f"could not read {location_path}: {exc}") from exc

        try:
            document: Any = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise DatasetMalformedError(f"{location_path} is not valid JSON: {exc}") from exc

        _require(
            isinstance(document, dict) and isinstance(document.get("objects"), list),
            f'{location_path} must have a top-level object with an "objects" list',
        )
        name = document.get("project_id") or drawing_dir.name
        _require(isinstance(name, str), f'{location_path} has a non-string "project_id"')

        objects_by_page: dict[int, list[Any]] = {}
        for obj in document["objects"]:
            _require(
                isinstance(obj, dict)
                and isinstance(obj.get("category"), str)
                and isinstance(obj.get("page"), int)
                and isinstance(obj.get("bbox"), dict),
                f"{location_path} has a malformed object: {obj!r}",
            )
            objects_by_page.setdefault(obj["page"], []).append(obj)
        _require(len(objects_by_page) > 0, f"{location_path} defines no objects")

        try:
            pdf_document = pymupdf.open(pdf_path)
        except Exception as exc:  # pragma: no cover - PyMuPDF raises varied types
            raise DatasetMalformedError(f"PDF is not readable: {pdf_path}: {exc}") from exc

        pages: list[PageGroundTruth] = []
        rejected: list[RejectedBox] = []
        with pdf_document:
            for page_number in sorted(objects_by_page):
                _require(
                    1 <= page_number <= pdf_document.page_count,
                    f"{location_path} references page {page_number}, out of range for "
                    f"{pdf_path} ({pdf_document.page_count} pages)",
                )
                page, page_rejected = self._load_page(
                    pdf_document, page_number, objects_by_page[page_number], location_path
                )
                pages.append(page)
                rejected.extend(page_rejected)

        return DrawingGroundTruth(
            name=name,
            pdf_path=pdf_path,
            pages=tuple(pages),
            rejected=tuple(rejected),
        )

    def _load_page(
        self,
        pdf_document: pymupdf.Document,
        page_number: int,
        objects: list[Any],
        location_path: Path,
    ) -> tuple[PageGroundTruth, list[RejectedBox]]:
        page_rect = pdf_document[page_number - 1].rect
        page_width, page_height = page_rect.width, page_rect.height

        boxes: list[Box] = []
        rejected: list[RejectedBox] = []
        for obj in objects:
            bbox = obj["bbox"]
            _require(
                all(
                    isinstance(bbox.get(key), (int, float)) for key in ("x", "y", "width", "height")
                ),
                f"{location_path} object {obj.get('id')!r} on page {page_number} has a "
                f"malformed bbox: {bbox!r}",
            )
            label = LABEL_ALIASES.get(obj["category"], obj["category"])
            x_min = float(bbox["x"])
            y_min = float(bbox["y"])
            x_max = x_min + float(bbox["width"])
            y_max = y_min + float(bbox["height"])

            reason = self._rejection_reason(
                label, x_min, y_min, x_max, y_max, page_width, page_height
            )
            if reason is not None:
                rejected.append(RejectedBox(page=page_number, label=label, reason=reason))
                continue

            boxes.append(
                Box(
                    label=label,
                    x_min=x_min / page_width,
                    y_min=y_min / page_height,
                    x_max=x_max / page_width,
                    y_max=y_max / page_height,
                )
            )

        return (
            PageGroundTruth(page=page_number, boxes=tuple(boxes)),
            rejected,
        )


def resolve_dataset(dataset_dir: Path | None) -> tuple[Path, dict[str, DrawingGroundTruth]]:
    if dataset_dir is None:
        raise DatasetError(
            "no dataset directory given; pass --dataset-dir or set CASEV_DATASET_DIR"
        )
    return dataset_dir, LocalDatasetSource(dataset_dir).load()
