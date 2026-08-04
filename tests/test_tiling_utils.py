"""Tests for app/tiling_utils.py — the locate_tiled workflow's grid, crop, and
dedup helpers.
"""

from PIL import Image

from app.tiling_utils import compute_tile_grid, crop_pixel_region, deduplicate_objects


def test_compute_tile_grid_single_tile_when_image_fits():
    grid = compute_tile_grid(800, 600, tile_size=1400, overlap_frac=0.2)
    assert grid == [(0, 0, 800, 600)]


def test_compute_tile_grid_covers_full_image_with_overlap():
    grid = compute_tile_grid(2000, 1500, tile_size=1400, overlap_frac=0.2)

    # Every tile box is within bounds.
    for left, top, right, bottom in grid:
        assert 0 <= left < right <= 2000
        assert 0 <= top < bottom <= 1500

    # The far edges are always reached exactly (no gap left uncovered).
    assert max(right for _, _, right, _ in grid) == 2000
    assert max(bottom for _, _, _, bottom in grid) == 1500
    assert min(left for left, _, _, _ in grid) == 0
    assert min(top for _, top, _, _ in grid) == 0

    # With overlap_frac > 0, consecutive tiles along an axis actually overlap.
    xs = sorted(set(left for left, _, _, _ in grid))
    assert xs[1] < xs[0] + 1400  # second tile starts before the first one ends


def test_compute_tile_grid_zero_overlap_can_still_overlap_at_the_far_edge():
    # 2000 doesn't divide evenly by 1400, so even at 0% nominal overlap the
    # last tile is pulled left to stay flush with the edge, overlapping the
    # previous one — this is intentional (full coverage beats exact spacing).
    grid = compute_tile_grid(2000, 100, tile_size=1400, overlap_frac=0.0)
    xs = sorted(set(left for left, _, _, _ in grid))
    assert xs == [0, 600]


def test_deduplicate_objects_merges_within_same_page_and_label_only():
    objects = [
        {"label": "cabinet", "image_index": 0, "box": [100, 100, 300, 300]},
        {"label": "cabinet", "image_index": 0, "box": [102, 101, 301, 299]},  # near-duplicate
        {"label": "cabinet", "image_index": 0, "box": [900, 900, 950, 950]},  # distinct
        {"label": "countertop", "image_index": 0, "box": [100, 100, 300, 300]},  # different label
        {"label": "cabinet", "image_index": 1, "box": [100, 100, 300, 300]},  # different page
    ]

    kept = deduplicate_objects(objects, iou_threshold=0.5)

    assert len(kept) == 4
    kept_keys = [(o["label"], o["image_index"], tuple(o["box"])) for o in kept]
    assert ("cabinet", 0, (102, 101, 301, 299)) not in kept_keys  # the duplicate was dropped
    assert ("cabinet", 0, (100, 100, 300, 300)) in kept_keys  # first-seen one survives
    assert ("cabinet", 0, (900, 900, 950, 950)) in kept_keys
    assert ("countertop", 0, (100, 100, 300, 300)) in kept_keys
    assert ("cabinet", 1, (100, 100, 300, 300)) in kept_keys


def test_deduplicate_objects_below_threshold_keeps_both():
    objects = [
        {"label": "cabinet", "image_index": 0, "box": [0, 0, 100, 100]},
        {"label": "cabinet", "image_index": 0, "box": [90, 0, 190, 100]},  # low IoU with the first
    ]
    kept = deduplicate_objects(objects, iou_threshold=0.5)
    assert len(kept) == 2


def test_crop_pixel_region_produces_expected_size(tmp_path):
    source = tmp_path / "page.png"
    Image.new("RGB", (800, 600), color="white").save(source)

    output = tmp_path / "tile.png"
    crop_pixel_region(str(source), (100, 50, 500, 350), str(output))

    with Image.open(output) as cropped:
        assert cropped.size == (400, 300)
