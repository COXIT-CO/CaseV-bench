from __future__ import annotations

import os
import shutil

import pymupdf
from PIL import Image

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg'}


def extract_pdf_images(file_path: str, images_dir: str, dpi: int = 200) -> list[str]:
    pdf_stem = os.path.splitext(os.path.basename(file_path))[0]
    target_dir = os.path.join(images_dir, pdf_stem)
    os.makedirs(target_dir, exist_ok=True)
    image_paths = []
    with pymupdf.open(file_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            image_path = os.path.join(target_dir, f"page_{page_index:04d}.png")
            pix.save(image_path)
            image_paths.append(image_path)
    return image_paths


def extract_images_from_folder(folder_path: str, dpi: int = 200, output_dir: str | None = None) -> list[str]:
    """Convert all PDFs/raster inputs to PNGs in a clean run-specific directory."""
    folder_path = os.path.abspath(folder_path)
    if not os.path.isdir(folder_path):
        return []
    images_dir = os.path.abspath(output_dir or os.path.join(folder_path, "images"))
    if os.path.exists(images_dir):
        shutil.rmtree(images_dir)
    os.makedirs(images_dir, exist_ok=True)
    image_paths: list[str] = []
    for filename in sorted(os.listdir(folder_path)):
        full_path = os.path.join(folder_path, filename)
        if not os.path.isfile(full_path):
            continue
        extension = os.path.splitext(filename)[1].lower()
        if extension == '.pdf':
            image_paths.extend(extract_pdf_images(full_path, images_dir, dpi=dpi))
        elif extension in IMAGE_EXTENSIONS:
            target_dir = os.path.join(images_dir, os.path.splitext(filename)[0])
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, "page_0001.png")
            with Image.open(full_path) as image:
                image.convert("RGB").save(target, "PNG")
            image_paths.append(target)
    return image_paths
