import os
import re
import json
import time
import base64
import asyncio
import uuid
import shutil
from datetime import datetime, timezone
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional
from dotenv import load_dotenv
from pdf2image import convert_from_bytes, pdfinfo_from_bytes
from openai import AsyncOpenAI
from json_repair import repair_json  # Library for repairing malformed JSON
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel
from location_scorer import score as score_locations

load_dotenv()

# pdf2image renders pages through PIL, which caps decoded images at ~89.5M
# pixels as a decompression-bomb guard against untrusted external images.
# Here the images are generated internally from PDFs the user uploaded
# themselves (not fetched from the web), so the risk that guard protects
# against doesn't apply — but a large ARCH-D/E sheet at high DPI can easily
# exceed it. Raise the cap instead of leaving it silently warn/error mid-batch.
Image.MAX_IMAGE_PIXELS = 300_000_000  # ~300 MP ceiling, still bounded

app = FastAPI()
app.mount("/static", StaticFiles(directory="frontend"), name="static")

HISTORY_DIR = os.getenv("HISTORY_DIR", "history")
HISTORY_MAX_RUNS = int(os.getenv("HISTORY_MAX_RUNS", "50"))
os.makedirs(HISTORY_DIR, exist_ok=True)
app.mount("/history-files", StaticFiles(directory=HISTORY_DIR), name="history-files")

# ===============================
# History image resizing (perf)
# ===============================
# The History tab used to point both the small run-list thumbnail (rendered
# at 46x46px in the UI) and the full page-detail canvas (CSS-capped to the
# modal's width) directly at the ORIGINAL full-DPI PNG saved for that run —
# which can be many megabytes on a large architectural sheet. That meant
# opening the History tab downloaded and decoded dozens of full-resolution
# images just to show postage-stamp thumbnails, which is what caused the
# lag. This never touches meta.json, the run folder layout, or the original
# PNGs — it only adds small cached JPEG copies alongside them, generated on
# first request, so nothing about how history is stored or what's already
# saved changes or is at risk.
HISTORY_IMAGE_SIZES = {
    "thumb": (160, 60),     # small run-list thumbnail (shown at 46x46, 2-3x for retina)
    "display": (1800, 82),  # page-detail canvas (CSS-capped to the modal width)
}
HISTORY_CACHE_SUBDIR = "_cache"

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

VALID_LABELS = {"cabinet", "countertop", "elevation", "floor plan", "callout"}
LABEL_TO_SUMMARY_KEY = {
    "cabinet": "cabinets",
    "countertop": "countertops",
    "elevation": "elevations",
    # Two-Stage/Grid are untouched and still emit "elevation_callout" —
    # this mapping stays for them. One-Stage's taxonomy (2026-08-17) has
    # no "elevation_callout" any more, only "callout" — see [[casev-object-taxonomy]].
    "elevation_callout": "elevation_callouts",
    "callout": "callouts",
    # "floor plan" has a SPACE — matches ground truth's category spelling
    # exactly (drafts/overlay-demo/input/prj*-obj-location.json), so
    # predictions can match it string-for-string during scoring.
    "floor plan": "floor_plans",
}

# Every model has its own native box-order bias (Gemini's vision head is
# trained on Google's grounding format [y_min, x_min, y_max, x_max]; most
# others default to [x_min, y_min, x_max, y_max]; a few invent their own).
# Rather than trust any one model to self-assemble "box" in the right order,
# the prompt asks for four independently-named fields instead
# (left/top/right/bottom, each unambiguous regardless of a model's internal
# training convention) — see resolve_box() below, which is what actually
# reconstructs "box" from them in code. This makes the box-order guarantee
# hold for ANY model, not just ones that happen to assemble arrays the way
# Gemini does.
#
# Kept for token-budget/reasoning tuning: "mandatory" models always run an
# internal reasoning pass they can't turn off; "optional" models CAN reason
# but don't by default. Both are given the same "low effort" nudge and the
# same larger token budget below — full sheet scans (elevations especially)
# are the kind of task that benefits from at least a little reasoning, and
# a model that's allowed none tends to under-scan exactly those pages.
MANDATORY_REASONING_SUBSTR = ("gemini-3", "gemini-2.5")
OPTIONAL_REASONING_SUBSTR = ("claude-sonnet", "claude-opus", "claude-haiku", "gpt-5")

def model_reasoning_kind(model_name: str) -> str:
    name = model_name.lower()
    if any(s in name for s in MANDATORY_REASONING_SUBSTR):
        return "mandatory"
    if any(s in name for s in OPTIONAL_REASONING_SUBSTR):
        return "optional"
    return "none"


def resolve_box(obj: dict):
    """Returns the best available [x_min, y_min, x_max, y_max] box for a raw
    object dict from a model response. Prefers the independently-named
    left/top/right/bottom fields (when present and internally consistent —
    right > left, bottom > top) over the model's own self-assembled "box"
    array, since the whole point of asking for named fields is to not have
    to trust that assembly step. Falls back to "box" directly for models/
    prompts that only ever produce that field."""
    left, top, right, bottom = (obj.get("left"), obj.get("top"), obj.get("right"), obj.get("bottom"))
    if (all(isinstance(v, (int, float)) for v in (left, top, right, bottom))
            and right > left and bottom > top):
        return [left, top, right, bottom]

    box = obj.get("box")
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        return box
    return None


def extract_ground_truth_items(gt_raw):
    """Accepts either a bare list of ground-truth objects, or the project's
    own export shape — {"project_id": ..., "objects": [...]} — and returns
    the flat list of objects either way."""
    if isinstance(gt_raw, dict):
        items = gt_raw.get("objects", [])
    elif isinstance(gt_raw, list):
        items = gt_raw
    else:
        items = []
    return items if isinstance(items, list) else []


def extract_ground_truth_box(item: dict):
    """Returns [x_min, y_min, x_max, y_max] from a ground-truth object's own
    "box"/"bbox" field, in whichever of the two shapes it was given as: a
    4-number [x_min,y_min,x_max,y_max] array, or the project's own
    {"x","y","width","height"} (top-left + size) shape. Returns None if
    neither is present or well-formed."""
    box = item.get("box")
    if box is None:
        box = item.get("bbox")
    if isinstance(box, dict):
        x, y, w, h = box.get("x"), box.get("y"), box.get("width"), box.get("height")
        if all(isinstance(v, (int, float)) for v in (x, y, w, h)):
            return [x, y, x + w, y + h]
        return None
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        return box
    return None


def to_unit_box(box, width, height):
    """0-1 analog of normalize_box() below: auto-detects whether a box is
    already a 0-1 fraction or raw pixel coordinates, and returns it as a 0-1
    fraction either way. Used for ground-truth boxes a person pastes in for
    the grid-detection flow, which may be given in either form (see
    /api/grid/detect)."""
    if max(box) <= 1:
        return [max(0.0, min(1.0, v)) for v in box]
    if not width or not height:
        return [max(0.0, min(1.0, v)) for v in box]
    x0, y0, x1, y1 = box
    return [
        max(0.0, min(1.0, x0 / width)),
        max(0.0, min(1.0, y0 / height)),
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
    ]


# ===========================================================================
# Scoring against human ground truth (shared by all three detection flows)
# ===========================================================================
# location-scorer works in 0-1 xyxy fractions and buckets objects by
# (page, object_type). Two things have to be reconciled before any flow here
# can hand it data:
#
#   1. SCALE. The grid flow already produces 0-1 boxes, but /api/generate and
#      the two-stage flow both work in 0-1000, so their predictions are
#      divided down here rather than each flow growing its own conversion.
#
#   2. PAGE IDENTITY. The grid flow scores exactly ONE PDF, so a bare page
#      number identifies a page uniquely. /api/generate and the two-stage
#      flow accept SEVERAL files at once, where "page 1" exists once per
#      file — scoring those with a bare page number would let a prediction on
#      file A's page 1 match a ground-truth box on file B's page 1. So when
#      more than one file is in play, the two are folded into one synthetic
#      integer key (see score_page_key) that stays unique per (file, page).
#      An integer is used rather than a "0:1" string purely so the value
#      remains the same type location-scorer already receives today.

PAGE_KEY_STRIDE = 100000


def score_page_key(file_index, page_num: int, multi_file: bool) -> int:
    """Page identifier handed to location-scorer. With a single file this is
    just the page number (so scores and any returned objects stay readable);
    with several, (file_index, page_num) is folded into one unique int."""
    if not multi_file:
        return page_num
    return (file_index or 0) * PAGE_KEY_STRIDE + page_num


def normalize_ground_truth(gt_items, page_dims_points: dict, multi_file: bool,
                            file_index_by_name: Optional[dict] = None) -> list:
    """Converts human-supplied ground-truth objects into the 0-1, one-key-per
    -page shape location-scorer expects.

    page_dims_points maps (file_index, page_num) -> that page's size in PDF
    POINTS (1/72in). Points — not the render's pixels — are what the
    project's own exports (drafts/expected/*/prj*-obj-location.json) measure
    bboxes in, so normalizing against the pixel size of whatever DPI a run
    happened to use would shrink every box by (dpi/72)x and score ~0 true
    positives. See the same reasoning spelled out in /api/grid/detect.

    Each item may name its file via "file_index" or "file"/"file_name"; with
    a single uploaded file that's unnecessary and everything defaults to
    file 0, which keeps the common single-PDF case free of boilerplate."""
    file_index_by_name = file_index_by_name or {}
    out = []
    for g in gt_items:
        if not isinstance(g, dict):
            continue
        obj_type = g.get("category") or g.get("object_type") or g.get("label")
        page_num = g.get("page")
        box = extract_ground_truth_box(g)
        if not obj_type or not isinstance(page_num, int) or box is None:
            continue

        f_idx = g.get("file_index")
        if not isinstance(f_idx, int):
            name = g.get("file") or g.get("file_name")
            f_idx = file_index_by_name.get(name, 0)

        dims = page_dims_points.get((f_idx, page_num))
        if dims is None:
            # Ground truth points at a page this run doesn't actually have.
            # Skipped rather than normalized against unknown dimensions,
            # which would produce a degenerate box that location-scorer
            # rejects outright and would crash scoring for every model.
            continue

        pts_w, pts_h = dims
        unit_box = to_unit_box(box, pts_w, pts_h)
        # A zero-area box makes location-scorer raise, so one malformed row
        # (a typo, a genuinely zero-size annotation) would otherwise take
        # down scoring for the whole run.
        if unit_box[2] <= unit_box[0] or unit_box[3] <= unit_box[1]:
            continue

        out.append({
            "object_type": obj_type,
            "page": score_page_key(f_idx, page_num, multi_file),
            "bbox": unit_box,
        })
    return out


def predictions_from_objects(objects, multi_file: bool, scale: float = 1000.0) -> list:
    """Converts this codebase's own detected-object shape ({label, box,
    file_index, page_num}) into location-scorer's prediction shape, dividing
    the box down from `scale` (0-1000 for every flow except grid, which
    already works in 0-1 and passes scale=1)."""
    preds = []
    for obj in objects:
        box = obj.get("box")
        label = obj.get("label")
        page_num = obj.get("page_num")
        if not label or not isinstance(page_num, int):
            continue
        if not (isinstance(box, list) and len(box) == 4
                and all(isinstance(v, (int, float)) for v in box)):
            continue
        unit = [max(0.0, min(1.0, v / scale)) for v in box]
        # location-scorer rejects zero-area boxes; a model occasionally
        # returns one and it should not abort the whole run's scoring.
        if unit[2] <= unit[0] or unit[3] <= unit[1]:
            continue
        preds.append({
            "object_type": label,
            "page": score_page_key(obj.get("file_index"), page_num, multi_file),
            "bbox": unit,
        })
    return preds


def safe_score(predictions, ground_truth, iou_threshold: float):
    """Runs location-scorer, returning None when there's no ground truth to
    score against and an {"error": ...} record if scoring itself fails —
    a scoring problem should never turn an otherwise-successful detection
    run into a failed request."""
    if not ground_truth:
        return None
    try:
        return score_locations(predictions, ground_truth,
                               iou_threshold=iou_threshold, include_objects=True)
    except Exception as e:
        print(f"[score] scoring failed: {e}")
        return {"error": str(e)}


# ===========================================================================
# Grid-cell detection (labeled reference grid instead of raw coordinates)
# ===========================================================================
# An alternative single-pass detection strategy: rather than asking a model
# to compute a numeric box directly (which every other flow in this file
# does), a labeled grid is drawn on top of the full sheet page and the model
# is asked only to name which cell(s) each object occupies — reading off a
# label is a much easier task for a model than estimating a fraction, and
# spreadsheet-style "A1" references are something every model has seen an
# enormous amount of in training. The box is then derived in CODE from
# whichever cell(s) were named, never trusted from the model's own numeric
# estimate. See drafts/prompts/System Grid for the full detection prompt.
#
# Boxes produced by this flow are 0-1 xyxy (top-left origin) — the shared
# contract location-scorer expects — NOT the 0-1000 scale every other flow
# in this file uses. See save_grid_history()'s "coord_scale" field for how
# the frontend tells the two scales apart when drawing history overlays.

