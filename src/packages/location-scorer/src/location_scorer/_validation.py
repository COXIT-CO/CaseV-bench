from typing import Sequence

from ._types import Box


def validate_ground_truth(ground_truth: Sequence[Box]) -> None:
    for index, box in enumerate(ground_truth):
        x_min, y_min, x_max, y_max = box["bbox"]
        if x_min > x_max or y_min > y_max:
            raise ValueError(
                f"ground_truth[{index}] is inverted: "
                f"[{x_min}, {y_min}, {x_max}, {y_max}]"
            )
        if x_min == x_max:
            raise ValueError(
                f"ground_truth[{index}] has zero width: "
                f"x_min and x_max are both {x_min}"
            )
        if y_min == y_max:
            raise ValueError(
                f"ground_truth[{index}] has zero height: "
                f"y_min and y_max are both {y_min}"
            )
