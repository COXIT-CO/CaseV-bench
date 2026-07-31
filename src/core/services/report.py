"""Downloadable standalone HTML run report (ADR 0026, spec-run-report, ticket 02).

Assemble one location Run's scattered per-model Results into a single, self-contained HTML
file a non-expert can read offline. Pure local assembly — no model calls, no ``Report``
table, no stored file: the route streams the string this service returns.

The file is standalone: every image is a base64 ``data:`` URI (the cached prediction overlay
PNGs, and GT overlays rendered on demand via the ADR-0024 renderer), all CSS is inlined, and
there are no external references, so it opens by double-click over ``file://``. Zoom is a later
ticket (03), so ticket 02 emits static markup with no script.

Layout is page-major for a newcomer: an ObjectType colour legend, a summary block (headline
verdict + F1 ranking + coverage note), one section per page (GT column first where present,
then a column per model), and a per-label breakdown table.

Per-page and aggregate F1 are recomputed at generation time from the **salvage-inclusive**
seam ``ScoringService.predicted_boxes_by_page`` (ADR 0027) and the pure ``score_location``
matcher, so the report's numbers match the Leaderboard.

Because the file is frozen while live Scores recompute on read, the summary also stamps the
Operating point those numbers were computed under — the IoU threshold the scorer echoed back
and the installed ``location-scorer`` version (spec-scorer-library-implementation, "Stating
the operating point").
"""

import base64
import html
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.prompt import Prompt
from core.models.results import OBJECT_LABELS
from core.models.run import Prediction, PredictionStatus, Result, Run
from core.services.location_ground_truth import LocationGroundTruthService
from core.services.scoring import (
    SCORER_DISTRIBUTION,
    LocationBox,
    ScoringService,
    score_location,
    scorer_version,
)
from core.utils import LABEL_COLORS, color_for_label, ground_truth_overlay_png


@dataclass(frozen=True)
class ReportFile:
    """The assembled report: the download ``filename`` and the self-contained ``html``."""

    filename: str
    html: str


@dataclass
class _Cell:
    """One model's outcome on one page: the overlay image (``None`` → a failed placeholder),
    the per-label detection tally, whether the underlying Prediction was salvaged, and the
    per-page F1 (only on a GT page)."""

    overlay_uri: str | None
    tally: list[tuple[str, int]]
    salvaged: bool
    f1: float | None


@dataclass
class _ModelReport:
    """One model column across the whole Run: its aggregate rates (``None`` when the Drawing
    has no GT at all), the IoU threshold those rates came back from, its per-label F1, its
    per-page cells, whether any page salvaged, and whether it produced no detections at all
    (a failed/empty model, ranked last)."""

    model: str
    result_id: int
    scored: bool
    f1: float | None
    precision: float | None
    recall: float | None
    iou_threshold: float | None
    per_label_f1: dict[str, float]
    cells_by_page: dict[int, _Cell]
    salvaged: bool
    no_output: bool


def _esc(text: object) -> str:
    return html.escape(str(text))