_ALPHA_LABEL_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
_NUMERIC_LABEL_RE = re.compile(r"^[Rr](\d+)[Cc](\d+)$")
LABEL_SCHEMES = ("alpha", "numeric", "banded")  # "banded" uses alpha labels + zebra row tint

# Named positions within a cell, each a (x, y) fraction of that cell's own
# width/height — the vocabulary a model picks from when box_precision is
# "cell_anchor" to say where WITHIN a named cell its box actually starts/
# ends, instead of always snapping to the cell's outer edges. Kept as a
# small fixed set of labels (not a free 0-1 float) so the model is still
# picking a category, the same robustness the grid approach is built on,
# just a finer-grained one.
ANCHOR_FRACTIONS = {
    "topleft": (0.0, 0.0), "top": (0.5, 0.0), "topright": (1.0, 0.0),
    "left": (0.0, 0.5), "center": (0.5, 0.5), "right": (1.0, 0.5),
    "bottomleft": (0.0, 1.0), "bottom": (0.5, 1.0), "bottomright": (1.0, 1.0),
}
BOX_PRECISIONS = ("cell", "cell_anchor", "cell_fraction")


def resolve_point_fraction(value):
    """Resolves a model-reported "where in this cell" value into an (fx, fy)
    0-1 pair, or None if it doesn't resolve to anything usable. Accepts
    EITHER a named anchor (box_precision="cell_anchor", the robust/coarse
    option — model picks a category, same as picking a grid cell) OR a raw
    [fx, fy] two-number array (box_precision="cell_fraction", the flexible/
    fine option — model estimates a continuous point, same estimation task
    as a plain bounding box but scoped to ONE cell instead of the whole
    sheet, so the error stays small even if the estimate is a little off).
    Accepting either shape regardless of which mode was requested costs
    nothing and is harmless if a model gives the "wrong" shape for the mode
    it was asked for."""
    if isinstance(value, str):
        return ANCHOR_FRACTIONS.get(value)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        fx, fy = value
        if isinstance(fx, (int, float)) and isinstance(fy, (int, float)) and 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0:
            return (float(fx), float(fy))
    return None


def col_letters(col_idx: int) -> str:
    """0-indexed column -> spreadsheet-style letters: 0->'A', 25->'Z', 26->'AA', ..."""
    n = col_idx + 1
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def letters_to_col(letters: str) -> int:
    """Inverse of col_letters() -> 0-indexed column."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def cell_label(row_idx: int, col_idx: int, scheme: str = "alpha") -> str:
    """0-indexed (row, col) -> a cell label in the given scheme. "alpha" and
    "banded" both use spreadsheet-style labels, e.g. (0, 0) -> 'A1' — banded
    only changes how the grid is DRAWN (zebra row tint), not how cells are
    named. "numeric" avoids any letters at all, e.g. (0, 0) -> 'R1C1'."""
    if scheme == "numeric":
        return f"R{row_idx + 1}C{col_idx + 1}"
    return f"{col_letters(col_idx)}{row_idx + 1}"


def parse_cell_label(label, rows: int, cols: int, scheme: str = "alpha"):
    """Parses a cell label in the given scheme ('C4' for alpha/banded,
    'R4C3' for numeric) into a 0-indexed (row, col) pair, or None if
    malformed or outside the grid this request actually used (guards
    against a model inventing a cell beyond what was actually drawn)."""
    if not isinstance(label, str):
        return None
    s = label.strip()
    if scheme == "numeric":
        m = _NUMERIC_LABEL_RE.match(s)
        if not m:
            return None
        row_idx = int(m.group(1)) - 1
        col_idx = int(m.group(2)) - 1
    else:
        m = _ALPHA_LABEL_RE.match(s)
        if not m:
            return None
        col_idx = letters_to_col(m.group(1).upper())
        row_idx = int(m.group(2)) - 1
    if not (0 <= row_idx < rows and 0 <= col_idx < cols):
        return None
    return row_idx, col_idx


def cells_to_unit_box(cell_labels, rows: int, cols: int, scheme: str = "alpha",
                       start_anchor=None, end_anchor=None):
    """Converts a list of grid cell labels into one [x_min, y_min, x_max,
    y_max] box in 0-1 fractional space. One physical object spanning
    several cells is reported as a single detection with all its cells
    listed (see the System Grid prompt), not one detection per cell, so
    this always produces exactly one box per object regardless of how many
    cells it spans. Returns None if no cell label in the list was valid.

    By default (start_anchor/end_anchor both None) the box is the smallest
    rectangle enclosing every named cell's full outer edges — the original,
    coarsest behavior. When box_precision is "cell_anchor" or
    "cell_fraction", the caller passes the model's own reported position —
    a named anchor OR a raw [fx, fy] pair, resolve_point_fraction() accepts
    either regardless of mode — for where its box actually starts within
    the top-left-most named cell and ends within the bottom-right-most
    named cell, giving sub-cell precision without needing a finer grid. An
    invalid/missing value falls back to that corner's outer cell edge (0,0
    for start, 1,1 for end) — always a safe, valid box, never a hard
    failure."""
    parsed = [parse_cell_label(c, rows, cols, scheme) for c in (cell_labels or [])]
    parsed = [p for p in parsed if p is not None]
    if not parsed:
        return None
    row_indices = [p[0] for p in parsed]
    col_indices = [p[1] for p in parsed]
    row_min, row_max = min(row_indices), max(row_indices)
    col_min, col_max = min(col_indices), max(col_indices)

    start_fx, start_fy = resolve_point_fraction(start_anchor) or (0.0, 0.0)
    end_fx, end_fy = resolve_point_fraction(end_anchor) or (1.0, 1.0)
    x0, y0 = (col_min + start_fx) / cols, (row_min + start_fy) / rows
    x1, y1 = (col_max + end_fx) / cols, (row_max + end_fy) / rows
    if x1 <= x0 or y1 <= y0:
        # A nonsensical point pair (e.g. start right of end, in the same
        # cell) would otherwise collapse or invert the box — fall back to
        # the full cell-union edges rather than emit a degenerate box.
        return [col_min / cols, row_min / rows, (col_max + 1) / cols, (row_max + 1) / rows]
    return [x0, y0, x1, y1]


def resize_to_fit(img, max_dim: int):
    """Downscales img (preserving aspect ratio) so its longer side is at
    most max_dim, or returns it unchanged if already smaller. This MUST run
    before draw_grid_overlay(), not after: a vision model downscales
    whatever image it's actually given internally before its own encoder
    ever sees it, and grid line/label sizing in draw_grid_overlay() is
    computed relative to the image it's called on — draw the grid on a
    native-DPI render (which can be 10000px+ on a large architectural
    sheet) and the labels end up a tiny fraction of what the model's own
    internal downscaling leaves behind, effectively illegible to it, even
    though they'd look fine to a human opening the full-res file directly."""
    width, height = img.size
    longest = max(width, height)
    if longest <= max_dim:
        return img
    scale = max_dim / longest
    return img.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)


def hex_to_rgb(value: str, default=(220, 0, 0)):
    """Parses a "#rrggbb" (or "rrggbb") string into an (r, g, b) tuple,
    falling back to default on anything malformed rather than raising —
    this is user-supplied styling, not something worth a 400 over."""
    s = (value or "").strip().lstrip("#")
    if len(s) != 6:
        return default
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return default


def draw_grid_overlay(img, rows: int, cols: int, line_color=(220, 0, 0), line_opacity: float = 0.59,
                       line_width: int = 1, label_scheme: str = "alpha"):
    """Returns (image, geom) — a NEW image with a labeled reference grid
    drawn on top of img, and the geometry (margins + content/canvas size)
    needed to map a cell-derived box back onto THIS specific image later.

    Column letters are printed ONCE each, in a header strip added above the
    image; row numbers ONCE each, in a strip added to its left — never
    inside a cell itself. An earlier version put each cell's own label
    inside it (e.g. "C4" in its top-left corner), which works fine at a
    coarse grid but falls apart at a fine one: a label's legible size is
    tied to how big ITS OWN cell is, so packing many rows/cols into the
    same image (needed for real localization precision) shrinks every label
    right along with its cell until they're all illegible and/or overlap
    the drawing. Margin labels don't have that problem — legibility only
    depends on how much margin LENGTH is available for however many labels
    need to fit along it, independent of cell size, and they can never sit
    on top of (or be crowded out by) anything actually drawn on the sheet.
    Grid lines themselves are still drawn across the original image, thin
    and translucent, so a model can trace a line from a header label to the
    cell it bounds.

    Because a header strip is added, the returned CANVAS is bigger than the
    sheet content it wraps — cells_to_unit_box() computes fractions of the
    CONTENT area alone (the only thing ground truth / ordinary page
    fractions can mean), so those fractions are NOT directly usable as
    fractions of the returned canvas. content_box_to_canvas_frac() below
    does that remaining conversion, using the geom this function returns."""
    base = img.convert("RGB")
    width, height = base.size
    cell_w = width / cols
    cell_h = height / rows

    # Margin sizing: legible against a LOT of crammed-in labels, but capped
    # so it doesn't dominate a coarse, few-cell grid. "numeric" row headers
    # read "R123" instead of a bare number, so they need a bit more width.
    font_size = max(11, min(24, round(min(cell_w, cell_h) * 0.6)))
    margin_top = font_size + 12
    margin_left = round(font_size * (2.6 if label_scheme == "numeric" else 2)) + 12

    canvas = Image.new("RGB", (width + margin_left, height + margin_top), (255, 255, 255))
    canvas.paste(base, (margin_left, margin_top))

    # Grid lines (and, for "banded", a zebra tint under every other row —
    # purely a visual counting aid, no meaning of its own) drawn on a
    # translucent overlay, composited only over the image region — legible
    # over dense line-art without hiding it, same idea as the old design,
    # just no longer sharing space with labels.
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    line_draw = ImageDraw.Draw(overlay)
    if label_scheme == "banded":
        band_color = (*line_color, 35)
        for r in range(rows):
            if r % 2 == 1:
                y0 = margin_top + round(r * cell_h)
                y1 = margin_top + round((r + 1) * cell_h)
                line_draw.rectangle([margin_left, y0, margin_left + width, y1], fill=band_color)
    rgba_line_color = (*line_color, round(max(0.0, min(1.0, line_opacity)) * 255))
    for c in range(cols + 1):
        x = margin_left + round(c * cell_w)
        line_draw.line([(x, margin_top), (x, margin_top + height)], fill=rgba_line_color, width=line_width)
    for r in range(rows + 1):
        y = margin_top + round(r * cell_h)
        line_draw.line([(margin_left, y), (margin_left + width, y)], fill=rgba_line_color, width=line_width)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    try:
        font = ImageFont.load_default(size=font_size)
    except TypeError:
        # Older Pillow without the size= kwarg on load_default().
        font = ImageFont.load_default()

    draw = ImageDraw.Draw(canvas)
    label_color = tuple(round(c * 0.82) for c in line_color)  # a bit darker than the lines, always fully opaque

    for c in range(cols):
        label = f"C{c + 1}" if label_scheme == "numeric" else col_letters(c)
        cx = margin_left + (c + 0.5) * cell_w
        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.text((cx - tw / 2 - tb[0], (margin_top - th) / 2 - tb[1]), label, fill=label_color, font=font)

    for r in range(rows):
        label = f"R{r + 1}" if label_scheme == "numeric" else str(r + 1)
        cy = margin_top + (r + 0.5) * cell_h
        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.text(((margin_left - tw) / 2 - tb[0], cy - th / 2 - tb[1]), label, fill=label_color, font=font)

    geom = {
        "margin_left": margin_left, "margin_top": margin_top,
        "content_width": width, "content_height": height,
        "canvas_width": canvas.width, "canvas_height": canvas.height,
    }
    return canvas, geom


def content_box_to_canvas_frac(box, geom):
    """Maps a [x0,y0,x1,y1] box in 0-1 fractions of the CONTENT area (what
    cells_to_unit_box produces, and what scoring/ground truth both use) onto
    0-1 fractions of the full CANVAS draw_grid_overlay() actually returned
    (content + header margin). Only for DISPLAY: the saved/returned image
    includes the margin, so a box meant to be drawn on top of it needs to
    account for that offset, or it lands shifted and shrunk relative to the
    grid lines it's supposed to line up with — never used for scoring,
    which must stay in content-only fractions to match ground truth."""
    ml, mt = geom["margin_left"], geom["margin_top"]
    w, h = geom["content_width"], geom["content_height"]
    cw, ch = geom["canvas_width"], geom["canvas_height"]
    x0, y0, x1, y1 = box
    return [
        (ml + x0 * w) / cw,
        (mt + y0 * h) / ch,
        (ml + x1 * w) / cw,
        (mt + y1 * h) / ch,
    ]


