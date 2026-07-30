"""Pydantic request/response models shared across API routes."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ModelOption(BaseModel):
    id: str
    label: str
    provider: str = "openrouter"
    implemented: bool = True
    custom: bool = False


class AddModelRequest(BaseModel):
    id: str = Field(min_length=1, description="OpenRouter model slug, e.g. moonshotai/kimi-k3")


class ConfigResponse(BaseModel):
    """What the frontend needs on load: model list + defaults + default prompt text."""

    models: list[ModelOption]
    default_model: str
    default_temperature: float
    default_max_tokens: int
    default_system_prompt: str
    default_user_prompt: str
    # AI-crop mode's Pass 1 (region/cell detection) prompt, editable in the
    # UI only when AI-crop is on (see PromptEditor.jsx's "Crop prompt"
    # field). Defaults to Settings.ai_crop_cell_prompt_text() -- the same
    # fixed prompt Pass 1 used before this field existed -- so out of the
    # box, behavior is unchanged.
    default_crop_prompt: str
    categories: list[str]
    pdf_dpi: int


class PageInfo(BaseModel):
    page_id: str = Field(description="Stable id for this page, e.g. 'page-1'")
    page_number: int = Field(description="1-indexed page number")
    width: int
    height: int
    image_url: str = Field(description="Relative URL the frontend can load the PNG from")


class SavePromptRequest(BaseModel):
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    page_count: int
    pages: list[PageInfo]


class ReferenceImage(BaseModel):
    """A user-attached calibration image (e.g. "this is what a cabinet symbol
    looks like"), sent alongside the main page image in the same API call.
    Only `name` (for history/display) is persisted server-side -- the image
    bytes themselves are per-request only."""

    data: str = Field(description="Base64-encoded image bytes, no data: URI prefix")
    media_type: str = Field(description="e.g. image/png, image/jpeg")
    name: str = Field(default="", description="Original filename, for display/history only")


class AnalyzeRequest(BaseModel):
    session_id: str
    page_ids: list[str] = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    model: str
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, ge=1, le=64000)
    reference_images: list[ReferenceImage] = Field(default_factory=list)
    # Set by the frontend's Multi-Prompting mode: one independent /analyze
    # call per category, each tagged with which category its `user_prompt`
    # was written for (e.g. "cabinets"). analyze.py relabels every detection
    # in the response to this category (overriding whatever label the model
    # actually used) and merges into any other categories' results already
    # stored for the same page (see session_store.set_result's `merge` flag)
    # instead of overwriting them -- so all 4 categories' detections survive
    # in the session's stored results (and therefore the exports), not just
    # the last category to finish. None for an ordinary single-prompt call.
    category: str | None = None
    # Overlapping-tile detection ("Cutting" mode, frontend Toolbar.jsx). When
    # True, the page is split into a grid_rows x grid_cols grid of
    # overlapping crops, each sent as a separate image block in ONE combined
    # LLM request (see services/tiling.py), instead of one whole-page image
    # -- lets a model "see" small objects at higher effective resolution
    # than the whole page would allow at a sane token budget. False
    # (default) leaves the existing single-image path completely unchanged
    # -- see analyze.py's analyze_one.
    cutting: bool = False
    grid_rows: int = Field(default=3, ge=1, le=6)
    grid_cols: int = Field(default=3, ge=1, le=6)
    # Fraction (not percent) of a cell's own width/height that adjacent
    # tiles overlap by on each shared edge, so an object straddling a grid
    # line ends up fully contained in at least one tile. 0.25 default
    # matches the frontend's "Overlap %" default of 25. Bounded at 0.5
    # because an overlap at or beyond half a cell's size makes the grid math
    # degenerate (each tile expands by overlap_pct on every side, so two
    # adjacent expanded cells would swallow each other's entire far edge).
    overlap_pct: float = Field(default=0.25, ge=0.0, le=0.5)
    # AI-crop mode (services/ai_crop.py): a two-pass alternative to Cutting --
    # Pass 1 locates tagged grid cells on the page, our own code filters out
    # floor-plan cells, Pass 2 runs the real object-detection prompt against
    # just the surviving crops. Mutually exclusive with `cutting` (see
    # _check_cutting_ai_crop_exclusive below) -- both are alternate ways of
    # chopping up the same page before detection, and running both at once
    # isn't meaningful. The frontend UI already keeps them mutually
    # exclusive; this validator is defense-in-depth for a direct API call.
    ai_crop: bool = False
    # AI-crop mode's Pass 1 (region/cell detection) prompt -- editable in
    # the UI only when ai_crop is on (see PromptEditor.jsx's "Crop prompt"
    # field, defaulted from ConfigResponse.default_crop_prompt). Pass 2
    # (the real object-detection task) is unaffected by this field and
    # keeps using `user_prompt` exactly as it always has. Optional/blank
    # falls back to Settings.ai_crop_cell_prompt_text() in analyze.py --
    # the same fixed prompt Pass 1 used before this field existed -- so a
    # direct API call that omits it still behaves exactly as before.
    crop_prompt: str | None = None

    @model_validator(mode="after")
    def _check_cutting_ai_crop_exclusive(self) -> "AnalyzeRequest":
        if self.cutting and self.ai_crop:
            raise ValueError("cutting and ai_crop cannot both be enabled at once.")
        return self


class ResetResultsRequest(BaseModel):
    """Body for POST /sessions/{id}/reset-results. Multi-Prompting mode calls
    this once, before firing its 4 parallel per-category /analyze calls, so
    a re-run starts from a clean slate instead of merging on top of stale
    detections left over from a previous run of the same pages."""

    page_ids: list[str] = Field(min_length=1)


class BBox(BaseModel):
    """Pixel-space x/y/width/height, used only on `ExportLocationObject` --
    converted from the model's 0-1000-normalized `DetectedObject` coordinates
    at the export boundary in `parser.build_location_export` to match the
    benchmark's on-disk format."""

    x: float
    y: float
    width: float
    height: float


class DetectedObject(BaseModel):
    """One detection returned by the model (see prompts/detector_system.txt).
    The model's raw top-level response is a single JSON object with exactly
    one key, "bounding_box", holding an array of these -- NOT a bare array
    (that was the previous schema; see CLAUDE.md's coordinate-system entry
    for that history) -- unpacked in parser.parse_model_response, which is
    also lenient about a bare array at the top level, since that's
    unambiguous either way.

    Coordinates are flattened directly onto this object -- there is no
    nested box structure -- normalized 0-1000 relative to the source image's
    own width/height, origin top-left: (x_min, y_min) is the top-left
    corner, (x_max, y_max) the bottom-right. Downstream code (parser.py,
    BBoxCanvas.jsx, ResultsSummary.jsx) rescales these via `(value / 1000) *
    dimension` at render/export time -- never upstream.

    The 0-1000 range was chosen (over a 0-1 fraction, used previously) after
    real models (Gemini, Kimi K3) reliably ignored a "use 0-1 fractions"
    instruction and emitted values in roughly the 0-1000 range anyway --
    that's the near-universal training convention for bounding boxes across
    vision models (e.g. Gemini's own native `box_2d` format), so asking for
    fractions was fighting every model's prior instead of working with it.
    The bounds are enforced (not just documented): a value outside 0-1000 is
    semantically invalid, not a recoverable typo like a misnamed key, so
    it's rejected here and the one bad detection is dropped by
    parser.parse_model_response rather than failing the whole page.
    """

    label: str
    x_min: float = Field(ge=0.0, le=1000.0)
    y_min: float = Field(ge=0.0, le=1000.0)
    x_max: float = Field(ge=0.0, le=1000.0)
    y_max: float = Field(ge=0.0, le=1000.0)
    # Not consumed for filtering/scoring anywhere (yet) -- just captured and
    # surfaced (ResultsSummary.jsx) since the model reports it. Optional:
    # nothing else in this app requires it to be present.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # Set only for a detection produced under "Cutting" mode (see
    # services/tiling.py) -- which tile (e.g. "r0c1") the model reported
    # this detection against, in that tile's own local coordinate space,
    # before remap_tile_detections converts it to full-image coordinates.
    # Cleared back to None once a detection is merged across tiles (see
    # merge_overlapping_detections), since a merged box no longer belongs to
    # one tile. Always None on the ordinary non-cutting path. Nothing
    # downstream (BBoxCanvas.jsx, ResultsSummary.jsx,
    # parser.build_location_export) reads this -- purely diagnostic.
    tile_id: str | None = None
    # The system prompt's JSON schema has long included a "text" key on each
    # detection (see prompts/detector_system.txt) that nothing captured
    # until now -- Pydantic silently drops it as an extra field. AI-crop's
    # Pass 1 (services/ai_crop.py) depends on this being captured: it's the
    # detail-tag text (e.g. "66 VENDING - NORTH ELEVATION") used to filter
    # out floor-plan cells in our own code rather than the prompt. Optional
    # and unused outside AI-crop's Pass 1.
    text: str | None = None


class ParsedResult(BaseModel):
    objects: list[DetectedObject] = Field(default_factory=list)
    # NOT supplied by the model -- prompts/detector_system.txt doesn't ask it
    # to count. Populated by parser.parse_model_response() after validation,
    # by tallying `objects` per label.
    counts: dict[str, int] = Field(default_factory=dict)


class PageResultStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class CropInfo(BaseModel):
    """One crop AI-crop's Pass 1 + filtering step decided to send to Pass 2
    (services/ai_crop.py). Carried on PageResult purely so the frontend's
    crops carousel (CropsCarousel.jsx) can render every surviving crop --
    plus its own detections, drawn directly on it -- from already-held
    response state, no server-side file storage, no extra network round
    trip to view them.

    Detections stay in THIS crop's own 0-1000-normalized coordinate space
    (see DetectedObject) -- there is no full-page remap anymore (Pass 2's
    per-crop coordinates were found to be unreliable once converted back to
    full-page space, so the app now shows each crop's detections on that
    crop directly instead)."""

    cell_id: str
    tag_text: str
    media_type: str = "image/png"
    # Base64-encoded image bytes, no "data:" URI prefix -- the frontend
    # constructs that, same convention as ReferenceImage.data.
    image_base64: str
    # Crop-local detections (0-1000 normalized relative to THIS crop's own
    # image) -- see services/ai_crop.py's _build_verified_crops. Always
    # ordinary DetectedObject instances; empty when Pass 2 didn't return a
    # matching entry for this crop (see `verified` below).
    detections: list[DetectedObject] = Field(default_factory=list)
    # False when Pass 2's response couldn't be trusted for this specific
    # crop -- either the whole Pass-2 response's entry count didn't match
    # the number of crops sent, or this entry's own echoed cell_id didn't
    # match the cell_id expected at its position. `detections` is still
    # populated whenever Pass 2 returned something for this position, even
    # when verified is False -- flagged, not discarded (see
    # services/ai_crop.py's _build_verified_crops).
    verified: bool = True


class PageResult(BaseModel):
    page_id: str
    page_number: int
    status: PageResultStatus
    raw_response: str | None = None
    parsed: ParsedResult | None = None
    parse_error: bool = False
    truncated: bool = False
    error_message: str | None = None
    # How much of max_tokens went to invisible "thinking" rather than visible
    # output (see OpenRouterProvider._create_completion) -- surfaced mainly
    # to explain a `truncated` result: raising max_tokens won't help much if
    # most of it is being spent on reasoning before the model writes anything.
    reasoning_tokens: int | None = None
    completion_tokens: int | None = None
    # Set only on a single category's own (pre-merge) result -- see
    # AnalyzeRequest.category. Cleared (None) once merged with other
    # categories' results for the same page, since a merged result spans
    # more than one category and no single tag applies.
    category: str | None = None
    # Populated only on a merged (Multi-Prompting) result: which categories
    # failed and why, keyed by category -- e.g. {"elevation_callouts":
    # "Could not parse JSON from the response"}. Lets the UI show "callouts
    # request failed" alongside the successful categories' boxes, instead of
    # the whole page looking like it failed just because one category did.
    category_errors: dict[str, str] | None = None
    # Populated only for an AI-crop run that sent at least one crop to Pass
    # 2 (services/ai_crop.py) -- None on every other path (non-cutting,
    # Cutting, or an AI-crop run that never reached Pass 2).
    crops: list[CropInfo] | None = None
    # A general AI-crop diagnostic note (services/ai_crop.py), populated in
    # any of three distinct cases (space-joined if more than one applies):
    # (1) Pass 1 found zero cells at all -- Pass 2 never runs; (2) Pass 1's
    # own response was truncated (hit max_tokens) -- it may have found fewer
    # cells than are actually on the page, with no other signal that would
    # otherwise be visible; (3) Pass 2 ran but its response's entry count
    # didn't match the number of crops sent, or was unparseable entirely --
    # every crop's `verified` is False in that case (see CropInfo.verified /
    # ai_crop._build_verified_crops), but their `detections` are still kept
    # where Pass 2 did return something. Pass 1 no longer filters cells by
    # content (e.g. "floor plan" tag text) -- every cell it returns gets
    # cropped and sent to Pass 2; that used to happen here but ran
    # unconditionally regardless of what the crop prompt actually asked for.
    # Either way this is a successful, informative result, not an error:
    # `parsed` is still ParsedResult(objects=[], counts={...}), `parse_error`
    # is False, `status` is DONE. None on every other path (including a
    # clean AI-crop run where every crop verified successfully).
    ai_crop_message: str | None = None


class AnalyzeResponse(BaseModel):
    session_id: str
    model: str
    results: list[PageResult]


class ExportCountResponse(BaseModel):
    cabinets: int = 0
    countertops: int = 0
    elevations: int = 0
    elevation_callouts: int = 0


class ExportLocationObject(BaseModel):
    id: str
    category: str
    page: int
    bbox: BBox


class ExportLocationResponse(BaseModel):
    project_id: str
    objects: list[ExportLocationObject]


class ScoreRequest(BaseModel):
    """Body for POST /sessions/{id}/score (services/scoring.py, wrapping the
    location-scorer package in packages/location-scorer). `ground_truth` is
    shaped exactly like this app's own GET .../export/locations output --
    either a prj*-obj-location.json file downloaded from a benchmark
    project, or one of this session's own exports re-uploaded as a
    sanity check.

    Those two sources are NOT in the same scale, and `ground_truth_space`
    says which one this request's `ground_truth` is in:
    - "pdf_points" (default): PDF point space (72 DPI-equivalent), already
      reflecting the page's own rotation -- what a benchmark project's own
      prj*-obj-location.json uses. The backend just scales these up by
      this session's DPI/72 zoom before scoring -- see
      services/scoring.py's module docstring for how this was verified
      (down to individual callout-symbol circles landing exactly on real
      content, no rotation-matrix step needed).
    - "render_pixels": already in this session's own render pixel space --
      what GET .../export/locations produces. No scaling is applied; boxes
      are used as given.
    Picking the wrong one silently over- or under-scales every box -- there's
    no reliable way to detect which space an arbitrary uploaded file is in,
    so this must stay an explicit, visible choice rather than a guess.

    `iou_threshold` has no default here on purpose, mirroring
    location_scorer.score()'s own signature: the library's README is
    explicit that the same version scores differently at different
    operating points, so the choice must be visible at every call site
    rather than buried. The frontend's IoU input starts pre-filled (0.5)
    but is always a value the user sees and can change before scoring, not
    a silent default baked into request-building code.
    """

    ground_truth: ExportLocationResponse
    ground_truth_space: Literal["pdf_points", "render_pixels"] = "pdf_points"
    iou_threshold: float = Field(ge=0.0, le=1.0)
    include_objects: bool = False


class HistoryRecord(BaseModel):
    """One persisted record of a past `POST /analyze` call (see
    services/history_store.py). Written once per request, not once per page,
    since every page in a request shares the same prompts/model/temperature/
    dpi -- that's exactly the configuration a user would want to reload."""

    id: str
    timestamp: str
    filename: str
    model: str
    temperature: float
    max_tokens: int
    dpi: int
    system_prompt: str
    user_prompt: str
    reference_image_names: list[str] = Field(default_factory=list)
    page_count: int