def _slug(name: str) -> str:
    """A filesystem-legible slug of the drawing name for the download filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "drawing"


def _data_uri(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _tally(boxes: list[LocationBox]) -> list[tuple[str, int]]:
    """Per-label counts of a page's boxes, taxonomy order first then any stray labels, only
    the labels actually present."""
    counts = Counter(box.label for box in boxes)
    ordered = [(label, counts[label]) for label in OBJECT_LABELS if counts[label]]
    extras = sorted(l for l in counts if l not in OBJECT_LABELS)
    return ordered + [(label, counts[label]) for label in extras]


class ReportService:
    def __init__(self, session: Session):
        self.session = session

    def build(self, run: Run) -> ReportFile:
        """Assemble the standalone HTML for a terminal ``run`` (the route has already
        validated its status). Returns the download filename and the HTML string."""
        drawing = self.session.get(Drawing, run.drawing_id)
        prompt = self.session.get(Prompt, run.prompt_id)
        pages = list(drawing.pages)

        # Fetch the Drawing's GT rows once: the GT-overlay renderer needs the ORM rows, while
        # scoring needs them reduced to LocationBox — both derive from this single query.
        gt_rows_by_page = LocationGroundTruthService(self.session).boxes_by_page(
            run.drawing_id
        )
        gt_by_page = {
            page_id: [
                LocationBox(r.label, r.x_min, r.y_min, r.x_max, r.y_max) for r in rows
            ]
            for page_id, rows in gt_rows_by_page.items()
        }
        # GT pages are those with GT that actually belong to this Drawing's page set.
        page_ids = {page.id for page in pages}
        gt_page_ids = set(gt_by_page) & page_ids
        has_gt = bool(gt_page_ids)

        models = self._model_reports(run, pages, gt_by_page)
        ranked = self._rank(models)

        # One clock read for both the filename and the header date, so they can't straddle
        # midnight and disagree.
        now = datetime.now(timezone.utc)
        html_str = self._render(
            run=run,
            drawing=drawing,
            prompt=prompt,
            pages=pages,
            gt_by_page=gt_by_page,
            gt_rows_by_page=gt_rows_by_page,
            gt_page_ids=gt_page_ids,
            has_gt=has_gt,
            models=ranked,
            now=now,
        )
        filename = f"casev-report-run{run.id}-{_slug(drawing.name)}-{now:%Y%m%d}.html"
        return ReportFile(filename=filename, html=html_str)

    def _model_reports(
        self,
        run: Run,
        pages: list[Page],
        gt_by_page: dict[int, list[LocationBox]],
    ) -> list[_ModelReport]:
        scoring = ScoringService(self.session)
        reports: list[_ModelReport] = []
        for result in run.results:
            # The salvage-inclusive seam (ADR 0027) so report numbers match the Leaderboard.
            pred_by_page = scoring.predicted_boxes_by_page(result)
            aggregate = score_location(pred_by_page, gt_by_page)
            per_label_f1 = (
                {ls.label: ls.f1 for ls in aggregate.per_label} if aggregate else {}
            )
            preds_by_page = {p.page_id: p for p in result.predictions}

            cells: dict[int, _Cell] = {}
            any_salvaged = False
            box_total = 0
            for page in pages:
                pred = preds_by_page.get(page.id)
                boxes = pred_by_page.get(page.id, [])
                box_total += len(boxes)
                # Salvaged = an ``error`` Prediction whose surviving boxes are scored (ADR 0027
                # gate: "has usable detections", not merely a non-null ``parsed_json``). An
                # errored-but-empty recovery contributes nothing, so it is not badged salvaged.
                salvaged = bool(
                    pred and pred.status == PredictionStatus.error and boxes
                )
                any_salvaged = any_salvaged or salvaged
                f1 = None
                if page.id in gt_by_page:
                    single = score_location(
                        {page.id: boxes}, {page.id: gt_by_page[page.id]}
                    )
                    f1 = single.f1 if single else None
                cells[page.id] = _Cell(
                    overlay_uri=self._overlay_uri(pred),
                    tally=_tally(boxes),
                    salvaged=salvaged,
                    f1=f1,
                )
            reports.append(
                _ModelReport(
                    model=result.model,
                    result_id=result.id,
                    scored=aggregate is not None,
                    f1=aggregate.f1 if aggregate else None,
                    precision=aggregate.precision if aggregate else None,
                    recall=aggregate.recall if aggregate else None,
                    iou_threshold=aggregate.iou_threshold if aggregate else None,
                    per_label_f1=per_label_f1,
                    cells_by_page=cells,
                    salvaged=any_salvaged,
                    no_output=box_total == 0,
                )
            )
        return reports

    def _overlay_uri(self, pred: Prediction | None) -> str | None:
        """Inline a Prediction's cached overlay PNG as a ``data:`` URI, or ``None`` when there
        is no overlay (a failure with no boxes) — the cell then renders a placeholder. The
        report scores the model's original output, so the cached run-time overlay is used, not
        an on-demand edited redraw."""
        if pred is None or not pred.overlay_path:
            return None
        path = Path(pred.overlay_path)
        if not path.exists():
            return None
        return _data_uri(path.read_bytes())

    @staticmethod
    def _rank(models: list[_ModelReport]) -> list[_ModelReport]:
        """Best-first by aggregate F1; an unscored/failed model (F1 0 or ``None``) sorts last.
        Model name then breaks ties for a stable order."""
        return sorted(
            models,
            key=lambda m: (-(m.f1 if m.f1 is not None else -1.0), m.model),
        )

    # --- rendering -------------------------------------------------------------------

    def _render(
        self,
        *,
        run: Run,
        drawing: Drawing,
        prompt: Prompt,
        pages: list[Page],
        gt_by_page: dict[int, list[LocationBox]],
        gt_rows_by_page: dict[int, list[LocationGroundTruth]],
        gt_page_ids: set[int],
        has_gt: bool,
        models: list[_ModelReport],
        now: datetime,
    ) -> str:
        title = f"CaseV report — Run #{run.id} — {drawing.name}"
        parts = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_esc(title)}</title>",
            f"<style>{_STYLES}</style>",
            "</head>",
            "<body>",
            self._render_header(run, drawing, prompt, len(pages), now),
            _render_legend(),
            self._render_summary(models, gt_page_ids, len(pages), has_gt),
            self._render_pages(pages, gt_by_page, gt_rows_by_page, models),
            self._render_per_label(models, has_gt),
            _LIGHTBOX_MARKUP,
            f"<script>{_LIGHTBOX_JS}</script>",
            "</body>",
            "</html>",
        ]
        return "\n".join(parts)

    def _render_header(
        self, run: Run, drawing: Drawing, prompt: Prompt, page_count: int, now: datetime
    ) -> str:
        date = now.strftime("%Y-%m-%d")
        return (
            '<header class="report-header">'
            f"<h1>Model comparison — Run #{_esc(run.id)}</h1>"
            '<p class="meta">'
            f"Drawing <strong>{_esc(drawing.name)}</strong> · "
            f"Prompt {_esc(prompt.family)} v{_esc(prompt.version)} · "
            f"{_esc(page_count)} page(s) · Generated {_esc(date)}"
            "</p>"
            "</header>"
        )

    def _render_summary(
        self,
        models: list[_ModelReport],
        gt_page_ids: set[int],
        total_pages: int,
        has_gt: bool,
    ) -> str:
        n, m = len(gt_page_ids), total_pages
        if has_gt and models:
            best = models[0]
            verdict = (
                f"Best model: <strong>{_esc(best.model)}</strong> — "
                f"F1 {best.f1:.2f} across {n} page(s) with ground truth"
            )
        else:
            verdict = (
                "No ground truth for this drawing — visual comparison only, no scores"
            )
        coverage = f"Scored on {n} of {m} page(s)"
        operating_point = _render_operating_point(models)

        rows = []
        for i, model in enumerate(models):
            star = " ★" if has_gt and i == 0 else ""
            badges = ""
            if model.salvaged:
                badges += '<span class="badge salvaged">salvaged</span>'
            # A model that emitted no boxes at all — an outright failure, ranked last. Gated on
            # "produced nothing", not "F1 0", so a model that made detections which all missed
            # (pure false positives) is not mislabelled as having no detections.
            if model.no_output:
                badges += '<span class="badge failed">no detections</span>'
            if model.scored:
                cells = (
                    f"<td>{model.f1:.2f}</td>"
                    f"<td>{model.precision:.2f}</td>"
                    f"<td>{model.recall:.2f}</td>"
                )
            else:
                cells = "<td>—</td><td>—</td><td>—</td>"
            rows.append(
                f"<tr><td>{i + 1}</td>"
                f'<td class="model">{_esc(model.model)}{star}{badges}</td>'
                f"{cells}</tr>"
            )

        return (
            '<section class="summary">'
            f'<p class="verdict">{verdict}</p>'
            f'<p class="coverage">{_esc(coverage)}</p>'
            f"{operating_point}"
            '<table class="ranking"><thead><tr>'
            "<th>#</th><th>Model</th><th>F1</th><th>Precision</th><th>Recall</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "</section>"
        )

    def _render_pages(
        self,
        pages: list[Page],
        gt_by_page: dict[int, list[LocationBox]],
        gt_rows_by_page: dict[int, list[LocationGroundTruth]],
        models: list[_ModelReport],
    ) -> str:
        sections = []
        for page in pages:
            page_has_gt = page.id in gt_by_page
            columns = []
            if page_has_gt:
                gt_png = ground_truth_overlay_png(
                    Path(page.image_path), gt_rows_by_page[page.id]
                )
                columns.append(
                    _render_column(
                        title="Ground truth",
                        overlay_uri=_data_uri(gt_png),
                        tally=_tally(gt_by_page[page.id]),
                        f1=None,
                        salvaged=False,
                        is_gt=True,
                    )
                )
            for model in models:
                cell = model.cells_by_page[page.id]
                columns.append(
                    _render_column(
                        title=model.model,
                        overlay_uri=cell.overlay_uri,
                        tally=cell.tally,
                        f1=cell.f1,
                        salvaged=cell.salvaged,
                        is_gt=False,
                    )
                )
            flag = (
                ""
                if page_has_gt
                else '<span class="visual-only">visual-only — no ground truth</span>'
            )
            sections.append(
                '<section class="page">'
                f"<h2>Page {_esc(page.page_number)} {flag}</h2>"
                f'<div class="grid">{"".join(columns)}</div>'
                "</section>"
            )
        return '<div class="pages">' + "".join(sections) + "</div>"

    def _render_per_label(self, models: list[_ModelReport], has_gt: bool) -> str:
        if not has_gt:
            return ""
        head = "".join(f"<th>{_esc(m.model)}</th>" for m in models)
        rows = []
        for label in OBJECT_LABELS:
            cells = "".join(
                f"<td>{m.per_label_f1.get(label, 0.0):.2f}</td>" for m in models
            )
            swatch = (
                f'<span class="swatch" style="background:{color_for_label(label)}">'
                "</span>"
            )
            rows.append(f"<tr><td>{swatch}{_esc(label)}</td>{cells}</tr>")
        return (
            '<section class="per-label">'
            "<h2>Per-label F1</h2>"
            '<table class="breakdown"><thead><tr>'
            f"<th>Label</th>{head}</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></section>"
        )


def _render_operating_point(models: list[_ModelReport]) -> str:
    """The Operating point line, stamped beside the metrics it qualifies.

    The report file outlives the code that made it, so "F1 0.73" needs the threshold that
    decided what counted as a match and the scorer version that decided how. Live Scores
    recompute on read, so two reports exported either side of a threshold change would
    otherwise disagree about the same Run with nothing on either page saying why.

    The threshold is the one every scored model's ``LocationScore`` came back carrying, not a
    constant re-read here, so the stamp cannot drift from the numbers above it. Nothing scored
    (no Ground Truth) means no operating point exists to state — and no metrics for it to
    qualify — so the line is omitted rather than invented. The version clause drops out on its
    own when the installed distribution reports no version.
    """
    thresholds = {m.iou_threshold for m in models if m.iou_threshold is not None}
    if not thresholds:
        return ""
    # One Run scores every model through the same call, so this set is a singleton; min() just
    # refuses to guess if that ever stops being true.
    threshold = min(thresholds)
    version = scorer_version()
    scorer = f" · scored by {SCORER_DISTRIBUTION} v{_esc(version)}" if version else ""
    return (
        f'<p class="operating-point">Matches counted at '
        f"IoU ≥ {threshold:.2f}{scorer}</p>"
    )


def _render_legend() -> str:
    items = "".join(
        f'<li><span class="swatch" style="background:{color}"></span>{_esc(label)}</li>'
        for label, color in LABEL_COLORS.items()
    )
    return f'<section class="legend"><h2>Object types</h2><ul>{items}</ul></section>'


def _render_column(
    *,
    title: str,
    overlay_uri: str | None,
    tally: list[tuple[str, int]],
    f1: float | None,
    salvaged: bool,
    is_gt: bool,
) -> str:
    badge = '<span class="badge salvaged">salvaged</span>' if salvaged else ""
    if overlay_uri is None:
        image = '<div class="placeholder">failed — no output</div>'
    else:
        # ``zoomable`` is the ticket-03 lightbox hook; the inlined pixels render with or
        # without JS, so click-to-enlarge is pure enhancement.
        image = f'<img class="zoomable" src="{overlay_uri}" alt="{_esc(title)}">'
    tally_html = (
        "".join(
            f'<li><span class="swatch" style="background:{color_for_label(label)}">'
            f"</span>{_esc(label)} ×{count}</li>"
            for label, count in tally
        )
        or '<li class="none">no detections</li>'
    )
    f1_html = ""
    if not is_gt:
        f1_html = (
            f'<p class="f1">F1 {f1:.2f}</p>'
            if f1 is not None
            else '<p class="f1 muted">visual-only</p>'
        )
    cls = "column gt" if is_gt else "column"
    return (
        f'<div class="{cls}">'
        f"<h3>{_esc(title)}{badge}</h3>"
        f"{image}"
        f'<ul class="tally">{tally_html}</ul>'
        f"{f1_html}"
        "</div>"
    )


_STYLES = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
  Arial, sans-serif; color: #1a1a1a; margin: 0; padding: 24px; background: #f7f7f8;
  line-height: 1.45; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 24px 0 10px; }
h3 { font-size: 13px; margin: 0 0 6px; word-break: break-word; }
.report-header .meta { color: #555; font-size: 13px; margin: 0; }
section { margin-bottom: 8px; }
.legend ul, .tally { list-style: none; padding: 0; margin: 0; }
.legend ul { display: flex; flex-wrap: wrap; gap: 14px; }
.legend li, .tally li { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 2px;
  border: 1px solid rgba(0,0,0,0.2); flex: none; }
.summary { background: #fff; border: 1px solid #e3e3e6; border-radius: 8px;
  padding: 16px; }
.verdict { font-size: 16px; font-weight: 600; margin: 0 0 4px; }
.coverage { color: #555; font-size: 13px; margin: 0; }
.operating-point { color: #6b6b70; font-size: 12px; margin: 4px 0 0; }
.summary .ranking { margin-top: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #ececef; }
th { color: #555; font-weight: 600; }
.ranking td.model { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px; }
.badge { display: inline-block; font-size: 10px; font-weight: 600; padding: 1px 6px;
  border-radius: 999px; margin-left: 6px; vertical-align: middle; }
.badge.salvaged { background: #fef3c7; color: #92400e; }
.badge.failed { background: #fee2e2; color: #991b1b; }
.pages { }
.page { background: #fff; border: 1px solid #e3e3e6; border-radius: 8px; padding: 16px;
  margin-bottom: 16px; }
.visual-only { font-size: 11px; font-weight: 500; color: #92400e; background: #fef3c7;
  padding: 1px 8px; border-radius: 999px; vertical-align: middle; }
.grid { display: flex; gap: 14px; overflow-x: auto; padding-bottom: 4px; }
.column { flex: 0 0 300px; max-width: 300px; }
.column.gt { border-right: 2px solid #e3e3e6; padding-right: 14px; }
.column img { width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px;
  display: block; }
.placeholder { width: 100%; aspect-ratio: 4 / 3; display: flex; align-items: center;
  justify-content: center; color: #991b1b; background: #fef2f2; border: 1px dashed #fca5a5;
  border-radius: 4px; font-size: 13px; }
.tally { margin-top: 8px; display: flex; flex-direction: column; gap: 3px; }
.tally li.none { color: #888; }
.f1 { font-size: 13px; font-weight: 600; margin: 6px 0 0; }
.f1.muted { color: #888; font-weight: 500; }
.per-label .breakdown td:first-child { display: flex; align-items: center; gap: 6px; }
.zoomable { cursor: zoom-in; }
.lightbox[hidden] { display: none; }
.lightbox { position: fixed; inset: 0; z-index: 999; display: flex; align-items: center;
  justify-content: center; padding: 24px; background: rgba(0,0,0,0.82); cursor: zoom-out;
  overflow: hidden; }
.lightbox img { max-width: 96vw; max-height: 96vh; width: auto; height: auto;
  border-radius: 4px; box-shadow: 0 8px 40px rgba(0,0,0,0.5); cursor: zoom-in;
  transition: transform 0.15s ease; }
.lightbox img.zoomed { cursor: zoom-out; transform: scale(2.5); }
"""