# ===========================================================================
# Two-stage detection (elevation-first, then crop-and-detail)
# ===========================================================================
# A separate, single-model workflow: (1) detect "elevation" frames AND
# "elevation_callout" symbols on the full page, (2) a human reviews/approves
# the elevations in the UI (callouts need no review — nothing gets cropped
# from them), then (3) each APPROVED elevation is cropped out of the
# original full-res page and sent as its own request asking only for
# cabinet/countertop within that crop. Coordinates coming back from a crop
# are relative to the crop, so they're transformed back into the full
# page's own 0-1000 space before being merged with the elevation boxes and
# the untouched callouts into one final result, in the exact same
# {"summary":..., "objects":[...]} shape /api/generate uses.

def normalize_box(box, width, height):
    """Same pixel-vs-normalized safety net used in /api/generate's run_batch
    (see the comment there) — pulled out standalone here since the two-stage
    endpoints call it from more than one place (full page + every crop)."""
    if max(box) <= 1000:
        return [max(0, min(1000, v)) for v in box]
    if not width or not height:
        return [max(0, min(1000, v)) for v in box]
    x0, y0, x1, y1 = box
    return [
        max(0, min(1000, round((x0 / width) * 1000))),
        max(0, min(1000, round((y0 / height) * 1000))),
        max(0, min(1000, round((x1 / width) * 1000))),
        max(0, min(1000, round((y1 / height) * 1000))),
    ]


def render_pdf_page(pdf_bytes: bytes, page_num: int, dpi: int):
    """Renders exactly one 1-indexed page of a PDF at the given DPI —
    used instead of convert_from_bytes-for-every-page since the two-stage
    flow only ever needs one specific page at a time."""
    images = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=page_num, last_page=page_num, fmt="png")
    if not images:
        raise HTTPException(status_code=400, detail=f"Page {page_num} not found in this PDF")
    return images[0]


def iter_pdf_pages(pdf_bytes: bytes, dpi: int):
    """Yields (0-indexed position, PIL.Image) for every page of a PDF, ONE
    page at a time, instead of the single convert_from_bytes(pdf_bytes,
    dpi=dpi) call that used to render the whole document into memory as
    fully-decoded rasters in one go. A physically large multi-page sheet set
    (e.g. 28 ANSI-E pages) rendered that way holds every page's raw
    (uncompressed) bitmap alive simultaneously — that spike, not any request
    concurrency setting, is what was actually OOM-killing the container,
    since it happens before a single model request is even built. Getting
    the page count from pdfinfo first (metadata only, no rasterizing) lets
    each page be rendered, consumed, and freed before the next one starts."""
    try:
        page_count = pdfinfo_from_bytes(pdf_bytes).get("Pages")
    except Exception:
        page_count = None

    if not page_count:
        # Metadata lookup failed for some reason — fall back to the
        # original bulk render rather than silently yielding nothing.
        for p_idx, img in enumerate(convert_from_bytes(pdf_bytes, dpi=dpi, fmt="png")):
            yield p_idx, img
        return

    for p_idx in range(page_count):
        images = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=p_idx + 1, last_page=p_idx + 1, fmt="png")
        if images:
            yield p_idx, images[0]


def image_to_b64(img) -> str:
    buffered = BytesIO()
    img.save(buffered, format="PNG", optimize=True)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


# ===========================================================================
# Per-request cost / latency accounting
# ===========================================================================
# Every flow in this file ultimately makes the same kind of call — one chat
# completion with one or more images — but each one used to call the client
# directly, so nothing measured how long a request took or what it cost.
# timed_completion() is the single choke point all three flows now go
# through, so latency and token/cost accounting are captured identically
# everywhere, including for requests that FAIL (a model that times out or
# errors still consumed wall-clock time, and that's exactly the number worth
# seeing when comparing models).
#
# Cost comes from OpenRouter's own usage accounting rather than a local
# price table: passing usage:{include:true} makes it return the real credit
# amount it charged for that specific request, so this stays correct as
# prices change and across every model/provider without this file having to
# know anything about pricing. If a provider doesn't report cost, the token
# counts are still recorded and cost is simply left None.

def _usage_int(usage, *names):
    """Reads the first present integer field from an OpenAI/OpenRouter usage
    object, tolerating the naming differences between providers (e.g.
    prompt_tokens vs input_tokens)."""
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def extract_usage(response, latency_ms: float, model_name: str) -> dict:
    """Flattens whatever usage the provider reported into one flat record.
    Every field is optional — a provider that reports nothing still yields a
    usable record carrying the measured latency."""
    record = {
        "model": model_name,
        "latency_ms": round(latency_ms),
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "cached_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
    }

    usage = getattr(response, "usage", None)
    if usage is None:
        return record

    record["input_tokens"] = _usage_int(usage, "prompt_tokens", "input_tokens")
    record["output_tokens"] = _usage_int(usage, "completion_tokens", "output_tokens")
    record["total_tokens"] = _usage_int(usage, "total_tokens")

    # Reasoning/cached counts live in nested *_tokens_details objects and are
    # worth separating out: reasoning tokens are billed as output but aren't
    # part of the JSON the model actually returned, and cached input tokens
    # are billed at a different rate than fresh ones.
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        record["reasoning_tokens"] = _usage_int(details, "reasoning_tokens")
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        record["cached_tokens"] = _usage_int(details, "cached_tokens")

    cost = getattr(usage, "cost", None)
    if isinstance(cost, (int, float)):
        record["cost_usd"] = float(cost)

    if record["total_tokens"] is None and None not in (record["input_tokens"], record["output_tokens"]):
        record["total_tokens"] = record["input_tokens"] + record["output_tokens"]

    return record


async def timed_completion(model_name: str, messages: list, max_tokens: int,
                            extra_body: dict, usage_sink: Optional[list] = None,
                            stage: Optional[str] = None):
    """Makes one chat-completion request, appending a usage record (latency +
    tokens + cost) to usage_sink whether the call succeeds or raises. `stage`
    optionally tags the record so a multi-stage flow can tell which of its
    passes a given request belonged to."""
    body = dict(extra_body or {})
    # OpenRouter-specific: ask for real cost accounting on the response.
    body["usage"] = {"include": True}

    started = time.perf_counter()
    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0,
            top_p=0.1,
            max_tokens=max_tokens,
            extra_body=body,
        )
    except Exception as e:
        if usage_sink is not None:
            failed = extract_usage(None, (time.perf_counter() - started) * 1000, model_name)
            failed["error"] = str(e)
            if stage:
                failed["stage"] = stage
            usage_sink.append(failed)
        raise

    if usage_sink is not None:
        record = extract_usage(response, (time.perf_counter() - started) * 1000, model_name)
        if stage:
            record["stage"] = stage
        usage_sink.append(record)
    return response


def summarize_usage(records: list) -> dict:
    """Rolls a list of per-request usage records up into the totals shown per
    model. Latency is reported BOTH as a sum and as per-request stats, since
    the sum is only meaningful for sequential execution — under parallel
    execution the requests overlap, so mean/max describe what actually
    happened far better than a total that exceeds the wall clock."""
    records = list(records or [])
    latencies = [r["latency_ms"] for r in records if isinstance(r.get("latency_ms"), (int, float))]

    def total_of(field):
        values = [r[field] for r in records if isinstance(r.get(field), (int, float))]
        return sum(values) if values else None

    cost = total_of("cost_usd")
    return {
        "requests": len(records),
        "failed_requests": sum(1 for r in records if r.get("error")),
        "input_tokens": total_of("input_tokens"),
        "output_tokens": total_of("output_tokens"),
        "reasoning_tokens": total_of("reasoning_tokens"),
        "cached_tokens": total_of("cached_tokens"),
        "total_tokens": total_of("total_tokens"),
        "cost_usd": round(cost, 6) if cost is not None else None,
        "latency_ms": {
            "sum": round(sum(latencies)) if latencies else None,
            "mean": round(sum(latencies) / len(latencies)) if latencies else None,
            "min": round(min(latencies)) if latencies else None,
            "max": round(max(latencies)) if latencies else None,
        },
        "requests_detail": records,
    }


async def call_model_for_objects(model_name: str, system_prompt: str, user_prompt: str,
                                  image_b64: str, width: int, height: int,
                                  allowed_labels: set, override_note: str,
                                  usage_sink: Optional[list] = None,
                                  stage: Optional[str] = None) -> list:
    """Sends ONE image to a model with a given system+user prompt, parses the
    {"objects":[...]} response, keeps only entries whose label is in
    allowed_labels, and normalizes every box to 0-1000 relative to
    (width, height). Shared by both stages of the two-stage flow — same
    parsing/repair/normalization discipline as the main /api/generate path,
    just scoped to a single image and a restricted label set per call."""
    final_system_instruction = system_prompt + f"""

    [SYSTEM OVERRIDE - CRITICAL]
    You have been provided with EXACTLY 1 image. image_index is always 0.
    {override_note}
    """

    reasoning_kind = model_reasoning_kind(model_name)
    extra_body = {}
    if reasoning_kind in ("mandatory", "optional"):
        extra_body["reasoning"] = {"effort": "low"}
    max_response_tokens = 16000 if reasoning_kind in ("mandatory", "optional") else 10000

    messages = [
        {"role": "system", "content": final_system_instruction},
        {"role": "user", "content": [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]}
    ]

    response = await timed_completion(model_name, messages, max_response_tokens,
                                       extra_body, usage_sink=usage_sink, stage=stage)

    if not response.choices:
        raise Exception("Model returned no choices")
    message = response.choices[0].message
    if message is None:
        raise Exception("Model returned empty message")
    response_text = message.content
    print(response_text)
    if not response_text:
        raise Exception(f"Model returned empty content. Finish reason: {response.choices[0].finish_reason}")

    response_text = response_text.replace("```json", "").replace("```", "").strip()
    try:
        parsed_json = json.loads(response_text)
    except json.JSONDecodeError:
        parsed_json = json.loads(repair_json(response_text))

    if isinstance(parsed_json, list):
        parsed_json = {"objects": parsed_json}
    elif not isinstance(parsed_json, dict):
        raise Exception(f"Unexpected top-level JSON type from model: {type(parsed_json).__name__}")

    raw_objects = parsed_json.get("objects", [])
    if not isinstance(raw_objects, list):
        raw_objects = []

    valid_objects = []
    for obj in raw_objects:
        if not isinstance(obj, dict):
            continue
        label = obj.get("label")
        if label not in allowed_labels:
            continue
        box = resolve_box(obj)
        if box is None:
            continue
        valid_objects.append({"label": label, "box": normalize_box(box, width, height)})

    return valid_objects


# ===========================================================================
# Execution model
# ===========================================================================
# The old version always packed EVERY page of EVERY file into a single user
# message and ran one API call per model, one model after another.
#
# This version keeps that as the default, but makes each axis configurable
# from the frontend's "Execution Settings" modal:
#
#   model_execution_mode  ("sequential" | "parallel")
#       Whether the configured models are queried one after another or all
#       at once.
#
#   file_grouping_mode    ("single" | "split")
#       "single" -> every uploaded file's pages are combined into ONE group
#                   (this is the original behaviour).
#       "split"  -> each uploaded file becomes its OWN group, sent as a
#                   separate request (or requests) per model.
#   file_execution_mode   ("sequential" | "parallel")
#       Only meaningful when file_grouping_mode == "split". Controls whether
#       the per-file requests for a given model run one after another or
#       concurrently.
#
#   page_grouping_mode    ("single" | "split")
#       "single" -> all pages inside a file-group are sent together as one
#                   request (original behaviour).
#       "split"  -> each page inside a file-group is sent as its own,
#                   separate request.
#   page_execution_mode   ("sequential" | "parallel")
#       Only meaningful when page_grouping_mode == "split". Controls whether
#       the per-page requests inside a single file-group run one after
#       another or concurrently.
#
# Whatever combination is chosen, results from every request are merged back
# together into a single JSON response per model, so the frontend contract
# (one {model, response} entry per configured model) never changes.
#
# ===========================================================================
# Counting philosophy
# ===========================================================================
# The LLM is asked ONLY to find objects and their locations. Counting is
# never trusted from the model — "summary" in the final response is always
# computed here, in code, from the actual "objects" list. Any "summary" a
# model includes in its own raw output is parsed but discarded; only
# "objects" is ever read from a model response (see run_batch below).
#
# ===========================================================================
# Run history
# ===========================================================================
# Every /api/generate call is persisted to HISTORY_DIR/<run_id>/:
#   meta.json    - timestamp, system_prompt, per-model prompts, dpi,
#                  execution settings, file list, and per-model results
#                  (raw response + code-computed counts).
#   images/      - every rendered page, saved once (shared across models),
#                  so history shows exactly what the models were shown,
#                  independent of later DPI/viewer changes.
# The oldest runs are pruned once HISTORY_MAX_RUNS is exceeded, since
# high-DPI dense sheets can add up in disk space quickly.


@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


