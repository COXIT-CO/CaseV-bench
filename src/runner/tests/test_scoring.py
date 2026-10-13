import pytest

from core.scoring import CANONICAL_IOU_THRESHOLD, Box, ScorerWrapper


def _box(
    object_type: str, x_min: float, y_min: float, x_max: float, y_max: float, page: int = 1
) -> Box:
    return {"object_type": object_type, "bbox": [x_min, y_min, x_max, y_max], "page": page}


class TestScorerWrapper:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.scorer = ScorerWrapper()

    def test_score_drawing_reports_a_clean_match_at_the_canonical_threshold(self) -> None:
        ground_truth = [_box("cabinet", 0.1, 0.1, 0.3, 0.3)]
        predictions = [_box("cabinet", 0.1, 0.1, 0.3, 0.3)]

        result = self.scorer.score_drawing("drawing-1", predictions, ground_truth)

        assert not result["unscored"]
        canonical = result["scores"][f"{CANONICAL_IOU_THRESHOLD:.2f}"]
        assert canonical["counts"] == {"tp": 1, "fp": 0, "fn": 0}
        assert canonical["metrics"]["f1"] == 1.0

    def test_score_drawing_is_unscored_with_no_ground_truth(self) -> None:
        result = self.scorer.score_drawing("drawing-1", [_box("cabinet", 0.1, 0.1, 0.3, 0.3)], [])
        assert result["unscored"]

    def test_aggregate_run_score_pools_counts_across_drawings(self) -> None:
        hit = _box("cabinet", 0.1, 0.1, 0.3, 0.3)
        miss_gt = _box("elevation", 0.5, 0.5, 0.9, 0.9)

        drawing_a = self.scorer.score_drawing("drawing-a", [hit], [hit])
        drawing_b = self.scorer.score_drawing("drawing-b", [], [miss_gt])

        aggregate = self.scorer.aggregate_run_score([drawing_a, drawing_b])

        canonical = aggregate["scores"][f"{CANONICAL_IOU_THRESHOLD:.2f}"]
        assert canonical["counts"] == {"tp": 1, "fp": 0, "fn": 1}
        assert set(aggregate["drawings"]) == {"drawing-a", "drawing-b"}
        assert canonical["per_type"]["cabinet"]["counts"] == {"tp": 1, "fp": 0, "fn": 0}
        assert canonical["per_type"]["elevation"]["counts"] == {"tp": 0, "fp": 0, "fn": 1}
