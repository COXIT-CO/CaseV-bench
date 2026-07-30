"""AI-crop mode: two-pass region-localization detection.

Instead of sending one whole-page image straight to the object-detection
model (the non-cutting path), or splitting it into a uniform overlapping
grid (Cutting mode, services/tiling.py), AI-crop asks the model to first
find the page's own tagged regions, then runs the real object-detection
task only against those regions:

  Pass 1: send the whole page (downscaled the same way the non-cutting path
  already does) with a FIXED, internal cell-detection prompt
  (Settings.ai_crop_cell_prompt_text -- prompts/elevations_unfiltered.txt by
  default) asking the model to bound every tagged grid cell and extract its
  detail-tag text. This is independent of whatever system_prompt/user_prompt
  the request actually carries for its real object-detection task -- that's
  Pass 2's job.

  Crop: every cell Pass 1 returns (no content-based filtering -- see the
  crop prompt's own "No Shape Filtering" instruction, which explicitly
  asks the model to include floor plan cells too) has its box converted to
  full-image pixels, padded by a small fixed percentage (clip-avoidance,
  not overlap -- unlike Cutting's deliberately-overlapping grid, these are
  distinct sheet regions), then cropped/resized via the same
  crop_utils.crop_and_resize Cutting mode uses. An earlier version of this
  function also dropped any cell whose extracted tag text contained "floor
  plan" here, in our own code -- that ran unconditionally regardless of
  what the (user-editable) crop prompt actually asked the model to return,
  so it silently contradicted a crop prompt that explicitly asked for
  floor plans to be included. Removed; filtering, if ever wanted again,
  belongs in the prompt the user controls, not a hardcoded backend check.

  If Pass 1 finds zero cells at all, we stop here -- Pass 2 never runs, and
  the caller gets an informative empty result instead of an error. If
  Pass 1's own response was truncated (hit max_tokens), that's surfaced
  too (see pass1_truncated_note in analyze_page_ai_crop) instead of
  silently returning fewer cells than are actually on the page with no
  indication why.

  Pass 2: all surviving crops go into ONE combined LLM request (reusing
  llm_client.OpenRouterProvider.analyze_tiles as-is -- the "N images, each
  with its own caption, ending in one prompt" shape is identical to
  Cutting's), using the request's REAL system_prompt/user_prompt (with an
  instruction appended overriding the response shape -- see
  PASS2_INSTRUCTION), since this pass is the actual object-detection task
  the user configured. Detections stay in each crop's own 0-1000-normalized
  coordinate space -- there used to be a step here converting them back to
  full-page coordinates, but that remap was found to be unreliable in
  practice, so it's been removed entirely. The frontend now shows each
  crop's detections drawn directly on that crop (CropsCarousel.jsx)
  instead of overlaid on the full page.

  Because there's no shared full-page coordinate space to merge into
  anymore, Pass 2's response is instead one entry per image sent (in the
  same order), each echoing back the cell_id it was captioned with -- this
  makes a misalignment a checkable, surfaced signal (CropInfo.verified)
  instead of a silent one. See _build_verified_crops for the exact
  verification rules.

This is deliberately self-contained: analyze_page_ai_crop is the only entry
point analyze.py needs, and it returns an AiCropResult carrying an ordinary
(always-empty-objects) ParsedResult -- see CropInfo/ParsedResult.counts for
why -- plus a `crops` array (base64-encoded image + crop-local detections +
verified flag, for the frontend's crops carousel) -- everything downstream
of analyze.py stays completely unaware that any of this happened.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from json_repair import repair_json
from PIL import Image

from app.models.schemas import CropInfo, DetectedObject, ParsedResult, ReferenceImage
from app.services.crop_utils import crop_and_resize
from app.services.image_utils import downscale_image
from app.services.parser import (
    LABEL_TO_CATEGORY,
    parse_model_response,
    strip_code_fences,
    validate_detection,
)

if TYPE_CHECKING:
    from app.services.llm_client import LLMProvider

# Clip-avoidance only, not overlap-for-continuity like Cutting's grid --
# these crops are distinct, non-overlapping sheet regions Pass 1 already
# located, so a small pad just keeps content near the cell's own boundary
# (e.g. the detail-tag circle itself) from being clipped.
CROP_PADDING_PCT = 0.02

# Appended to the request's real user_prompt for Pass 2 only -- never
# written back to prompts/*.txt or data_dir/custom_user_prompt.txt (same
# request-scoped-only convention as tiling.TILE_ID_INSTRUCTION). This
# deliberately tells the model to ignore the system prompt's earlier
# "bounding_box" envelope description for THIS response -- the system
# prompt is shared with every other call (Pass 1, Cutting, non-cutting)
# and describes a schema that doesn't match what Pass 2 needs here, so the
# override has to happen in the appended instruction, not by editing the
# shared system prompt file.
PASS2_INSTRUCTION = (
    "\n\nYou will be shown multiple separate images in this message, each "
    'immediately preceded by a caption of the exact form "IMAGE {n} — '
    'cell_id: {cell_id}" (e.g. "IMAGE 3 — cell_id: cell2"). Analyze each '
    "image independently for the objects described above.\n\n"
    "IMPORTANT: for this response, ignore the JSON schema described "
    "earlier (the \"bounding_box\" object) -- it does not apply here. "
    "Instead, respond with a single top-level JSON ARRAY with EXACTLY one "
    "entry per image you were shown, in the SAME ORDER the images "
    "appeared, of this exact shape:\n"
    "[\n"
    "  {\n"
    '    "cell_id": "<the cell_id from that image\'s caption, echoed exactly>",\n'
    '    "detections": [\n'
    '      { "label": "...", "bbox": { "x_min": <int>, "y_min": <int>, '
    '"x_max": <int>, "y_max": <int> }, "confidence": <float> },\n'
    "      ...\n"
    "    ]\n"
    "  },\n"
    "  ...\n"
    "]\n"
    "bbox coordinates are normalized 0-1000 relative to THAT image only "
    "(not the original page), origin top-left. Every image must get its "
    "own array entry, even if its detections array is empty -- do not "
    "omit an image just because you found nothing in it."
)


@dataclass
class CellCrop:
    cell_id: str  # e.g. "cell0" -- sequential, assigned by our own code
    tag_text: str


@dataclass
class AiCropResult:
    """Mirrors tiling.TiledAnalysisResult's shape, plus the two fields only
    AI-crop mode produces (crops, no_cells_message)."""

    raw_text: str | None
    parsed: ParsedResult | None
    parse_error: bool
    truncated: bool
    reasoning_tokens: int | None
    completion_tokens: int | None
    crops: list[CropInfo]
    no_cells_message: str | None


def _tally_counts(objects: list[DetectedObject]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for obj in objects:
        category = LABEL_TO_CATEGORY.get(obj.label, obj.label)
        tally[category] = tally.get(category, 0) + 1
    return tally


def _empty_result(message: str) -> AiCropResult:
    return AiCropResult(
        raw_text=None,
        parsed=ParsedResult(objects=[], counts={}),
        parse_error=False,
        truncated=False,
        reasoning_tokens=None,
        completion_tokens=None,
        crops=[],
        no_cells_message=message,
    )


def _crop_box_for_cell(obj: DetectedObject, full_width: int, full_height: int) -> tuple[int, int, int, int]:
    """Convert a Pass-1 cell's 0-1000-normalized box to full-image pixels,
    then pad it by CROP_PADDING_PCT of its own size on every side (clamped
    to image bounds)."""
    px_x0 = (obj.x_min / 1000) * full_width
    px_y0 = (obj.y_min / 1000) * full_height
    px_x1 = (obj.x_max / 1000) * full_width
    px_y1 = (obj.y_max / 1000) * full_height

    pad_x = (px_x1 - px_x0) * CROP_PADDING_PCT
    pad_y = (px_y1 - px_y0) * CROP_PADDING_PCT

    x0 = max(0, round(px_x0 - pad_x))
    y0 = max(0, round(px_y0 - pad_y))
    x1 = min(full_width, round(px_x1 + pad_x))
    y1 = min(full_height, round(px_y1 + pad_y))
    x1 = max(x1, x0 + 1)
    y1 = max(y1, y0 + 1)
    return x0, y0, x1, y1


def parse_pass2_response(raw_text: str) -> list | None:
    """Parse Pass 2's raw text into a top-level list -- one entry expected
    per crop sent, per PASS2_INSTRUCTION -- or None if it isn't valid JSON
    at all, or isn't a list once parsed. Same two-step resilience
    (strip_code_fences, then a json_repair fallback if strict parsing
    fails) parser.parse_model_response already uses, but this response's
    top-level shape (a bare array of {cell_id, detections}) is different
    enough from parse_model_response's ("bounding_box" envelope or a bare
    array of flat detections) that it needs its own top-level parse here --
    per-detection validation within each entry still goes through
    parser.validate_detection, so that resilience isn't duplicated.
    """
    cleaned = strip_code_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            data = repair_json(cleaned, return_objects=True)
        except Exception:
            return None
    return data if isinstance(data, list) else None


def _build_verified_crops(
    cell_crops: list[CellCrop],
    crop_bytes_by_id: dict[str, bytes],
    pass2_entries: list | None,
) -> tuple[list[CropInfo], str | None]:
    """Build the final CropInfo list, verifying Pass 2's response against
    what was actually sent to it.

    Two independent checks, both from the feature's own design (see
    CropInfo.verified's docstring):
      1. Whole-response check: if the number of entries Pass 2 returned
         doesn't match the number of crops sent, the whole response is
         unreliable -- every crop is marked unverified, and a top-level
         message explains the mismatch. This does NOT discard whatever
         detections did come back: crops are still matched to entries by
         position (up to the shorter length) on a best-effort basis, since
         a model that got most crops right shouldn't have that data thrown
         away just because the total count was off by one.
      2. Per-entry check: even when the counts line up, an individual
         entry's echoed cell_id might not match the cell_id expected at
         that position (the model attributed detections to the wrong
         image) -- that specific crop is marked unverified, but its
         detections are still kept, exactly like the whole-response case.

    A crop is verified True only when both checks pass for it.
    """
    count_mismatch = pass2_entries is None or len(pass2_entries) != len(cell_crops)
    mismatch_message = None
    if count_mismatch:
        got = len(pass2_entries) if pass2_entries is not None else 0
        mismatch_message = (
            f"Pass 2 returned {got} result(s) for {len(cell_crops)} crop(s) sent -- "
            "the counts didn't match, so every crop below is unverified "
            "(detections shown are best-effort, matched by position)."
        )

    crops: list[CropInfo] = []
    for i, cell in enumerate(cell_crops):
        entry = pass2_entries[i] if pass2_entries is not None and i < len(pass2_entries) else None
        detections: list[DetectedObject] = []
        verified = False
        if isinstance(entry, dict):
            cell_id_matches = entry.get("cell_id") == cell.cell_id
            verified = cell_id_matches and not count_mismatch
            raw_detections = entry.get("detections")
            if isinstance(raw_detections, list):
                for item in raw_detections:
                    obj = validate_detection(item)
                    if obj is not None:
                        detections.append(obj)
        crops.append(
            CropInfo(
                cell_id=cell.cell_id,
                tag_text=cell.tag_text,
                media_type="image/png",
                image_base64=base64.standard_b64encode(crop_bytes_by_id[cell.cell_id]).decode("utf-8"),
                detections=detections,
                verified=verified,
            )
        )
    return crops, mismatch_message


async def analyze_page_ai_crop(
    *,
    provider: "LLMProvider",
    image_path: Path,
    system_prompt: str,
    user_prompt: str,
    cell_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    reference_images: list[ReferenceImage] | None = None,
) -> AiCropResult:
    """AI-crop-mode replacement for the non-cutting path's downscale_image ->
    provider.analyze_image -> parse_model_response. Must be called with
    `image_path` = page.image_path (the full-resolution original), same
    reasoning as Cutting mode: crop boxes are recorded in the same pixel
    space page.width/height describes.
    """
    with Image.open(image_path) as image:
        image.load()
        full_width, full_height = image.size

        # PASS 1: locate every tagged grid cell, using the fixed internal
        # cell_prompt -- NOT the request's user_prompt, which is the user's
        # actual object-detection task and irrelevant to cell-finding.
        pass1_bytes, pass1_media_type = downscale_image(image_path.read_bytes(), "image/png")
        pass1_analysis = await provider.analyze_image(
            image_bytes=pass1_bytes,
            media_type=pass1_media_type,
            system_prompt=system_prompt,
            user_prompt=cell_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reference_images=None,  # calibration examples are for the main object task, not cell-detection
        )
        pass1_parsed, _pass1_parse_error = parse_model_response(pass1_analysis.text)

        # Pass 1's own truncation state used to be silently discarded here --
        # if the model's response got cut off by max_tokens partway through
        # listing cells, parse_model_response still returns whatever complete
        # detections came before the cut, with no signal that the count is
        # short of what's actually on the page. Surfaced now (see
        # pass1_truncated_note below) instead of looking like a clean,
        # complete result that just happens to have fewer cells.
        pass1_truncated_note = (
            "Pass 1's response was cut off (hit the max tokens limit) -- it "
            "may have found fewer grid cells than are actually on the page. "
            "Consider raising max tokens and re-running."
            if pass1_analysis.truncated
            else None
        )

        if pass1_parsed is None or not pass1_parsed.objects:
            message = "No elevation cells were found on this page."
            if pass1_truncated_note:
                message = f"{message} {pass1_truncated_note}"
            return _empty_result(message)

        # No content-based filtering here anymore -- every cell Pass 1
        # returns (floor plans included) gets cropped and sent to Pass 2.
        # Filtering by tag text used to happen here (dropping any cell whose
        # text contained "floor plan"), but that ran unconditionally
        # regardless of what the (now user-editable) crop prompt actually
        # asked the model to return, so it silently contradicted a crop
        # prompt that explicitly asked for floor plans to be included.

        # CROPPING: sequential cell_id assigned by our own code (not the
        # model's own possibly-colliding circled numbers), same reasoning
        # as Cutting's r{row}c{col} scheme.
        cell_crops: list[CellCrop] = []
        crop_bytes_by_id: dict[str, bytes] = {}
        for i, obj in enumerate(pass1_parsed.objects):
            box = _crop_box_for_cell(obj, full_width, full_height)
            png_bytes, _scale = crop_and_resize(image, box)
            cell_id = f"cell{i}"
            cell_crops.append(CellCrop(cell_id=cell_id, tag_text=obj.text or ""))
            crop_bytes_by_id[cell_id] = png_bytes

    # PASS 2: the real object-detection task, batched across every
    # surviving crop in one request -- reuses Cutting's analyze_tiles as-is.
    # Caption is exactly "IMAGE {n} — cell_id: {cell_id}" (1-based n) --
    # tag_text no longer goes into the prompt at all, only PASS2_INSTRUCTION
    # describes the caption format the model should expect to see.
    tile_content = [
        (crop_bytes_by_id[c.cell_id], f"IMAGE {i + 1} — cell_id: {c.cell_id}")
        for i, c in enumerate(cell_crops)
    ]
    augmented_prompt = user_prompt + PASS2_INSTRUCTION
    pass2_analysis = await provider.analyze_tiles(
        tiles=tile_content,
        media_type="image/png",
        system_prompt=system_prompt,
        user_prompt=augmented_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        reference_images=reference_images,
    )

    pass2_entries = parse_pass2_response(pass2_analysis.text)
    crops, mismatch_message = _build_verified_crops(cell_crops, crop_bytes_by_id, pass2_entries)

    all_detections = [obj for crop in crops for obj in crop.detections]
    parsed = ParsedResult(objects=[], counts=_tally_counts(all_detections))

    combined_message = " ".join(m for m in (pass1_truncated_note, mismatch_message) if m) or None

    return AiCropResult(
        raw_text=pass2_analysis.text,
        parsed=parsed,
        parse_error=False,
        truncated=pass2_analysis.truncated,
        reasoning_tokens=pass2_analysis.reasoning_tokens,
        completion_tokens=pass2_analysis.completion_tokens,
        crops=crops,
        no_cells_message=combined_message,
    )
