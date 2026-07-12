import json
from pathlib import Path

from PIL import Image, ImageDraw

from models.results import LocationDetection

# Long-edge (px) each page image is downsampled to at ingest; snapshotted on a Run.
DEFAULT_DOWNSAMPLE_PX = 1568


def parse_json(content: str) -> dict:
    def _strip_code_fence(content: str) -> str:
        if content is None:
            raise ValueError(
                "response content is empty (likely truncated before completion)"
            )
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        return content.strip()

    stripped = _strip_code_fence(content)
    return json.loads(stripped)


def downsample(
    source: Path, dest: Path, max_long_edge: int = DEFAULT_DOWNSAMPLE_PX
) -> Path:
    with Image.open(source) as image:
        scale = max_long_edge / max(image.size)
        new_size = (round(image.width * scale), round(image.height * scale))
        image.resize(new_size).save(dest)
    return dest


def draw_overlay(
    image_path: Path, detections: list[LocationDetection], dest: Path
) -> Path:
    with Image.open(image_path) as image:
        overlay = image.convert("RGB").copy()
        draw = ImageDraw.Draw(overlay)
        width, height = overlay.size
        for detection in detections:
            box = detection.bounding_box
            x0, y0 = box.x_min * width, box.y_min * height
            draw.rectangle(
                (x0, y0, box.x_max * width, box.y_max * height),
                outline="red",
                width=3,
            )
            # Label the box so the taxonomy class is legible on the overlay, not just
            # the location — a developer needs to tell a cabinet box from a countertop.
            draw.text((x0 + 2, max(0, y0 - 12)), detection.label, fill="red")
        dest.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(dest)
    return dest