def compute_counts(response_text: str) -> dict:
    """Recompute per-label counts from a model's raw response JSON, ignoring
    any 'summary' the model may have included — counting is code's job."""
    counts = {"cabinets": 0, "countertops": 0, "elevations": 0, "elevation_callouts": 0,
              "callouts": 0, "floor_plans": 0}
    try:
        data = json.loads(response_text)
        objects = data.get("objects", []) if isinstance(data, dict) else []
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            key = LABEL_TO_SUMMARY_KEY.get(obj.get("label"))
            if key:
                counts[key] += 1
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return counts


def get_history_image_path(run_id: str, filename: str, size: str):
    """Return the path to a cached, resized JPEG copy of a saved history
    page image, generating it on first request. The original PNG saved at
    generation time is never modified or moved — this only ever adds a
    small derived file next to it. Works identically for old and new runs,
    since it derives from whatever's already on disk rather than anything
    written at save time.
    """
    preset = HISTORY_IMAGE_SIZES.get(size)
    if preset is None:
        return None

    # Guard against path traversal via a crafted filename/run_id.
    if "/" in run_id or ".." in run_id or "\\" in run_id:
        return None
    filename = os.path.basename(filename)
    if not filename or ".." in filename:
        return None

    images_dir = os.path.join(HISTORY_DIR, run_id, "images")
    src_path = os.path.join(images_dir, filename)
    if not os.path.isfile(src_path):
        return None

    cache_dir = os.path.join(images_dir, HISTORY_CACHE_SUBDIR)
    os.makedirs(cache_dir, exist_ok=True)
    base_name, _ext = os.path.splitext(filename)
    cache_path = os.path.join(cache_dir, f"{base_name}_{size}.jpg")

    # Reuse the cached copy if it's already there and not older than the
    # source (covers the source ever being replaced/re-saved).
    if os.path.isfile(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(src_path):
        return cache_path

    max_dim, quality = preset
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        im.save(cache_path, format="JPEG", quality=quality, optimize=True)

    return cache_path


def prune_history():
    try:
        run_dirs = [
            d for d in os.listdir(HISTORY_DIR)
            if os.path.isdir(os.path.join(HISTORY_DIR, d))
        ]
    except FileNotFoundError:
        return
    run_dirs.sort()  # run_id is timestamp-prefixed, so lexical sort == chronological
    excess = len(run_dirs) - HISTORY_MAX_RUNS
    for old_dir in run_dirs[:max(0, excess)]:
        shutil.rmtree(os.path.join(HISTORY_DIR, old_dir), ignore_errors=True)


@app.post("/api/generate")
async def generate_responses(
        system_prompt: str = Form(...),
        models_data: str = Form(...),
        dpi: int = Form(200),
        max_dim: Optional[int] = Form(None),
        model_execution_mode: str = Form("sequential"),
        file_grouping_mode: str = Form("single"),
        file_execution_mode: str = Form("sequential"),
        page_grouping_mode: str = Form("single"),
        page_execution_mode: str = Form("sequential"),
        max_parallel_pages: Optional[int] = Form(None),
        ground_truth: str = Form("[]"),   # JSON, same shape /api/grid/detect accepts
        iou_threshold: float = Form(0.5),
        files: List[UploadFile] = File(...)
):
    models = json.loads(models_data)
    iou_threshold = max(0.0, min(1.0, iou_threshold))

    try:
        gt_items = extract_ground_truth_items(json.loads(ground_truth) if ground_truth else [])
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid ground_truth payload")

    # Guard against absurd or malicious values while still respecting the
    # user's chosen resolution (matches the 72-600 range exposed in the UI).
    dpi = max(72, min(600, dpi))
    # Optional: None/absent means "no resize", the original DPI-only flow —
    # only clamp and apply resize_to_fit() below when the caller actually
    # set a value, rather than forcing every request through a resize.
    if max_dim is not None:
        max_dim = max(256, min(8092, max_dim))

    def norm_mode(value, default="sequential"):
        value = (value or default).strip().lower()
        return value if value in ("sequential", "parallel") else default

    model_execution_mode = norm_mode(model_execution_mode)
    file_execution_mode = norm_mode(file_execution_mode)
    page_execution_mode = norm_mode(page_execution_mode)
    file_grouping_mode = "split" if (file_grouping_mode or "").strip().lower() == "split" else "single"
    page_grouping_mode = "split" if (page_grouping_mode or "").strip().lower() == "split" else "single"

    # Optional: caps how many page-level requests (page_grouping_mode=split,
    # page_execution_mode=parallel) may be in flight at once for this run,
    # across every model/file. Left unset, a large PDF's every page fires
    # off as one big burst of concurrent requests — which is what actually
    # overwhelmed a run and made it fail outright rather than just run
    # slower. None means "no cap", the original unbounded-parallel behaviour.
    if max_parallel_pages is not None:
        max_parallel_pages = max(1, min(15, max_parallel_pages))

    execution_settings = {
        "model_execution_mode": model_execution_mode,
        "file_grouping_mode": file_grouping_mode,
        "file_execution_mode": file_execution_mode,
        "page_grouping_mode": page_grouping_mode,
        "page_execution_mode": page_execution_mode,
        "max_parallel_pages": max_parallel_pages,
    }

    # ===============================
    # Convert every PDF page to an image
    # ===============================
    # file_pages[file_idx] = [(page_num, base64_png, filename), ...]
    file_pages: List[List[tuple]] = []
    file_names: List[str] = []

    for f_idx, file in enumerate(files):
        await file.seek(0)
        pdf_bytes = await file.read()
        file_names.append(file.filename)

        pages = []
        for p_idx, img in iter_pdf_pages(pdf_bytes, dpi):
            # native_w/native_h are the untouched DPI render's size — needed
            # below to convert back to PDF points (native_w * 72 / dpi is
            # only correct against the ACTUAL dpi render, not whatever
            # resize_to_fit() produces from it two lines down).
            native_w, native_h = img.width, img.height

            # DPI alone renders a physically-large sheet to far more pixels
            # than a physically-small one at the same setting, and every
            # vision model downscales whatever it's actually given before
            # its own encoder sees it — so an oversized render doesn't get
            # more detail through, it gets an internal downscale we don't
            # control, and the model's own coordinate math ends up relative
            # to whatever internal frame IT picked, not the render we
            # measured page_pixel_dims from. Doing that resize ourselves,
            # to the same max_dim contract the Grid flow already uses (see
            # resize_to_fit()), makes the frame the model measures against
            # the same frame we know the pixel size of, regardless of how
            # physically large or small the source sheet is.
            #
            # Optional: only applied when max_dim was actually set. Left
            # unset, this is exactly the original DPI-only flow — img stays
            # the native render, native_w/native_h == img.width/img.height.
            if max_dim is not None:
                img = resize_to_fit(img, max_dim)
            buffered = BytesIO()
            img.save(buffered, format="PNG", optimize=True)
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            pages.append((p_idx + 1, img_str, file.filename, img.width, img.height, native_w, native_h))
        file_pages.append(pages)

    total_images = sum(len(p) for p in file_pages)

    # ===============================
    # Set up this run's history folder + persist the rendered pages once,
    # shared across every model (they all see the exact same images).
    # ===============================
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = os.path.join(HISTORY_DIR, run_id)
    images_dir = os.path.join(run_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # (file_index, page_num) -> (width_px, height_px) of the exact image
    # sent to the models. Fallback used if a model ignores the 0-1000
    # normalization and returns raw pixel coordinates instead — see the
    # coordinate normalization step in run_batch below.
    page_pixel_dims = {}
    # Ground truth is measured in PDF points, so each page's render is
    # converted back to its 72-DPI equivalent size rather than normalized
    # against the pixel size of whatever DPI this run used — see
    # normalize_ground_truth() for why that distinction matters. Computed
    # from native_w/native_h (the untouched DPI render), NOT from px_w/px_h
    # below — those are post-resize_to_fit() and would silently shrink
    # every ground-truth box if used here instead.
    page_points_dims = {}

    pages_meta = []
    for f_idx, pages in enumerate(file_pages):
        for (page_num, b64, fname, px_w, px_h, native_w, native_h) in pages:
            image_filename = f"{f_idx}_{page_num}.png"
            with open(os.path.join(images_dir, image_filename), "wb") as f:
                f.write(base64.b64decode(b64))
            pages_meta.append({
                "file_index": f_idx,
                "file_name": fname,
                "page_num": page_num,
                "image_url": f"/history-files/{run_id}/images/{image_filename}",
            })
            page_pixel_dims[(f_idx, page_num)] = (px_w, px_h)
            page_points_dims[(f_idx, page_num)] = (native_w * 72 / dpi, native_h * 72 / dpi)

    multi_file = len(file_pages) > 1
    file_index_by_name = {name: idx for idx, name in enumerate(file_names)}
    ground_truth_norm = normalize_ground_truth(gt_items, page_points_dims, multi_file,
                                                file_index_by_name)

    # Nothing to analyze — skip the model loop and say so clearly instead of
    # sending an empty request to every model.
    if total_images == 0:
        results = [
            {"model": item["model"], "response": json.dumps({"error": "No pages were found in the uploaded PDF(s)."}),
             "score": None, "usage": None}
            for item in models
        ]
        save_history_meta(run_id, run_dir, system_prompt, dpi, execution_settings,
                           file_names, pages_meta, models, results,
                           ground_truth_norm, iou_threshold)
        prune_history()
        return {"results": results, "run_id": run_id}

    # ===============================
    # Build file groups, then batches (requests) within each group
    # ===============================
    if file_grouping_mode == "split":
        file_groups = [[i] for i in range(len(file_pages))]
    else:
        file_groups = [list(range(len(file_pages)))]

    # file_group_batches[g] = list of batches; a batch is a list of
    # (file_index, page_num, base64_png, filename) tuples that will be sent
    # together in ONE request.
    file_group_batches: List[List[list]] = []
    for group in file_groups:
        entries = [
            (fidx, p, b64, fname)
            for fidx in group
            for (p, b64, fname, _px_w, _px_h, _native_w, _native_h) in file_pages[fidx]
        ]
        if page_grouping_mode == "split":
            batches = [[entry] for entry in entries]
        else:
            batches = [entries]
        file_group_batches.append(batches)

    total_requests_per_model = sum(len(b) for b in file_group_batches)
    print(f"[generate] run={run_id} {total_images} page(s) at {dpi} DPI split into "
          f"{len(file_group_batches)} file-group(s) / {total_requests_per_model} request(s) per model "
          f"(models: {model_execution_mode}, files: {file_grouping_mode}/{file_execution_mode}, "
          f"pages: {page_grouping_mode}/{page_execution_mode}"
          f"{f', max {max_parallel_pages} page(s) at once' if max_parallel_pages is not None else ''}).")

    # Shared across every model/file-group in this run (not recreated per
    # call), so "max_parallel_pages" caps the true total of concurrent
    # page-level requests in flight, not just within one group. Only
    # meaningful when page_execution_mode == "parallel" — the sequential
    # path below never has more than one request in flight anyway.
    page_semaphore = asyncio.Semaphore(max_parallel_pages) if max_parallel_pages is not None else None

    # ===============================
    # Run a single batch (one API call) for one model
    # ===============================
    async def run_batch(model_name: str, user_prompt: str, entries: list, usage_sink: list):
        n = len(entries)
        content = [{"type": "text", "text": user_prompt}]
        local_mapping = []

        for local_idx, (fidx, page_num, b64, fname) in enumerate(entries):
            content.append({
                "type": "text",
                "text": f"\n--- START OF IMAGE {local_idx} (File: {fname}, Page: {page_num}) ---\n"
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"}
            })
            content.append({
                "type": "text",
                "text": f"\n--- END OF IMAGE {local_idx} ---\n"
            })
            local_mapping.append({"file_index": fidx, "page_num": page_num})

        if n == 1:
            override_text = """
                    [SYSTEM OVERRIDE - CRITICAL]
                    You have been provided with EXACTLY 1 image in this request.
                    For every object found, its "image_index" MUST be 0.
                    """
        else:
            override_text = f"""
                    [SYSTEM OVERRIDE - CRITICAL]
                    You have been provided with EXACTLY {n} images, all in this single request.
                    They are marked sequentially from IMAGE 0 to IMAGE {n - 1}.
                    You MUST process EACH image separately.
                    DO NOT group all objects into "image_index": 0.
                    If an object is found in IMAGE 2, its "image_index" MUST be 2.
                    """

        final_system_instruction = system_prompt + override_text

        reasoning_kind = model_reasoning_kind(model_name)

        # Output grows with OBJECT COUNT on the page, not page count in the
        # batch — with page_grouping_mode=split, n is almost always 1, so a
        # formula that only scales with n stays flat regardless of sheet
        # density. The detailed left/top/right/bottom schema is verbose per
        # object, so even non-reasoning models need real headroom on a dense
        # sheet, not just the old flat ~4-5k tokens. "mandatory" and
        # "optional" reasoning models get the same larger budget, since both
        # now spend some of it on an actual low-effort reasoning pass.
        base_tokens = 12000 if reasoning_kind in ("mandatory", "optional") else 8000
        max_response_tokens = min(32000, base_tokens + n * 2000)

        extra_body = {}
        if reasoning_kind in ("mandatory", "optional"):
            # "mandatory" models can't fully disable thinking anyway,
            # "optional" ones (gpt-5, claude) used to have it forced off —
            # but a full-sheet scan (finding every elevation on a dense,
            # busy drawing) is exactly the kind of task a model does worse
            # on with zero reasoning. "low" effort gives it a little room to
            # actually scan systematically without the runaway/empty-content
            # failure mode a fully default (uncapped) reasoning pass caused.
            extra_body["reasoning"] = {"effort": "low"}

        messages = [
            {"role": "system", "content": final_system_instruction},
            {"role": "user", "content": content}
        ]

        try:
            response = await timed_completion(model_name, messages, max_response_tokens,
                                               extra_body, usage_sink=usage_sink)

            if not response.choices:
                raise Exception("Model returned no choices")

            message = response.choices[0].message
            if message is None:
                raise Exception("Model returned empty message")

            response_text = message.content
            if not response_text:
                raise Exception(f"Model returned empty content. Finish reason: {response.choices[0].finish_reason}")

            if response.choices[0].finish_reason == "length":
                print(f"[{model_name}] WARNING: response was truncated at {max_response_tokens} tokens; "
                      f"some pages/objects may be missing from this batch.")

            response_text = response_text.replace("```json", "").replace("```", "").strip()

            try:
                parsed_json = json.loads(response_text)
            except json.JSONDecodeError:
                print(f"[{model_name}] Invalid JSON. Attempting repair...")
                parsed_json = json.loads(repair_json(response_text))

            # Some models occasionally ignore the {"objects":[...]} wrapper
            # and return a bare array instead. Normalize rather than crash.
            if isinstance(parsed_json, list):
                parsed_json = {"objects": parsed_json}
            elif not isinstance(parsed_json, dict):
                raise Exception(
                    f"Unexpected top-level JSON type from model: {type(parsed_json).__name__}"
                )

            raw_objects = parsed_json.get("objects", [])
            if not isinstance(raw_objects, list):
                raw_objects = []

            valid_objects = []
            for obj in raw_objects:
                # Defensive: skip any entry that isn't itself an object
                # (a stray nested list or string slipped in by the model).
                if not isinstance(obj, dict):
                    continue

                label = obj.get("label")
                if label not in VALID_LABELS:
                    continue

                box = resolve_box(obj)
                if box is None:
                    continue  # malformed box — skip rather than pass garbage downstream

                img_idx = obj.get("image_index")
                if isinstance(img_idx, int) and 0 <= img_idx < n:
                    obj["file_index"] = local_mapping[img_idx]["file_index"]
                    obj["page_num"] = local_mapping[img_idx]["page_num"]

                obj.pop("image_index", None)

                # Some models ignore the requested coordinate scale and
                # report in a different one than the prompt asked for —
                # observed repeatedly with claude-sonnet-5 returning raw
                # pixel coordinates of the exact image it was sent, and
                # some prompts now legitimately ask for 0-1 fractions
                # instead of 0-1000 (see drafts/new_annotation). Detect
                # which of the three scales the box is actually in and
                # normalize to 0-1000 regardless, so every response leaving
                # this endpoint is on the same scale independent of what
                # the model/prompt combination actually produced:
                #   - every value <= 1.0: a 0-1 fraction (an object under
                #     0.1% of the image in both dimensions is never a
                #     genuine 0-1000 box, so this reading is unambiguous)
                #   - every value <= 1000: already 0-1000
                #   - anything above 1000: raw pixels, rescaled using the
                #     REAL pixel size of the exact page this object came
                #     from. No model-name check — this only ever activates
                #     when the numbers are actually out of range, so models
                #     that already normalize correctly are unaffected.
                if max(box) <= 1.0:
                    box_out = [max(0, min(1000, round(v * 1000))) for v in box]
                elif max(box) > 1000:
                    dims = page_pixel_dims.get((obj.get("file_index"), obj.get("page_num")))
                    box_out = box
                    if dims:
                        px_w, px_h = dims
                        x0, y0, x1, y1 = box
                        box_out = [
                            round((x0 / px_w) * 1000),
                            round((y0 / px_h) * 1000),
                            round((x1 / px_w) * 1000),
                            round((y1 / px_h) * 1000),
                        ]
                    box_out = [max(0, min(1000, v)) for v in box_out]
                else:
                    box_out = [max(0, min(1000, v)) for v in box]

                # Keep only what the frontend actually needs — label, box,
                # file_index, page_num. Any left/top/right/bottom fields the
                # model was asked to compute were scaffolding for building
                # "box" correctly and aren't needed downstream.
                valid_objects.append({
                    "label": obj["label"],
                    "box": box_out,
                    "file_index": obj.get("file_index"),
                    "page_num": obj.get("page_num"),
                })

            return {"ok": True, "objects": valid_objects}

        except Exception as e:
            print(f"Error from model {model_name} on a batch: {e}")
            return {"ok": False, "error": str(e)}

    # ===============================
    # Run every batch for one model, respecting file/page execution modes
    # ===============================
    async def run_batch_limited(model_name: str, user_prompt: str, entries: list, usage_sink: list):
        if page_semaphore is None:
            return await run_batch(model_name, user_prompt, entries, usage_sink)
        async with page_semaphore:
            return await run_batch(model_name, user_prompt, entries, usage_sink)

    async def run_batches_in_group(model_name: str, user_prompt: str, batches: list, usage_sink: list):
        if page_execution_mode == "parallel":
            return await asyncio.gather(*[run_batch_limited(model_name, user_prompt, b, usage_sink) for b in batches])
        results = []
        for b in batches:
            results.append(await run_batch(model_name, user_prompt, b, usage_sink))
        return results

    async def run_model(item: dict):
        model_name = item["model"]
        user_prompt = item["prompt"]
        usage_sink = []
        model_started = time.perf_counter()

        if file_execution_mode == "parallel":
            group_results = await asyncio.gather(*[
                run_batches_in_group(model_name, user_prompt, batches, usage_sink)
                for batches in file_group_batches
            ])
        else:
            group_results = []
            for batches in file_group_batches:
                group_results.append(await run_batches_in_group(model_name, user_prompt, batches, usage_sink))

        all_objects = []
        errors = []
        for batch_results in group_results:
            for r in batch_results:
                if r["ok"]:
                    all_objects.extend(r["objects"])
                else:
                    errors.append(r["error"])

        usage = summarize_usage(usage_sink)
        # Wall-clock for this model's whole run, which is what the sum of
        # per-request latencies stops describing as soon as any axis is set
        # to parallel and requests start overlapping.
        usage["wall_ms"] = round((time.perf_counter() - model_started) * 1000)

        # If every single batch failed, surface that clearly instead of
        # returning an empty-but-successful-looking payload.
        if errors and not all_objects and len(errors) == total_requests_per_model:
            return {
                "model": model_name,
                "response": json.dumps({"error": "; ".join(errors)}, ensure_ascii=False),
                "score": None,
                "usage": usage,
            }

        # Counting is ALWAYS done here, from the actual objects list — never
        # trusted from the model's own output.
        summary = {"cabinets": 0, "countertops": 0, "elevations": 0, "elevation_callouts": 0,
                   "callouts": 0, "floor_plans": 0}
        for obj in all_objects:
            key = LABEL_TO_SUMMARY_KEY.get(obj.get("label"))
            if key:
                summary[key] += 1

        final_json = {"summary": summary, "objects": all_objects}
        if errors:
            final_json["errors"] = errors

        score_result = safe_score(
            predictions_from_objects(all_objects, multi_file),
            ground_truth_norm, iou_threshold,
        )

        return {
            "model": model_name,
            "response": json.dumps(final_json, ensure_ascii=False),
            "score": score_result,
            "usage": usage,
        }

    # ===============================
    # Run every model, respecting model_execution_mode
    # ===============================
    if model_execution_mode == "parallel":
        results = await asyncio.gather(*[run_model(item) for item in models])
    else:
        results = []
        for item in models:
            results.append(await run_model(item))

    results = list(results)
    save_history_meta(run_id, run_dir, system_prompt, dpi, execution_settings,
                       file_names, pages_meta, models, results,
                       ground_truth_norm, iou_threshold, max_dim)
    prune_history()

    return {"results": results, "run_id": run_id}


def save_history_meta(run_id, run_dir, system_prompt, dpi, execution_settings,
                       file_names, pages_meta, models, results,
                       ground_truth=None, iou_threshold=None, max_dim=None):
    """Persist everything needed to fully reconstruct this run later: the
    prompts used (system + per-model), settings, file/page list, and every
    model's raw response alongside code-computed counts, its location score
    against whatever ground truth was supplied, and what the run cost (per-
    request latency and token/cost totals)."""
    results_by_model = {r["model"]: r for r in results}

    def response_of(model):
        entry = results_by_model.get(model)
        return entry["response"] if entry else ""

    meta = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "system_prompt": system_prompt,
        "dpi": dpi,
        "max_dim": max_dim,
        "execution_settings": execution_settings,
        "iou_threshold": iou_threshold,
        "ground_truth": ground_truth or [],
        "files": file_names,
        "pages": pages_meta,
        "results": [
            {
                "model": item["model"],
                "prompt": item["prompt"],
                "response": response_of(item["model"]),
                "counts": compute_counts(response_of(item["model"])),
                "score": (results_by_model.get(item["model"]) or {}).get("score"),
                "usage": (results_by_model.get(item["model"]) or {}).get("usage"),
                # Filled in later via PUT /api/history/{run_id}/expected-summary,
                # once a human pastes in the ground-truth counts for this run —
                # kept here (not a separate file) so it travels with the run and
                # survives export/prune the same way everything else does.
                "expected_summary": None,
            }
            for item in models
        ],
    }

    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ===============================
# History API
# ===============================

@app.get("/api/history-image/{run_id}/{filename}")
async def get_history_image(run_id: str, filename: str, size: str = "display"):
    """Serves a small, cached JPEG copy of a saved history page image,
    generated on first request from the original PNG. Used instead of the
    raw /history-files/... original for thumbnails and the page-detail
    canvas, since those are shown far smaller than the original render."""
    path = get_history_image_path(run_id, filename, size)
    if not path:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/jpeg")


def compact_usage(usage):
    """The few usage numbers worth showing in the run LIST, without the
    per-request detail the full record carries."""
    if not isinstance(usage, dict):
        return None
    latency = usage.get("latency_ms") or {}
    return {
        "requests": usage.get("requests"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": usage.get("cost_usd"),
        "wall_ms": usage.get("wall_ms"),
        "mean_latency_ms": latency.get("mean"),
    }


def compact_score(score):
    """Headline precision/recall/F1 for the run list — per-type breakdown and
    matched objects stay in the detail endpoint."""
    if not isinstance(score, dict) or "metrics" not in score:
        return None
    metrics = score.get("metrics") or {}
    return {
        "precision": metrics.get("precision"),
        "recall": metrics.get("recall"),
        "f1": metrics.get("f1"),
        "iou_threshold": score.get("iou_threshold"),
    }


@app.get("/api/history")
async def list_history(run_type: Optional[str] = None):
    """Lightweight list for the history browser — omits full page lists and
    full raw responses to keep this fast even with many past runs.

    run_type filters to "single" (regular benchmark runs) or "two_stage"
    (elevation-first runs) — the Benchmark and Two-Stage tabs each only
    ever ask for their own kind, so their history lists never mix."""
    if not os.path.isdir(HISTORY_DIR):
        return {"runs": []}

    run_ids = sorted(
        (d for d in os.listdir(HISTORY_DIR) if os.path.isdir(os.path.join(HISTORY_DIR, d))),
        reverse=True,  # newest first
    )

    runs = []
    for run_id in run_ids:
        meta_path = os.path.join(HISTORY_DIR, run_id, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        meta_run_type = meta.get("run_type", "single")
        if run_type and meta_run_type != run_type:
            continue

        first_page = (meta.get("pages") or [{}])[0]
        first_image_url = first_page.get("image_url")
        thumbnail_url = None
        if first_image_url:
            thumb_filename = os.path.basename(first_image_url)
            thumbnail_url = f"/api/history-image/{run_id}/{thumb_filename}?size=thumb"

        runs.append({
            "run_id": meta.get("run_id", run_id),
            "created_at": meta.get("created_at"),
            "run_type": meta_run_type,
            "dpi": meta.get("dpi"),
            "stage1_dpi": meta.get("stage1_dpi"),
            "stage2_dpi": meta.get("stage2_dpi"),
            "execution_settings": meta.get("execution_settings"),
            "files": meta.get("files", []),
            "page_count": len(meta.get("pages", [])),
            "thumbnail_url": thumbnail_url,
            "models": [
                {
                    "model": r["model"],
                    "counts": r.get("counts"),
                    # Compact rollup only — the full per-request breakdown
                    # stays in the detail endpoint, since this list is
                    # deliberately kept light enough to load many runs at once.
                    "usage": compact_usage(r.get("usage")),
                    "score": compact_score(r.get("score")),
                }
                for r in meta.get("results", [])
            ],
        })

    return {"runs": runs}


@app.get("/api/history/{run_id}")
async def get_history_run(run_id: str):
    """Full detail for one run — prompts, settings, every page image URL,
    and every model's raw response, for the history detail view."""
    # Guard against path traversal via a crafted run_id.
    if "/" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    meta_path = os.path.join(HISTORY_DIR, run_id, "meta.json")
    if not os.path.isfile(meta_path):
        raise HTTPException(status_code=404, detail="Run not found")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # Add a resized "display" URL alongside each page's original image_url,
    # computed here rather than stored, so meta.json on disk is untouched.
    for page in meta.get("pages", []):
        original_url = page.get("image_url")
        if original_url:
            page_filename = os.path.basename(original_url)
            page["display_image_url"] = f"/api/history-image/{run_id}/{page_filename}?size=display"

    return meta


class ExpectedSummaryUpdate(BaseModel):
    model: str
    expected_summary: dict


@app.put("/api/history/{run_id}/expected-summary")
async def set_expected_summary(run_id: str, payload: ExpectedSummaryUpdate):
    """Persists a human-pasted ground-truth summary (counts per label) for
    one model's result within a run, for later accuracy evaluation. Stored
    directly in that run's meta.json, alongside everything else already
    saved for it — nothing about the existing history format changes,
    this only ever adds/overwrites the "expected_summary" key already
    reserved on each result entry."""
    if "/" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    meta_path = os.path.join(HISTORY_DIR, run_id, "meta.json")
    if not os.path.isfile(meta_path):
        raise HTTPException(status_code=404, detail="Run not found")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    target = next((r for r in meta.get("results", []) if r.get("model") == payload.model), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Model not found in this run")

    target["expected_summary"] = payload.expected_summary

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"ok": True, "model": payload.model, "expected_summary": payload.expected_summary}


@app.delete("/api/history/{run_id}")
async def delete_history_run(run_id: str):
    if "/" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    run_dir = os.path.join(HISTORY_DIR, run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail="Run not found")

    shutil.rmtree(run_dir, ignore_errors=True)
    return {"deleted": run_id}


# ===============================
# Two-stage detection API
# ===============================
# Mirrors the file/page execution controls from /api/generate (see the
# "Execution model" note above run_batches_in_group): every uploaded file's
# pages are still sent one image per request (stage 1 needs one full-page
# image, stage 2 needs one crop per approved elevation) - there is no
# "grouping" axis here, only ordering/concurrency of those independent
# per-page requests, controlled by file_execution_mode / page_execution_mode
# ("sequential" | "parallel").
#
# "elevation_callout" is detected in STAGE 1, not stage 2: callouts live on
# floor plans/RCPs (never inside an elevation), so the same full-page image
# stage 1 already renders to find "elevation" frames is exactly what's
# needed to find them too - asking for both labels in that one request
# avoids a second full-page model call per page purely for callouts. They
# don't need human review the way elevations do (nothing gets cropped from
# them), so they're carried straight through stage 2 into the final result.

def normalize_exec_mode(value, default="sequential"):
    value = (value or default).strip().lower()
    return value if value in ("sequential", "parallel") else default


@app.post("/api/two-stage/elevations")
async def two_stage_detect_elevations(
        files: List[UploadFile] = File(...),
        stage1_dpi: int = Form(200),
        model: str = Form(...),
        system_prompt_stage1: str = Form(...),
        elevation_prompt: str = Form(...),
        file_execution_mode: str = Form("sequential"),
        page_execution_mode: str = Form("sequential"),
):
    """Stage 1: find 'elevation' frames AND 'elevation_callout' symbols, on
    EVERY page of EVERY uploaded file, in one pass per page. Elevations come
    back grouped by (file_index, page_num) with generated ids so the
    frontend can let a human include/exclude each one, per page, before
    stage 2 ever runs on that page. Callouts come back alongside them, with
    no id/review step, since stage 2 just carries them through untouched."""
    stage1_dpi = max(72, min(600, stage1_dpi))
    file_execution_mode = normalize_exec_mode(file_execution_mode)
    page_execution_mode = normalize_exec_mode(page_execution_mode)

    override_note = (
        'STAGE 1 OF 2 - ELEVATIONS AND PLAN-LEVEL CALLOUTS: for this '
        'request, find and report objects with label "elevation" (the '
        'full framed elevation drawings) AND label "elevation_callout" '
        '(small reference symbols that appear on floor plans/RCPs, '
        'pointing at an elevation - never inside an elevation drawing '
        'itself). Do not report cabinet or countertop in this pass - '
        'those live inside each elevation and are handled in a separate, '
        'later request, once every elevation frame found here has been '
        'reviewed and confirmed by a human.'
    )

    usage_sink = []
    started = time.perf_counter()

    async def process_page(f_idx: int, file_name: str, page_num: int, img):
        width, height = img.size
        image_b64 = image_to_b64(img)
        try:
            objects = await call_model_for_objects(
                model, system_prompt_stage1, elevation_prompt, image_b64, width, height,
                allowed_labels={"elevation", "elevation_callout"}, override_note=override_note,
                usage_sink=usage_sink, stage="stage1",
            )
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Model error on {file_name} page {page_num}: {e}",
            )
        elevations = [
            {"id": f"f{f_idx}-p{page_num}-elev-{i}", "box": obj["box"]}
            for i, obj in enumerate(o for o in objects if o["label"] == "elevation")
        ]
        callouts = [
            {"box": obj["box"]}
            for obj in objects if obj["label"] == "elevation_callout"
        ]
        return {
            "file_index": f_idx, "file_name": file_name, "page_num": page_num,
            "page_width": width, "page_height": height,
            "elevations": elevations, "callouts": callouts,
        }

    async def process_file(f_idx: int, file: UploadFile):
        pdf_bytes = await file.read()
        images = convert_from_bytes(pdf_bytes, dpi=stage1_dpi, fmt="png")
        pages = list(enumerate(images, start=1))
        if page_execution_mode == "parallel":
            return await asyncio.gather(*[
                process_page(f_idx, file.filename, p_num, img) for p_num, img in pages
            ])
        results = []
        for p_num, img in pages:
            results.append(await process_page(f_idx, file.filename, p_num, img))
        return results

    if file_execution_mode == "parallel":
        per_file_results = await asyncio.gather(*[
            process_file(i, f) for i, f in enumerate(files)
        ])
    else:
        per_file_results = []
        for i, f in enumerate(files):
            per_file_results.append(await process_file(i, f))

    results = [page_result for file_results in per_file_results for page_result in file_results]

    # Stage 1 is its own request, so its cost is reported here and carried
    # back by the frontend into the stage 2 call — that's what lets the final
    # result show what the two stages cost TOGETHER, which is the only number
    # that compares fairly against the one-stage flow's single figure.
    usage = summarize_usage(usage_sink)
    usage["wall_ms"] = round((time.perf_counter() - started) * 1000)
    return {"results": results, "usage": usage}


@app.post("/api/two-stage/details")
async def two_stage_detect_details(
        files: List[UploadFile] = File(...),
        stage1_dpi: int = Form(200),
        stage2_dpi: int = Form(300),
        stage2_concurrency: int = Form(3),
        model: str = Form(...),
        system_prompt_stage1: str = Form(""),
        system_prompt_stage2: str = Form(...),
        elevation_prompt: str = Form(""),
        detail_prompt: str = Form(...),
        # JSON-encoded list of {file_index, page_num, elevations: [{id, box}],
        # callouts: [{box}]} — one entry per page a human reviewed in stage
        # 1: whatever elevations it approved for that page (possibly none),
        # plus whatever elevation_callouts stage 1 already found there
        # (carried through untouched, no model call needed for them here).
        targets: str = Form(...),
        file_execution_mode: str = Form("sequential"),
        page_execution_mode: str = Form("sequential"),
        ground_truth: str = Form("[]"),   # JSON, same shape /api/grid/detect accepts
        iou_threshold: float = Form(0.5),
        # Whatever /api/two-stage/elevations reported for the stage 1 pass
        # that produced `targets`, handed back so the totals stored and
        # returned here cover BOTH stages rather than only the crops.
        stage1_usage: str = Form(""),
):
    """Stage 2: for every reviewed (file, page), crop each APPROVED
    elevation out of that page — RE-RENDERED from the original PDF at
    stage2_dpi, which is deliberately independent from whatever DPI stage 1
    used, since a close-up crop benefits from more detail than scanning a
    whole page for elevation frames needs. The 0-1000 elevation box from
    stage 1 maps onto this new render exactly the same way regardless of
    resolution, since it's a fraction of the page, not a pixel count. Every
    object returned from a crop is transformed from crop-relative back to
    full-page-relative 0-1000 coordinates before merging with the approved
    elevation boxes into one final result — same {"summary","objects"}
    shape as a normal /api/generate result entry, just spanning every
    reviewed page instead of one."""
    stage2_dpi = max(72, min(600, stage2_dpi))
    stage1_dpi = max(72, min(600, stage1_dpi))
    stage2_concurrency = max(1, min(20, stage2_concurrency))
    iou_threshold = max(0.0, min(1.0, iou_threshold))
    file_execution_mode = normalize_exec_mode(file_execution_mode)
    page_execution_mode = normalize_exec_mode(page_execution_mode)

    try:
        gt_items = extract_ground_truth_items(json.loads(ground_truth) if ground_truth else [])
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid ground_truth payload")

    # A malformed stage-1 usage blob should cost the run its combined totals,
    # not the whole detection result.
    stage1_records = []
    if stage1_usage:
        try:
            parsed = json.loads(stage1_usage)
            if isinstance(parsed, dict):
                parsed = parsed.get("requests_detail", [])
            if isinstance(parsed, list):
                stage1_records = [r for r in parsed if isinstance(r, dict)]
        except json.JSONDecodeError:
            print("[two-stage] ignoring malformed stage1_usage payload")

    try:
        target_list = json.loads(targets)
        if not isinstance(target_list, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid targets payload")

    targets_by_file: dict = {}
    for t in target_list:
        if not isinstance(t, dict):
            continue
        f_idx = t.get("file_index")
        page_num = t.get("page_num")
        if not isinstance(f_idx, int) or not isinstance(page_num, int):
            continue
        elevations = t.get("elevations")
        callouts = t.get("callouts")
        targets_by_file.setdefault(f_idx, []).append({
            "page_num": page_num,
            "elevations": elevations if isinstance(elevations, list) else [],
            "callouts": callouts if isinstance(callouts, list) else [],
        })

    # Configurable concurrency: run at most stage2_concurrency crop requests
    # (across every page and file in this request) at once, instead of
    # either strictly one-at-a-time or fully unbounded parallel — a batch
    # with many elevations across many pages shouldn't fire off dozens of
    # simultaneous requests just because it can.
    semaphore = asyncio.Semaphore(stage2_concurrency)

    usage_sink = []
    started = time.perf_counter()

    override_note = (
        'STAGE 2 OF 2 - DETAILS WITHIN ONE ELEVATION: this image is a '
        'CROPPED close-up of a single elevation drawing that a human has '
        'already reviewed and confirmed. Find and report ONLY cabinet and '
        'countertop objects visible within it - do not report "elevation" '
        'or "elevation_callout", since those were already handled in a '
        'separate, earlier pass.'
    )

    async def process_target_page(f_idx: int, file_name: str, pdf_bytes: bytes, target: dict):
        page_num = target["page_num"]
        approved = target["elevations"]
        callouts = target["callouts"]
        page_img = render_pdf_page(pdf_bytes, page_num, stage2_dpi)
        page_w, page_h = page_img.size

        async def process_one(elev):
            box = elev.get("box")
            if not (isinstance(box, list) and len(box) == 4
                    and all(isinstance(v, (int, float)) for v in box)):
                return []

            ex0, ey0, ex1, ey1 = box
            left = max(0, min(page_w - 1, round((ex0 / 1000) * page_w)))
            top = max(0, min(page_h - 1, round((ey0 / 1000) * page_h)))
            right = max(left + 1, min(page_w, round((ex1 / 1000) * page_w)))
            bottom = max(top + 1, min(page_h, round((ey1 / 1000) * page_h)))

            crop = page_img.crop((left, top, right, bottom))
            crop_w, crop_h = crop.size
            crop_b64 = image_to_b64(crop)

            try:
                async with semaphore:
                    crop_objects = await call_model_for_objects(
                        model, system_prompt_stage2, detail_prompt, crop_b64, crop_w, crop_h,
                        allowed_labels={"cabinet", "countertop"},
                        override_note=override_note,
                        usage_sink=usage_sink, stage="stage2",
                    )
            except Exception as e:
                print(f"[two-stage] details failed for {file_name} page {page_num} "
                      f"elevation {elev.get('id')}: {e}")
                return []

            # Map each crop-relative 0-1000 box back into the full page's own
            # 0-1000 space, using this elevation's own box as the linear window.
            e_width = ex1 - ex0
            e_height = ey1 - ey0
            transformed = []
            for obj in crop_objects:
                cx0, cy0, cx1, cy1 = obj["box"]
                transformed.append({
                    "label": obj["label"],
                    "box": [
                        max(0, min(1000, round(ex0 + (cx0 / 1000) * e_width))),
                        max(0, min(1000, round(ey0 + (cy0 / 1000) * e_height))),
                        max(0, min(1000, round(ex0 + (cx1 / 1000) * e_width))),
                        max(0, min(1000, round(ey0 + (cy1 / 1000) * e_height))),
                    ],
                    "file_index": f_idx,
                    "page_num": page_num,
                })
            return transformed

        per_elevation_results = await asyncio.gather(*[process_one(e) for e in approved])

        page_objects = [
            {"label": "elevation", "box": e["box"], "file_index": f_idx, "page_num": page_num}
            for e in approved
            if isinstance(e.get("box"), list) and len(e["box"]) == 4
        ]
        for r in per_elevation_results:
            page_objects.extend(r)
        # elevation_callout was already found in Stage 1's full-page scan —
        # carried through as-is rather than re-scanning the page here.
        page_objects.extend([
            {"label": "elevation_callout", "box": c["box"], "file_index": f_idx, "page_num": page_num}
            for c in callouts
            if isinstance(c.get("box"), list) and len(c["box"]) == 4
        ])

        return {
            "file_index": f_idx, "file_name": file_name, "page_num": page_num,
            "page_img": page_img, "objects": page_objects,
        }

    async def process_file(f_idx: int, file: UploadFile):
        file_targets = targets_by_file.get(f_idx, [])
        if not file_targets:
            return []
        pdf_bytes = await file.read()
        if page_execution_mode == "parallel":
            return await asyncio.gather(*[
                process_target_page(f_idx, file.filename, pdf_bytes, t) for t in file_targets
            ])
        results = []
        for t in file_targets:
            results.append(await process_target_page(f_idx, file.filename, pdf_bytes, t))
        return results

    if file_execution_mode == "parallel":
        per_file_results = await asyncio.gather(*[
            process_file(i, f) for i, f in enumerate(files)
        ])
    else:
        per_file_results = []
        for i, f in enumerate(files):
            per_file_results.append(await process_file(i, f))

    page_records = [rec for file_results in per_file_results for rec in file_results]

    final_objects = []
    for rec in page_records:
        final_objects.extend(rec["objects"])

    summary = {"cabinets": 0, "countertops": 0, "elevations": 0, "elevation_callouts": 0,
               "callouts": 0, "floor_plans": 0}
    for obj in final_objects:
        key = LABEL_TO_SUMMARY_KEY.get(obj.get("label"))
        if key:
            summary[key] += 1

    response_payload = {"summary": summary, "objects": final_objects}
    response_text = json.dumps(response_payload, ensure_ascii=False)

    # Totals span BOTH stages: stage 1's records were measured in the earlier
    # request and handed back in, stage 2's were measured just now.
    stage2_wall_ms = round((time.perf_counter() - started) * 1000)
    usage = summarize_usage(stage1_records + usage_sink)
    usage["wall_ms"] = stage2_wall_ms
    usage["by_stage"] = {
        "stage1": summarize_usage(stage1_records),
        "stage2": summarize_usage(usage_sink),
    }

    # page_records carries every page that was actually rendered in stage 2,
    # which is exactly the set of pages predictions can exist on — the same
    # points-based normalization the other flows use applies here too.
    multi_file = len(files) > 1
    page_points_dims = {}
    for rec in page_records:
        px_w, px_h = rec["page_img"].size
        page_points_dims[(rec["file_index"], rec["page_num"])] = (px_w * 72 / stage2_dpi,
                                                                  px_h * 72 / stage2_dpi)
    file_index_by_name = {f.filename: i for i, f in enumerate(files)}
    ground_truth_norm = normalize_ground_truth(gt_items, page_points_dims, multi_file,
                                                file_index_by_name)
    score_result = safe_score(
        predictions_from_objects(final_objects, multi_file),
        ground_truth_norm, iou_threshold,
    )

    try:
        save_two_stage_history(
            page_records=page_records, file_names=[f.filename for f in files],
            stage1_dpi=stage1_dpi, stage2_dpi=stage2_dpi, stage2_concurrency=stage2_concurrency,
            model=model, system_prompt_stage1=system_prompt_stage1, system_prompt_stage2=system_prompt_stage2,
            elevation_prompt=elevation_prompt, detail_prompt=detail_prompt,
            response_text=response_text,
            score=score_result, usage=usage,
            ground_truth=ground_truth_norm, iou_threshold=iou_threshold,
        )
        prune_history()
    except Exception as e:
        # History is a nice-to-have here — never let a save/prune failure
        # take down an otherwise-successful detection result.
        print(f"[two-stage] failed to save history: {e}")

    return {"model": model, "response": response_text, "score": score_result, "usage": usage}


def save_two_stage_history(page_records, file_names, stage1_dpi, stage2_dpi, stage2_concurrency,
                            model, system_prompt_stage1, system_prompt_stage2,
                            elevation_prompt, detail_prompt, response_text,
                            score=None, usage=None, ground_truth=None, iou_threshold=None):
    """Persists a completed two-stage run using the SAME history format and
    storage the regular /api/generate path uses (same meta.json shape, same
    images/ layout — including possibly-multiple pages/files), just tagged
    with run_type="two_stage" and a single result entry — so the existing
    History modal, list, detail view, and /api/history* endpoints all work
    for these runs with no separate UI. The image saved per page is the
    stage2_dpi render, since that's the one the final boxes are actually
    being drawn against."""
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = os.path.join(HISTORY_DIR, run_id)
    images_dir = os.path.join(run_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    pages_meta = []
    for rec in page_records:
        image_filename = f"{rec['file_index']}_{rec['page_num']}.png"
        rec["page_img"].save(os.path.join(images_dir, image_filename), format="PNG", optimize=True)
        pages_meta.append({
            "file_index": rec["file_index"],
            "file_name": rec["file_name"],
            "page_num": rec["page_num"],
            "image_url": f"/history-files/{run_id}/images/{image_filename}",
        })

    combined_prompt = (
        f"[Stage 1 — elevations]\n{elevation_prompt}\n\n"
        f"[Stage 2 — details within each elevation]\n{detail_prompt}"
    )

    meta = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_type": "two_stage",
        "system_prompt": system_prompt_stage1,  # generic field, kept for any older UI path that reads it
        "system_prompt_stage1": system_prompt_stage1,
        "system_prompt_stage2": system_prompt_stage2,
        "dpi": stage2_dpi,  # generic field, kept for any older UI path that reads it
        "stage1_dpi": stage1_dpi,
        "stage2_dpi": stage2_dpi,
        "stage2_concurrency": stage2_concurrency,
        "execution_settings": None,
        "iou_threshold": iou_threshold,
        "ground_truth": ground_truth or [],
        "files": file_names,
        "pages": pages_meta,
        "results": [{
            "model": model,
            "prompt": combined_prompt,
            "response": response_text,
            "counts": compute_counts(response_text),
            "score": score,
            "usage": usage,
            "expected_summary": None,
        }],
    }

    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ===========================================================================
# Grid-cell detection API
# ===========================================================================
# Single-PDF-only (unlike /api/generate, which accepts several): ground
# truth is entered per run, scoped to the one document being scored, so
# letting several unrelated PDFs share one run would make "which page is
# which" ambiguous between the ground truth the person pasted in and the
# pages actually sent. Every PAGE of that one PDF is still processed - in
# parallel or one after another, per page_execution_mode - same as the
# other flows' per-page axis.

async def call_model_for_grid_objects(model_name: str, system_prompt: str, user_prompt: str,
                                       image_b64: str, object_types: list,
                                       grid_rows: int, grid_cols: int,
                                       label_scheme: str = "alpha", box_precision: str = "cell",
                                       usage_sink: Optional[list] = None) -> list:
    """Sends ONE gridded page image to a model, asking it to name the grid
    cell(s) each requested object type occupies (see drafts/prompts/System
    Grid), then derives a 0-1 xyxy box for each one in code via
    cells_to_unit_box() — the model never estimates a numeric box itself.

    label_scheme controls how cells are named/drawn ("alpha" e.g. "C4",
    "numeric" e.g. "R4C3", "banded" = alpha labels + a zebra row tint to
    help count rows). box_precision="cell_anchor" additionally asks the
    model to name WHERE within its start/end cell the object's own corner
    actually sits (see ANCHOR_FRACTIONS) — sub-cell precision without a
    finer grid. Both are request-scoped instructions layered onto whatever
    system_prompt was configured, so the stored default prompt text never
    has to change to support them."""
    sample_row = min(1, grid_rows - 1)
    sample_col = min(1, grid_cols - 1)
    if label_scheme == "numeric":
        naming_note = (
            f'columns numbered C1 to C{grid_cols} left-to-right, rows numbered R1 to R{grid_rows} '
            f'top-to-bottom — name a cell by combining both, e.g. "{cell_label(sample_row, sample_col, label_scheme)}" '
            f'is row {sample_row + 1}, column {sample_col + 1}'
        )
    else:
        naming_note = (
            f'columns lettered A to {col_letters(grid_cols - 1)} left-to-right, rows numbered 1 to {grid_rows} '
            f'top-to-bottom (e.g. cell "{cell_label(sample_row, sample_col, label_scheme)}" is row {sample_row + 1}, '
            f'column {col_letters(sample_col)})'
        )
        if label_scheme == "banded":
            naming_note += ('. Every other row has a faint background tint purely to help you count rows '
                             'accurately — it has no meaning of its own')
    override_note = (
        f'This request uses a {grid_rows}x{grid_cols} reference grid — {naming_note}. Report ONLY these '
        f'object type(s) for this request: {", ".join(object_types)}. For each object found, report every '
        f'cell it occupies under "cells" — never a pixel, 0-1000, or 0-1 box.'
    )
    if box_precision == "cell_anchor":
        anchor_names = ", ".join(sorted(ANCHOR_FRACTIONS))
        override_note += (
            f' "cells" alone is NOT enough for this request. You are REQUIRED to also report "start_point" '
            f'and "end_point" on every object — this is a normal, required part of reporting each object, not '
            f'an optional extra. "start_point" is where the object\'s own top-left corner sits WITHIN its '
            f'top-left-most named cell; "end_point" is where its own bottom-right corner sits WITHIN its '
            f'bottom-right-most named cell. Each is one of these named positions: {anchor_names}. Most real '
            f'objects do NOT line up exactly with a grid line — before answering, look at how far across each '
            f'of those two cells the object\'s own edge actually falls, rather than defaulting to "topleft"/'
            f'"bottomright" out of habit; use those two ONLY when the object\'s own edge visibly touches that '
            f'exact cell edge. Reporting the same "topleft"/"bottomright" pair for every object is almost '
            f'certainly wrong and defeats the purpose of this request. Example object: '
            f'{{"object_type":"cabinet","cells":["C4"],"start_point":"left","end_point":"center"}}'
        )
    elif box_precision == "cell_fraction":
        override_note += (
            f' "cells" alone is NOT enough for this request. You are REQUIRED to also report "start_point" '
            f'and "end_point" on every object — this is a normal, required part of reporting each object, not '
            f'an optional extra. Each is a [fx, fy] array of two numbers from 0 to 1, giving a point\'s '
            f'position inside ONE cell (NOT the whole sheet): fx=0 is that cell\'s own left edge, fx=1 its own '
            f'right edge; fy=0 its own top edge, fy=1 its own bottom edge. "start_point" locates the object\'s '
            f'own top-left corner WITHIN its top-left-most named cell; "end_point" locates its own '
            f'bottom-right corner WITHIN its bottom-right-most named cell. This is the SAME kind of estimate '
            f'as placing a point on a 0-1 image, just rescaled to one small cell instead of the whole sheet, '
            f'so make an actual visual estimate — most real objects do NOT line up exactly with a grid line, '
            f'so [0,0]/[1,1] should be rare, only when the object\'s own edge visibly touches that exact cell '
            f'edge. Reporting [0,0]/[1,1] for every object is almost certainly wrong and defeats the purpose '
            f'of this request. Example object: '
            f'{{"object_type":"cabinet","cells":["C4"],"start_point":[0.1,0.4],"end_point":[0.9,0.7]}}'
        )
    final_system_instruction = system_prompt + f"""

    [SYSTEM OVERRIDE - CRITICAL]
    You have been provided with EXACTLY 1 image.
    {override_note}
    """

    reasoning_kind = model_reasoning_kind(model_name)
    extra_body = {}
    if reasoning_kind in ("mandatory", "optional"):
        extra_body["reasoning"] = {"effort": "low"}
    max_response_tokens = 16000 if reasoning_kind in ("mandatory", "optional") else 10000

    messages = [
        {"role": "system", "content": final_system_instruction},
        {"role": "user", "content": [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]}
    ]

    response = await timed_completion(model_name, messages, max_response_tokens,
                                       extra_body, usage_sink=usage_sink)

    if not response.choices:
        raise Exception("Model returned no choices")
    message = response.choices[0].message
    if message is None:
        raise Exception("Model returned empty message")
    response_text = message.content
    if not response_text:
        raise Exception(f"Model returned empty content. Finish reason: {response.choices[0].finish_reason}")

    response_text = response_text.replace("```json", "").replace("```", "").strip()
    try:
        parsed_json = json.loads(response_text)
    except json.JSONDecodeError:
        parsed_json = json.loads(repair_json(response_text))

    if isinstance(parsed_json, list):
        parsed_json = {"objects": parsed_json}
    elif not isinstance(parsed_json, dict):
        raise Exception(f"Unexpected top-level JSON type from model: {type(parsed_json).__name__}")

    raw_objects = parsed_json.get("objects", [])
    if not isinstance(raw_objects, list):
        raw_objects = []

    allowed = set(object_types)
    valid_objects = []
    for obj in raw_objects:
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("object_type") or obj.get("label")
        if obj_type not in allowed:
            continue
        cells = obj.get("cells")
        if isinstance(cells, str):
            cells = [cells]
        # "start_point"/"end_point" hold a named anchor string (cell_anchor
        # mode) or a raw [fx, fy] pair (cell_fraction mode) — resolve_point_
        # fraction() in cells_to_unit_box() accepts either shape regardless
        # of which mode was actually requested.
        start_point = obj.get("start_point") if box_precision != "cell" else None
        end_point = obj.get("end_point") if box_precision != "cell" else None
        box = cells_to_unit_box(cells, grid_rows, grid_cols, scheme=label_scheme,
                                 start_anchor=start_point, end_anchor=end_point)
        if box is None:
            continue
        valid_objects.append({"object_type": obj_type, "bbox": box})

    return valid_objects


@app.post("/api/grid/detect")
async def grid_detect(
        file: UploadFile = File(...),
        dpi: int = Form(200),
        max_dim: int = Form(None),
        grid_rows: int = Form(6),
        grid_cols: int = Form(6),
        grid_color: str = Form("#dc0000"),   # hex, e.g. "#dc0000" — grid line (and label) color
        grid_opacity: float = Form(0.6),     # 0-1, grid line opacity
        grid_thickness: int = Form(1),       # px, grid line width
        label_scheme: str = Form("alpha"),   # "alpha" ("C4"), "numeric" ("R4C3"), or "banded" (alpha + zebra rows)
        box_precision: str = Form("cell"),   # "cell" (full cell edges), "cell_anchor" (9-point) or "cell_fraction" (0-1 point)
        iou_threshold: float = Form(0.5),
        object_types: str = Form(...),      # JSON list of strings, e.g. ["cabinet","elevation"]
        models_data: str = Form(...),        # JSON list of {"model":..., "prompt":...} — same shape /api/generate uses
        system_prompt: str = Form(...),
        ground_truth: str = Form("[]"),      # JSON list of {"object_type":..., "page":..., "box":[...]}
        model_execution_mode: str = Form("sequential"),
        page_execution_mode: str = Form("sequential"),
):
    """Flow contract: input = (one sheet PDF, target object types) -> output
    = a list of {object_type, bbox} per page, scored against person-supplied
    ground truth with location-scorer. Renders every page of the ONE
    uploaded PDF, downscales each to fit the model, draws a labeled
    reference grid on top, and asks each configured model to name grid
    cells rather than compute coordinates directly (see
    call_model_for_grid_objects). Every model runs over every page of the
    same PDF; model_execution_mode/page_execution_mode control concurrency
    the same way they do for /api/generate."""
    dpi = max(72, min(600, dpi))
    max_dim = max(256, min(8092, max_dim))
    grid_rows = max(1, min(200, grid_rows))
    grid_cols = max(1, min(200, grid_cols))
    grid_rgb = hex_to_rgb(grid_color)
    grid_opacity = max(0.05, min(1.0, grid_opacity))
    grid_thickness = max(1, min(10, grid_thickness))
    label_scheme = label_scheme if label_scheme in LABEL_SCHEMES else "alpha"
    box_precision = box_precision if box_precision in BOX_PRECISIONS else "cell"
    iou_threshold = max(0.0, min(1.0, iou_threshold))
    model_execution_mode = normalize_exec_mode(model_execution_mode)
    page_execution_mode = normalize_exec_mode(page_execution_mode)

    try:
        target_types = json.loads(object_types)
        if not isinstance(target_types, list) or not target_types:
            raise ValueError
        target_types = [str(t) for t in target_types]
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid object_types payload")

    try:
        models = json.loads(models_data)
        if not isinstance(models, list) or not models:
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid models_data payload")

    try:
        gt_raw = json.loads(ground_truth) if ground_truth else []
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid ground_truth payload")
    gt_items = extract_ground_truth_items(gt_raw)

    pdf_bytes = await file.read()
    file_name = file.filename
    images = convert_from_bytes(pdf_bytes, dpi=dpi, fmt="png")
    if not images:
        raise HTTPException(status_code=400, detail="No pages were found in the uploaded PDF.")

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = os.path.join(HISTORY_DIR, run_id)
    images_dir = os.path.join(run_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # Render + downscale-to-fit + draw the grid ONCE per page, shared across
    # every model — and save that exact gridded image, so history shows
    # precisely what each model was shown, same as every other flow here.
    pages_meta = []
    page_payloads = []  # (page_num, image_b64, px_w, px_h) — px_w/px_h are the ORIGINAL render's pixel size
    page_grid_geom = {}  # page_num -> geom, for mapping display boxes onto the saved (margin-included) image
    for idx, img in enumerate(images):
        page_num = idx + 1
        px_w, px_h = img.size
        gridded, geom = draw_grid_overlay(resize_to_fit(img, max_dim), grid_rows, grid_cols,
                                           line_color=grid_rgb, line_opacity=grid_opacity,
                                           line_width=grid_thickness, label_scheme=label_scheme)
        page_grid_geom[page_num] = geom
        image_filename = f"0_{page_num}.png"
        gridded.save(os.path.join(images_dir, image_filename), format="PNG", optimize=True)
        pages_meta.append({
            "file_index": 0, "file_name": file_name, "page_num": page_num,
            "image_url": f"/history-files/{run_id}/images/{image_filename}",
        })
        page_payloads.append((page_num, image_to_b64(gridded), px_w, px_h))

    # Ground truth: normalized to 0-1 against each page's size in PDF POINTS
    # rather than its rendered pixels — see normalize_ground_truth(), which
    # every flow here shares. This endpoint always scores exactly one PDF, so
    # every page belongs to file 0 and plain page numbers stay unique.
    page_points_dims = {
        (0, p_num): (px_w * 72 / dpi, px_h * 72 / dpi)
        for (p_num, _b64, px_w, px_h) in page_payloads
    }
    ground_truth_norm = normalize_ground_truth(gt_items, page_points_dims, multi_file=False)

    async def run_page(model_name: str, user_prompt: str, page_num: int, image_b64: str,
                       usage_sink: list):
        try:
            objects = await call_model_for_grid_objects(
                model_name, system_prompt, user_prompt, image_b64,
                target_types, grid_rows, grid_cols,
                label_scheme=label_scheme, box_precision=box_precision,
                usage_sink=usage_sink,
            )
            return {"ok": True, "page": page_num, "objects": objects}
        except Exception as e:
            print(f"[grid] Error from model {model_name} on page {page_num}: {e}")
            return {"ok": False, "page": page_num, "error": str(e)}

    async def run_model(item: dict):
        model_name = item["model"]
        user_prompt = item["prompt"]
        usage_sink = []
        model_started = time.perf_counter()

        if page_execution_mode == "parallel":
            page_results = await asyncio.gather(*[
                run_page(model_name, user_prompt, p_num, b64, usage_sink)
                for (p_num, b64, _w, _h) in page_payloads
            ])
        else:
            page_results = []
            for (p_num, b64, _w, _h) in page_payloads:
                page_results.append(await run_page(model_name, user_prompt, p_num, b64, usage_sink))

        predictions = []
        errors = []
        for r in page_results:
            if r["ok"]:
                for obj in r["objects"]:
                    predictions.append({"object_type": obj["object_type"], "bbox": obj["bbox"], "page": r["page"]})
            else:
                errors.append(f"page {r['page']}: {r['error']}")

        usage = summarize_usage(usage_sink)
        usage["wall_ms"] = round((time.perf_counter() - model_started) * 1000)

        if errors and not predictions and len(errors) == len(page_results):
            return {
                "model": model_name,
                "response": json.dumps({"error": "; ".join(errors)}, ensure_ascii=False),
                "score": None,
                "usage": usage,
            }

        score_result = safe_score(predictions, ground_truth_norm, iou_threshold)

        summary = {"cabinets": 0, "countertops": 0, "elevations": 0, "elevation_callouts": 0,
                   "callouts": 0, "floor_plans": 0}
        objects_out = []
        for p in predictions:
            key = LABEL_TO_SUMMARY_KEY.get(p["object_type"])
            if key:
                summary[key] += 1
            # p["bbox"] is content-only fractions (what scoring against
            # ground truth needs) — the saved/displayed image includes the
            # header margin, so the DISPLAYED box needs the extra mapping
            # or it lands shifted/shrunk relative to the grid lines it's
            # supposed to align with (see content_box_to_canvas_frac()).
            geom = page_grid_geom.get(p["page"])
            display_box = content_box_to_canvas_frac(p["bbox"], geom) if geom else p["bbox"]
            objects_out.append({
                "label": p["object_type"],
                "box": display_box,
                "file_index": 0,
                "page_num": p["page"],
            })

        final_json = {"summary": summary, "objects": objects_out}
        if errors:
            final_json["errors"] = errors

        return {
            "model": model_name,
            "response": json.dumps(final_json, ensure_ascii=False),
            "score": score_result,
            "usage": usage,
        }

    if model_execution_mode == "parallel":
        results = await asyncio.gather(*[run_model(item) for item in models])
    else:
        results = []
        for item in models:
            results.append(await run_model(item))

    results = list(results)

    save_grid_history(
        run_id=run_id, run_dir=run_dir, system_prompt=system_prompt,
        dpi=dpi, max_dim=max_dim, grid_rows=grid_rows, grid_cols=grid_cols,
        grid_color="#%02x%02x%02x" % grid_rgb, grid_opacity=grid_opacity, grid_thickness=grid_thickness,
        label_scheme=label_scheme, box_precision=box_precision,
        iou_threshold=iou_threshold, object_types=target_types,
        file_name=file_name, pages_meta=pages_meta, models=models, results=results,
        ground_truth=ground_truth_norm,
        model_execution_mode=model_execution_mode, page_execution_mode=page_execution_mode,
    )
    prune_history()

    return {
        "results": [
            {"model": r["model"], "response": r["response"], "score": r["score"], "usage": r.get("usage")}
            for r in results
        ],
        "run_id": run_id,
    }


def save_grid_history(run_id, run_dir, system_prompt, dpi, max_dim, grid_rows, grid_cols,
                       grid_color, grid_opacity, grid_thickness, label_scheme, box_precision, iou_threshold,
                       object_types, file_name, pages_meta, models, results, ground_truth,
                       model_execution_mode, page_execution_mode):
    """Persists a completed grid-detection run using the same history shape
    every other flow uses (meta.json + images/), tagged run_type="grid" and
    coord_scale="unit" — every box saved here is a 0-1 fraction (the
    location-scorer shared contract), not the 0-1000 scale every other flow
    in this file uses. The frontend's shared history-detail drawing code
    checks that field and converts on load rather than this flow having to
    fake a 0-1000 value just to reuse that drawing path."""
    results_by_model = {r["model"]: r for r in results}

    meta = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_type": "grid",
        "coord_scale": "unit",
        "system_prompt": system_prompt,
        "dpi": dpi,
        "max_dim": max_dim,
        "grid_rows": grid_rows,
        "grid_cols": grid_cols,
        "grid_color": grid_color,
        "grid_opacity": grid_opacity,
        "grid_thickness": grid_thickness,
        "label_scheme": label_scheme,
        "box_precision": box_precision,
        "iou_threshold": iou_threshold,
        "object_types": object_types,
        "execution_settings": {
            "model_execution_mode": model_execution_mode,
            "page_execution_mode": page_execution_mode,
        },
        "files": [file_name],
        "pages": pages_meta,
        "ground_truth": ground_truth,
        "results": [
            {
                "model": item["model"],
                "prompt": item["prompt"],
                "response": results_by_model.get(item["model"], {}).get("response", ""),
                "counts": compute_counts(results_by_model.get(item["model"], {}).get("response", "")),
                "score": results_by_model.get(item["model"], {}).get("score"),
                "usage": results_by_model.get(item["model"], {}).get("usage"),
                "expected_summary": None,
            }
            for item in models
        ],
    }

    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)