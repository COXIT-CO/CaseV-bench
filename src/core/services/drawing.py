"""Drawing ingestion — turn an uploaded PDF into a Drawing with its Pages.

Each page is rendered and downsampled exactly once at ingest and cached on disk;
runs reuse the cached image (spec: Drawings & ingestion). Reuses the existing
``PDFProcessingService`` render and ``utils.downsample`` rather than duplicating
either.
"""

from pathlib import Path

from PIL import Image
from sqlmodel import Session, func, select

from core.config import settings
from core.models.counting_ground_truth import CountingGroundTruth
from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.run import Result, Run
from core.services.deletion import RunCascadeCounts, cascade_delete_runs, remove_tree
from core.services.pdf_processing import PDFProcessingService
from core.utils import downsample

# Production default under the single data root; tests inject a temp ``cache_root`` (ADR-0014).
DEFAULT_CACHE_ROOT = settings.drawings_root
DEFAULT_OVERLAY_ROOT = settings.overlays_root


class DrawingService:
    def __init__(
        self,
        session: Session,
        pdf_service: PDFProcessingService | None = None,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        overlay_root: Path = DEFAULT_OVERLAY_ROOT,
    ):
        self.session = session
        self.pdf_service = pdf_service or PDFProcessingService()
        self.cache_root = Path(cache_root)
        # Needed only by ``delete_drawing``, which cascades into the Drawing's Runs and their
        # per-Result overlay files (ADR-0016); ingestion never touches it.
        self.overlay_root = Path(overlay_root)

    def ingest(self, pdf_path: Path, name: str) -> Drawing:
        """Create a Drawing and one Page per PDF page, caching a downsampled image."""
        drawing = Drawing(name=name)
        self.session.add(drawing)
        self.session.commit()
        self.session.refresh(drawing)

        page_dir = self.cache_root / str(drawing.id)
        rendered = self.pdf_service.extract_images(pdf_path, output_dir=page_dir)

        for page_number, full_res in enumerate(rendered, start=1):
            # Record the full-resolution render's dimensions: imported COCO boxes
            # (ticket 10) are annotated against the real page raster, not our
            # model-facing downsample, so normalizing by these dims lands in 0-1.
            with Image.open(full_res) as image:
                width_px, height_px = image.size
            cached = full_res.with_stem(f"{full_res.stem}_downsampled")
            downsample(full_res, cached)
            self.session.add(
                Page(
                    drawing_id=drawing.id,
                    page_number=page_number,
                    image_path=str(cached),
                    width_px=width_px,
                    height_px=height_px,
                )
            )

        self.session.commit()
        self.session.refresh(drawing)
        return drawing

    def collateral_counts(self, drawing_id: int) -> RunCascadeCounts:
        """The Runs + Results a delete of this Drawing would cascade (ADR-0016), so the
        confirm dialog can state the blast radius before committing. Tallied from the Runs
        that used the Drawing — the same set ``delete_drawing`` cascades — so the preview and
        the delete's receipt agree. Keeping this in the service (not the router) holds the
        count logic on the domain side of the ``api → core`` boundary (ADR-0015)."""
        run_ids = self.session.exec(
            select(Run.id).where(Run.drawing_id == drawing_id)
        ).all()
        result_count = self.session.exec(
            select(func.count()).select_from(Result).where(Result.run_id.in_(run_ids))
        ).one()
        return RunCascadeCounts(runs=len(run_ids), results=result_count)

    def delete_drawing(self, drawing_id: int) -> RunCascadeCounts:
        """Permanently delete a Drawing and everything derived from it (ADR-0016): its
        Pages, both kinds of ground truth, its cached page images on disk, and — reusing the
        Run-deletion machinery — every Run and Result that used it (so they leave the
        Leaderboard too). Returns the collateral counts (Runs + Results removed) the confirm
        dialog showed. Raises ``ValueError`` when there is no such Drawing so the route 404s.

        The Runs are cascaded first: that clears every Prediction pointing at this Drawing's
        Pages (a Prediction hangs off a Result → Run, and a Run's Predictions are for its own
        Drawing's Pages), so the Pages then delete FK-clean. Ground truth is removed by
        ``drawing_id`` (counting) and by the Pages' ids (location).
        """
        drawing = self.session.get(Drawing, drawing_id)
        if drawing is None:
            raise ValueError(f"no drawing with id {drawing_id}")

        run_ids = self.session.exec(
            select(Run.id).where(Run.drawing_id == drawing_id)
        ).all()
        counts = cascade_delete_runs(self.session, list(run_ids), self.overlay_root)

        pages = self.session.exec(
            select(Page).where(Page.drawing_id == drawing_id)
        ).all()
        page_ids = [page.id for page in pages]
        location_gt = self.session.exec(
            select(LocationGroundTruth).where(LocationGroundTruth.page_id.in_(page_ids))
        ).all()
        counting_gt = self.session.exec(
            select(CountingGroundTruth).where(
                CountingGroundTruth.drawing_id == drawing_id
            )
        ).all()

        for row in (*location_gt, *counting_gt, *pages, drawing):
            self.session.delete(row)
        self.session.commit()

        remove_tree(self.cache_root / str(drawing_id))
        return counts
