"""Overlapping-tile ("Cutting" mode) detection.

Instead of sending one whole-page image to the model, the page is split into
a grid_rows x grid_cols grid of overlapping crops ("tiles"), each large
enough on its own for the model to see small objects (thin countertop
profiles, small elevation-callout circles) in more detail than the whole
page allows at a sane token budget. All tiles are sent as separate image
blocks in ONE combined LLM request (see llm_client.OpenRouterProvider.
analyze_tiles), each tagged with a `tile_id` the model is asked to echo back
per detection, so this module can remap every detection from its tile's
local coordinate space back to the full page image's, then merge detections
of the same object seen from two overlapping tiles into one box.

This is deliberately self-contained: analyze_page_tiled is the only entry
point analyze.py needs, and it returns a TiledAnalysisResult carrying an
ordinary ParsedResult (remapped + merged, full-image 0-1000-normalized
coordinates) -- everything downstream of analyze.py (BBoxCanvas.jsx,
ResultsSummary.jsx, parser.build_location_export) stays completely unaware
that tiling ever happened.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from app.models.schemas import DetectedObject, ParsedResult, ReferenceImage
from app.services.crop_utils import CROP_MAX_EDGE, crop_and_resize, remap_normalized_box
from app.services.parser import LABEL_TO_CATEGORY, parse_model_response

if TYPE_CHECKING:
    from app.services.llm_client import LLMProvider

# Re-exported for backward-compatible naming within this module's own
# comments/docstrings -- the actual constant (and the crop/resize logic
# it bounds) now lives in crop_utils.py, shared with AI-crop mode
# (services/ai_crop.py). See crop_utils.CROP_MAX_EDGE for the full
# reasoning comment.
TILE_MAX_EDGE = CROP_MAX_EDGE

# Two detections (from different tiles, same label) whose full-image boxes
# have at least this much IoU are treated as the same physical object and
# merged (see merge_overlapping_detections). 0.5 is a conservative middle
# ground: low enough to catch an object genuinely split near-50/50 across a
# tile boundary (the case compute_tile_grid's overlap expansion is meant to
# avoid, but doesn't eliminate for an object sitting exactly on a doubled
# boundary), high enough that two merely-adjacent, same-label objects close
# together (e.g. two neighboring cabinets) aren't wrongly collapsed.
DEFAULT_IOU_THRESHOLD = 0.5

# Rough per-tile output-token budget, used only as the reasoning behind the
# frontend's "max_tokens looks low" warning (App.jsx's own copy of this
# estimate) -- NOT enforced anywhere on the backend. ~(label + 4 coords +
# tile_id + confidence) at roughly 20-35 tokens/object (see config.yaml's
# own max_tokens comment) times a handful of objects per tile, plus fixed
# JSON scaffolding. Deliberately conservative (a floor, not a real cap).
TOKENS_PER_TILE_ESTIMATE = 300

# Appended to the user prompt only for the duration of a tiled request --
# never written back to prompts/*.txt or data_dir/custom_user_prompt.txt.
# Mirrors how Multi-Prompting mode's per-category prompts are also
# request-scoped and never persisted (see App.jsx's categoryPrompts).
TILE_ID_INSTRUCTION = (
    "\n\nThis image has been split into multiple overlapping tiles, each "
    'preceded by a caption like "Tile r0c1 (row 0, col 1)". For every '
    'detection in your "bounding_box" array, include an additional '
    '"tile_id" field (e.g. "r0c1") giving the exact tile you found that '
    "object in. Report coordinates in that tile's own image only -- not "
    "the original page. This field is required for every detection."
)


@dataclass
class TileMeta:
    tile_id: str  # e.g. "r0c1" -- also embedded in the prompt caption text
    row: int
    col: int
    # Crop box in FULL-IMAGE pixel coords, BEFORE any resize -- the
    # coordinate space tile-local detections get remapped back into.
    x0: int
    y0: int
    x1: int
    y1: int
    # Resize scale factor applied when building the tile image actually sent
    # to the model (1.0 if the crop's long edge was already <= TILE_MAX_EDGE).
    # Kept for diagnostics -- see remap_tile_detections's note on why this
    # doesn't actually need to appear in the remap formula.
    scale: float = 1.0


@dataclass
class TiledAnalysisResult:
    """Mirrors what analyze.py's non-cutting branch already destructures
    from AnalysisResult + parse_model_response, so analyze_one's two
    branches can converge on identical PageResult-building code."""

    raw_text: str
    parsed: ParsedResult | None
    parse_error: bool
    truncated: bool
    reasoning_tokens: int | None
    completion_tokens: int | None


def compute_tile_grid(
    width: int, height: int, rows: int, cols: int, overlap_pct: float
) -> list[TileMeta]:
    """Compute a rows x cols grid over a width x height image, expanding
    each cell by overlap_pct of that cell's own width/height on every side
    (clamped to image bounds), so adjacent tiles share a border strip of
    that size -- an object that would otherwise straddle exactly one grid
    line ends up fully contained in at least one tile's crop, which is what
    lets merge_overlapping_detections drop (not need to reconstruct) a
    near-boundary duplicate at merge time.

    tile_id format: "r{row}c{col}", 0-indexed -- embedded both in
    TileMeta.tile_id and in the per-tile caption text the model sees (see
    build_tile_content_and_prompt), so DetectedObject.tile_id values
    round-trip against this list by string equality.
    """
    cell_w = width / cols
    cell_h = height / rows
    pad_x = cell_w * overlap_pct
    pad_y = cell_h * overlap_pct

    tiles: list[TileMeta] = []
    for row in range(rows):
        for col in range(cols):
            bx0, bx1 = col * cell_w, (col + 1) * cell_w
            by0, by1 = row * cell_h, (row + 1) * cell_h
            x0 = max(0, round(bx0 - pad_x))
            y0 = max(0, round(by0 - pad_y))
            x1 = min(width, round(bx1 + pad_x))
            y1 = min(height, round(by1 + pad_y))
            # Defensive only -- shouldn't trigger given overlap_pct <= 0.5
            # and rows/cols >= 1, but guards against a degenerate 0-size tile.
            x1 = max(x1, x0 + 1)
            y1 = max(y1, y0 + 1)
            tiles.append(
                TileMeta(
                    tile_id=f"r{row}c{col}", row=row, col=col, x0=x0, y0=y0, x1=x1, y1=y1
                )
            )
    return tiles


def crop_and_resize_tile(image: Image.Image, tile: TileMeta) -> tuple[bytes, TileMeta]:
    """Crop tile's box out of the full-resolution image, downscale it
    (preserving aspect ratio) if its long edge exceeds TILE_MAX_EDGE, and
    return (PNG bytes, tile-with-scale-filled-in). Thin wrapper over
    crop_utils.crop_and_resize (shared with AI-crop mode) -- see that
    function for the resize behavior itself.
    """
    png_bytes, scale = crop_and_resize(image, (tile.x0, tile.y0, tile.x1, tile.y1))
    return png_bytes, replace(tile, scale=scale)


def augment_prompt_for_tiling(user_prompt: str) -> str:
    """Append the tile_id-output instruction to user_prompt for this request
    only. Never writes to prompts/*.txt or a saved
    data_dir/custom_user_prompt.txt."""
    return user_prompt + TILE_ID_INSTRUCTION


def _tally_counts(objects: list[DetectedObject]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for obj in objects:
        category = LABEL_TO_CATEGORY.get(obj.label, obj.label)
        tally[category] = tally.get(category, 0) + 1
    return tally


def remap_tile_detections(
    parsed: ParsedResult, tiles_by_id: dict[str, TileMeta], full_width: int, full_height: int
) -> ParsedResult:
    """Convert each detection's 0-1000-normalized, tile-local coordinates
    into 0-1000-normalized coordinates relative to the FULL page image -- the
    convention every downstream consumer (BBoxCanvas.jsx, ResultsSummary.jsx,
    parser.build_location_export) already expects, with zero awareness of
    tiles.

    Delegates the actual per-detection math to crop_utils.remap_normalized_box
    (shared with AI-crop mode) -- see that function for why the resize scale
    factor doesn't need to appear here at all.

    A detection whose tile_id doesn't match any known tile (hallucinated, or
    missing) is dropped rather than guessed at -- there's no coordinate
    space to interpret it against, the same philosophy as
    DetectedObject's 0-1000 Field bounds: an unrecoverable value is dropped,
    not silently misplaced.
    """
    remapped: list[DetectedObject] = []
    for obj in parsed.objects:
        tile = tiles_by_id.get(obj.tile_id) if obj.tile_id else None
        if tile is None:
            continue

        x_min, y_min, x_max, y_max = remap_normalized_box(
            obj.x_min, obj.y_min, obj.x_max, obj.y_max,
            tile.x0, tile.y0, tile.x1, tile.y1,
            full_width, full_height,
        )

        remapped.append(
            DetectedObject(
                label=obj.label,
                x_min=x_min,
                y_min=y_min,
                x_max=x_max,
                y_max=y_max,
                confidence=obj.confidence,
                tile_id=obj.tile_id,
            )
        )

    return ParsedResult(objects=remapped, counts=_tally_counts(remapped))


def _iou(a: DetectedObject, b: DetectedObject) -> float:
    x_min = max(a.x_min, b.x_min)
    y_min = max(a.y_min, b.y_min)
    x_max = min(a.x_max, b.x_max)
    y_max = min(a.y_max, b.y_max)
    if x_max <= x_min or y_max <= y_min:
        return 0.0
    intersection = (x_max - x_min) * (y_max - y_min)
    area_a = (a.x_max - a.x_min) * (a.y_max - a.y_min)
    area_b = (b.x_max - b.x_min) * (b.y_max - b.y_min)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _union_box(a: DetectedObject, b: DetectedObject) -> DetectedObject:
    return DetectedObject(
        label=a.label,
        x_min=min(a.x_min, b.x_min),
        y_min=min(a.y_min, b.y_min),
        x_max=max(a.x_max, b.x_max),
        y_max=max(a.y_max, b.y_max),
        confidence=(
            max(a.confidence, b.confidence)
            if a.confidence is not None and b.confidence is not None
            else (a.confidence if a.confidence is not None else b.confidence)
        ),
        tile_id=None,  # a merged box no longer belongs to one tile
    )


def merge_overlapping_detections(
    objects: list[DetectedObject], iou_threshold: float = DEFAULT_IOU_THRESHOLD
) -> list[DetectedObject]:
    """Collapse detections (already remapped to full-image 0-1000 coords,
    see remap_tile_detections) that are almost certainly the same physical
    object seen from two overlapping tiles, into one.

    Grouped by label first (a cabinet and a countertop overlapping in the
    same region are never the same object regardless of IoU) -- within each
    label group, any two detections whose boxes have IoU >= iou_threshold
    are merged: the merged box is the UNION (not intersection -- either
    tile's crop may have clipped part of the true object at its own edge,
    and the other tile's overlap region is exactly what fills that in), and
    the merged confidence is the higher of the two. Runs pairwise,
    iteratively, until no remaining pair in a label group exceeds the
    threshold -- deliberately simple O(n^2) (tile counts and per-tile
    detection counts are both small -- at most 6x6=36 tiles, a handful of
    objects each -- nowhere near a performance concern) rather than a
    spatial index.
    """
    by_label: dict[str, list[DetectedObject]] = {}
    for obj in objects:
        by_label.setdefault(obj.label, []).append(obj)

    merged: list[DetectedObject] = []
    for group in by_label.values():
        changed = True
        while changed:
            changed = False
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    if _iou(group[i], group[j]) >= iou_threshold:
                        combined = _union_box(group[i], group[j])
                        group = [
                            obj for k, obj in enumerate(group) if k != i and k != j
                        ] + [combined]
                        changed = True
                        break
                if changed:
                    break
        merged.extend(group)
    return merged


def build_tile_content_and_prompt(
    image: Image.Image, tiles: list[TileMeta], user_prompt: str
) -> tuple[list[tuple[bytes, str]], str, dict[str, TileMeta]]:
    """Crop/resize every tile out of `image`, pair each with its caption
    text, and return (tile_content, augmented_user_prompt, tiles_by_id) --
    tiles_by_id carries the FINALIZED TileMeta (scale filled in) needed by
    remap_tile_detections after the model responds."""
    tile_content: list[tuple[bytes, str]] = []
    tiles_by_id: dict[str, TileMeta] = {}
    for tile in tiles:
        png_bytes, finalized = crop_and_resize_tile(image, tile)
        caption = f"Tile {tile.tile_id} (row {tile.row}, col {tile.col})"
        tile_content.append((png_bytes, caption))
        tiles_by_id[finalized.tile_id] = finalized
    return tile_content, augment_prompt_for_tiling(user_prompt), tiles_by_id


async def analyze_page_tiled(
    *,
    provider: "LLMProvider",
    image_path: Path,
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    grid_rows: int,
    grid_cols: int,
    overlap_pct: float,
    reference_images: list[ReferenceImage] | None = None,
) -> TiledAnalysisResult:
    """Cutting-mode replacement for the non-cutting path's downscale_image ->
    provider.analyze_image -> parse_model_response. Must be called with
    `image_path` = page.image_path (the full-resolution original), NOT
    page.display_image_path (a browser-decode-safe downscaled copy) --
    crop boxes are recorded in the same pixel space page.width/height
    describes, which build_location_export and the frontend canvas already
    depend on.
    """
    with Image.open(image_path) as image:
        image.load()
        full_width, full_height = image.size
        tiles = compute_tile_grid(full_width, full_height, grid_rows, grid_cols, overlap_pct)
        tile_content, augmented_prompt, tiles_by_id = build_tile_content_and_prompt(
            image, tiles, user_prompt
        )

    analysis = await provider.analyze_tiles(
        tiles=tile_content,
        media_type="image/png",
        system_prompt=system_prompt,
        user_prompt=augmented_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reference_images=reference_images,
    )

    parsed, parse_error = parse_model_response(analysis.text)
    if parsed is not None:
        remapped = remap_tile_detections(parsed, tiles_by_id, full_width, full_height)
        merged_objects = merge_overlapping_detections(remapped.objects)
        parsed = ParsedResult(objects=merged_objects, counts=_tally_counts(merged_objects))

    return TiledAnalysisResult(
        raw_text=analysis.text,
        parsed=parsed,
        parse_error=parse_error,
        truncated=analysis.truncated,
        reasoning_tokens=analysis.reasoning_tokens,
        completion_tokens=analysis.completion_tokens,
    )
