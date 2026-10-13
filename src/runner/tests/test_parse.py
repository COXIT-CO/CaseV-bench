import json
from typing import Any

import pytest

from core.parse import ResponseParser


def _entry(label: str = "cabinet", x_min: float = 0.1, y_min: float = 0.1) -> dict[str, Any]:
    return {
        "label": label,
        "bounding_box": {"x_min": x_min, "y_min": y_min, "x_max": 0.5, "y_max": 0.5},
    }


class TestResponseParser:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.parser = ResponseParser()

    def test_parses_a_well_formed_array(self) -> None:
        result = self.parser.parse_response(
            json.dumps([_entry(), _entry(label="elevation")]), page=1
        )
        assert result.complete
        assert result.dropped == 0
        assert [box["object_type"] for box in result.boxes] == ["cabinet", "elevation"]
        assert all(box["page"] == 1 for box in result.boxes)

    def test_extracts_from_a_markdown_fence(self) -> None:
        fenced = f"Sure, here are the boxes:\n```json\n{json.dumps([_entry()])}\n```"
        result = self.parser.parse_response(fenced, page=1)
        assert result.complete
        assert len(result.boxes) == 1

    def test_salvages_entries_before_a_truncated_tail(self) -> None:
        array_text = json.dumps([_entry(), _entry(label="elevation")])
        truncated = array_text[: array_text.rfind("}")]  # cut off mid-second-entry
        result = self.parser.parse_response(truncated, page=1)
        assert not result.complete
        assert len(result.boxes) == 1
        assert result.boxes[0]["object_type"] == "cabinet"

    def test_drops_entries_off_the_taxonomy(self) -> None:
        result = self.parser.parse_response(json.dumps([_entry(label="window")]), page=1)
        assert result.complete
        assert result.boxes == []
        assert result.dropped == 1

    def test_drops_entries_missing_bounding_box_keys(self) -> None:
        entry = {"label": "cabinet", "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.5}}
        result = self.parser.parse_response(json.dumps([entry]), page=1)
        assert result.boxes == []
        assert result.dropped == 1

    def test_rescales_thousand_scale_coordinates(self) -> None:
        entry = _entry(x_min=100, y_min=100)
        entry["bounding_box"].update(x_max=500, y_max=500)
        result = self.parser.parse_response(json.dumps([entry]), page=1)
        assert result.boxes[0]["bbox"] == [0.1, 0.1, 0.5, 0.5]

    def test_sorts_inverted_min_max_pairs(self) -> None:
        entry = _entry()
        entry["bounding_box"].update(x_min=0.5, x_max=0.1)
        result = self.parser.parse_response(json.dumps([entry]), page=1)
        assert result.boxes[0]["bbox"][0] == 0.1
        assert result.boxes[0]["bbox"][2] == 0.5

    def test_no_array_in_the_text_is_incomplete_and_empty(self) -> None:
        result = self.parser.parse_response("the model refused to answer", page=1)
        assert result.boxes == []
        assert not result.complete
