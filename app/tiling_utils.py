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


def local_box_touches_edge(local_box_0_1000: list[float], tolerance: float = 15.0) -> bool:
    """Check whether a detection's box, in 0..1000 coordinates LOCAL to the
    tile it came from, touches (or nearly touches) any edge of that tile.
    `tolerance` is in the same 0..1000 units (default 15 == 1.5% of the
    tile's own width/height).

    A box touching its own tile's edge is a signal that the object it
    belongs to may have been cut off by the tile boundary: the model can
    only draw a box around what it can actually see, so a truncated object
    reads as ending right at the edge of the image it was given — real
    object boundaries essentially never land there by chance.
    """
    left, top, right, bottom = local_box_0_1000
    return (
        left <= tolerance
        or top <= tolerance
        or right >= 1000 - tolerance
        or bottom >= 1000 - tolerance
    )


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


def _boxes_touch_or_overlap(box_a: list[float], box_b: list[float], gap_tolerance: float = 0.0) -> bool:
    """True if two boxes overlap, or are separated by no more than
    `gap_tolerance` on every axis — i.e. touching or nearly touching,
    within rounding/seam slack. Same coordinate units as the boxes
    themselves (full-page 0..1000 here)."""
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    return not (
        ax1 + gap_tolerance < bx0
        or bx1 + gap_tolerance < ax0
        or ay1 + gap_tolerance < by0
        or by1 + gap_tolerance < ay0
    )


def deduplicate_objects(objects: list[dict], iou_threshold: float) -> list[dict]:
    """Greedy NMS within each (image_index, label) group, since overlapping
    tiles can independently re-detect the same object. There are no
    confidence scores to rank by, so the first-seen detection in a
    mutually-overlapping cluster is kept and later duplicates above
    `iou_threshold` are dropped.

    This only handles true duplicates (the same object seen whole in two
    overlapping tiles) — it does NOT stitch together two fragments of one
    object that was cut in half by a tile boundary, since two such
    fragments typically have LOW mutual IoU (they barely touch, rather
    than overlap). Kept here for reference/comparison; `fuse_tiled_objects`
    below is the default merge strategy for the tiled workflow because it
    additionally handles the split-object case.
    """
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


def fuse_tiled_objects(
    objects: list[dict],
    iou_dup_threshold: float = 0.3,
    edge_gap_tolerance: float = 4.0,
) -> list[dict]:
    """Merge tiled-inference detections within each (image_index, label)
    group into final boxes, handling BOTH cases that come out of
    overlapping-tile inference:

    1. True duplicates — the same object seen whole in two overlapping
       tiles, showing up as two boxes with high mutual IoU.
    2. Split fragments — one real object cut in half by a tile boundary,
       showing up as two (or more) boxes that barely touch each other and
       have LOW mutual IoU, but where at least one fragment is flagged
       `touches_tile_edge=True` (set by the caller from the object's
       LOCAL, tile-relative coordinates before remapping — see
       `local_box_touches_edge`), i.e. it looks cut off by its own tile's
       border.

    Each connected group (via union-find, so 3+ fragments of one very wide
    object chained across three tiles all merge together, not just pairs)
    is replaced by the bounding-box UNION of its members. Objects that
    don't match either case pass through unchanged.

    `iou_dup_threshold` is intentionally lower than the 0.5-ish typically
    used for pure NMS dedup, because near-duplicate boxes from two
    overlapping tiles are rarely pixel-identical (each tile's crop
    position shifts the model's read of the object's exact edges
    slightly) — union instead of "pick one" is also more forgiving of
    that jitter than plain NMS would be.
    """
    groups: dict[tuple[int, str], list[dict]] = {}
    for obj in objects:
        groups.setdefault((obj["image_index"], obj["label"]), []).append(obj)

    fused: list[dict] = []
    for items in groups.values():
        n = len(items)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(n):
            for j in range(i + 1, n):
                box_i, box_j = items[i]["box"], items[j]["box"]

                if _iou(box_i, box_j) >= iou_dup_threshold:
                    union(i, j)
                    continue

                edge_i = items[i].get("touches_tile_edge", False)
                edge_j = items[j].get("touches_tile_edge", False)
                if (edge_i or edge_j) and _boxes_touch_or_overlap(box_i, box_j, gap_tolerance=edge_gap_tolerance):
                    union(i, j)

        clusters: dict[int, list[dict]] = {}
        for idx in range(n):
            clusters.setdefault(find(idx), []).append(items[idx])

        for cluster in clusters.values():
            if len(cluster) == 1:
                solo = dict(cluster[0])
                solo.pop("touches_tile_edge", None)
                fused.append(solo)
                continue

            left = min(o["left"] for o in cluster)
            top = min(o["top"] for o in cluster)
            right = max(o["right"] for o in cluster)
            bottom = max(o["bottom"] for o in cluster)

            merged = dict(cluster[0])
            merged["box"] = [left, top, right, bottom]
            merged["left"], merged["top"], merged["right"], merged["bottom"] = left, top, right, bottom
            merged.pop("touches_tile_edge", None)
            fused.append(merged)

    return fused