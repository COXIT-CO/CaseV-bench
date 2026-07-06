import json
from pathlib import Path
from PIL import Image, ImageDraw
from models.results import CountResult, LocationDetection


def _strip_code_fence(content: str) -> str:
    if content is None:
        raise ValueError("response content is empty (likely truncated before completion)")
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0]
    return content.strip()


def parse_detections(content: str) -> list[LocationDetection]:
    return [
        LocationDetection(**detection)
        for detection in json.loads(_strip_code_fence(content))
    ]


def parse_count_result(content: str) -> CountResult:
    return CountResult(**json.loads(_strip_code_fence(content)))


def downsample(source: Path, dest: Path, max_long_edge: int) -> Path:
    with Image.open(source) as image:
        scale = max_long_edge / max(image.size)
        new_size = (round(image.width * scale), round(image.height * scale))
        image.resize(new_size).save(dest)
    return dest


def draw_overlay(image_path: Path, detections: list[LocationDetection], dest: Path) -> Path:
    with Image.open(image_path) as image:
        overlay = image.convert("RGB").copy()
        draw = ImageDraw.Draw(overlay)
        width, height = overlay.size
        for detection in detections:
            box = detection.bounding_box
            draw.rectangle(
                (
                    box.x_min * width,
                    box.y_min * height,
                    box.x_max * width,
                    box.y_max * height,
                ),
                outline="red",
                width=3,
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(dest)
    return dest


def next_run_key(logs: dict) -> str:
    existing = [int(key.split("_")[1]) for key in logs if key.startswith("run_")]
    return f"run_{max(existing, default=0) + 1}"
