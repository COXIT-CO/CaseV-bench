from __future__ import annotations

from PIL import Image


def _axis_starts(length: int, tile_size: int, stride: int) -> list[int]:
    """Start offsets along one axis covering `length` with `tile_size`-wide
    windows spaced `stride` apart, always including a final window flush
    with the far edge so coverage never falls short of it."""
    if length <= tile_size:
        return [0]
    starts = list(range(0, length - tile_size + 1, stride))
    if starts[-1] + tile_size < length:
        starts.append(length - tile_size)
    return starts


def compute_tile_grid(
    width: int,
    height: int,
    tile_size: int,
    overlap_frac: float,
) -> list[tuple[int, int, int, int]]:
    """SAHI-style sliding-window grid: overlapping `tile_size`x`tile_size`
    pixel boxes (clamped to the image bounds) covering the full image, with
    consecutive tiles offset by `tile_size * (1 - overlap_frac)`. Returns
    (left, top, right, bottom) pixel boxes in row-major order."""
    stride = max(1, round(tile_size * (1 - overlap_frac)))
    xs = _axis_starts(width, tile_size, stride)
    ys = _axis_starts(height, tile_size, stride)
    return [
        (x, y, min(x + tile_size, width), min(y + tile_size, height))
        for y in ys
        for x in xs
    ]


def crop_pixel_region(
    image_path: str,
    px_box: tuple[int, int, int, int],
    output_path: str,
) -> str:
    """Crop `image_path` to the given pixel box (already computed by
    compute_tile_grid — no padding or normalization) and save it as a PNG."""
    with Image.open(image_path) as image:
        image.convert("RGB").crop(px_box).save(output_path, format="PNG")
    return output_path


def _iou(box_a: list[float], box_b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def deduplicate_objects(objects: list[dict], iou_threshold: float) -> list[dict]:
    """Greedy NMS within each (image_index, label) group, since overlapping
    tiles can independently re-detect the same object. There are no
    confidence scores to rank by, so the first-seen detection in a
    mutually-overlapping cluster is kept and later duplicates above
    `iou_threshold` are dropped."""
    groups: dict[tuple[int, str], list[dict]] = {}
    for obj in objects:
        groups.setdefault((obj["image_index"], obj["label"]), []).append(obj)

    kept: list[dict] = []
    for group in groups.values():
        suppressed = [False] * len(group)
        for i, obj in enumerate(group):
            if suppressed[i]:
                continue
            kept.append(obj)
            for j in range(i + 1, len(group)):
                if not suppressed[j] and _iou(obj["box"], group[j]["box"]) >= iou_threshold:
                    suppressed[j] = True
    return kept