# The lightbox is a single hidden overlay the script projects the clicked image into; it is
# inert (hidden, no image) until JS wires it up, so a JS-disabled report never shows it.
_LIGHTBOX_MARKUP = (
    '<div id="lightbox" class="lightbox" hidden>'
    '<img alt="Enlarged overlay">'
    "</div>"
)

# Ticket-03 lightbox: a single inline *classic* script — no ES modules, no fetch, no external
# src — so it runs when the report is opened straight from ``file://``. Click any ``.zoomable``
# overlay to enlarge it; then click the enlarged image to magnify further around the click
# point (move the pointer to pan, click again to zoom back out); Esc or a backdrop click
# closes it. Pure enhancement over the inlined images, which render regardless.
_LIGHTBOX_JS = """
(function () {
  var box = document.getElementById('lightbox');
  if (!box) return;
  var full = box.querySelector('img');
  var zoomed = false;
  function resetZoom() {
    zoomed = false;
    full.classList.remove('zoomed');
    full.style.transformOrigin = '';
  }
  function open(src, alt) {
    full.setAttribute('src', src);
    full.setAttribute('alt', alt || 'Enlarged overlay');
    resetZoom();
    box.hidden = false;
  }
  function close() {
    box.hidden = true;
    full.removeAttribute('src');
    resetZoom();
  }
  function originFrom(e) {
    var rect = full.getBoundingClientRect();
    var x = ((e.clientX - rect.left) / rect.width) * 100;
    var y = ((e.clientY - rect.top) / rect.height) * 100;
    full.style.transformOrigin = x + '% ' + y + '%';
  }
  document.addEventListener('click', function (e) {
    var img = e.target.closest ? e.target.closest('img.zoomable') : null;
    if (img) {
      open(img.getAttribute('src'), img.getAttribute('alt'));
    } else if (!box.hidden && e.target === full) {
      // Click the enlarged image to magnify around the click point; click again to reset.
      if (zoomed) {
        resetZoom();
      } else {
        zoomed = true;
        originFrom(e);
        full.classList.add('zoomed');
      }
    } else if (!box.hidden && e.target === box) {
      // Only the backdrop closes — clicking the image zooms, so it never dismisses.
      close();
    }
  });
  full.addEventListener('mousemove', function (e) {
    if (zoomed) originFrom(e);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !box.hidden) close();
  });
})();
"""
