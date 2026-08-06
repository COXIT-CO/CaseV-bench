from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, UTC
from pathlib import Path
from threading import Thread
from typing import Any

from flask import current_app
from PIL import Image
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename

from app.annotate import annotate_image
from app.crop_prompts import CABINET_COUNTERTOP_SYSTEM_PROMPT, CABINET_COUNTERTOP_USER_PROMPT
from app.crop_utils import crop_image_to_box, remap_crop_box_to_full
from app.extensions import db
from app.models import Prompt, PromptRun, PromptStatus
from app.location_scoring import is_bbox_ground_truth, score_location
from app.pdf_procesing import extract_images_from_folder
from app.result_parser import (
    CANONICAL_LABELS,
    build_comparison,
    extract_counts,
    extract_json,
    extract_objects,
    normalize_expected,
)
from app.tiling_utils import (
    compute_tile_grid,
    crop_pixel_region,
    fuse_tiled_objects,
    local_box_touches_edge,
)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
# 'locate_2pass': pass 1 finds elevation/elevation_callout on the full sheet;
# pass 2 crops the original image to each elevation box and finds
# cabinet/countertop inside that crop at much higher effective resolution.
# 'locate_tiled': independent alternative to locate_2pass — no viewport hierarchy,
# just cuts the full page into overlapping fixed-size tiles and runs the same
# full prompt on every tile, then fuses detections that got duplicated or split
# across tile boundaries (see fuse_tiled_objects in tiling_utils.py).
WORKFLOWS = {'count', 'locate', 'locate_2pass', 'locate_tiled'}
IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Fallback defaults for locate_tiled — _create_run always stores tile_size/
# tile_overlap_pct per run, so these only matter if a PromptRun is ever
# constructed without going through it.
TILE_SIZE_PX = 1400
TILE_OVERLAP_FRAC = 0.2
# Fusion parameters for merging tiled detections (see fuse_tiled_objects in
# tiling_utils.py): TILE_FUSE_IOU_DUP catches true duplicates (same object,
# two overlapping tiles); TILE_FUSE_EDGE_GAP is the max full-page gap (in
# 0..1000 units) allowed between two edge-touching fragments to still be
# considered one object split by a tile boundary. Not yet configurable per
# run — next candidates to expose if fusion quality turns out to matter.
TILE_FUSE_IOU_DUP = 0.3
TILE_FUSE_EDGE_GAP = 4.0


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _safe_disk_filename(original_filename: str) -> str:
    safe = secure_filename(original_filename)
    ext = os.path.splitext(original_filename)[1].lower()
    if not safe or safe == ext.lstrip('.'):
        import uuid
        safe = f"{uuid.uuid4().hex[:10]}{ext}"
    return safe


def get_prompt_folder(prompt_id: int) -> str:
    base_folder = current_app.config.get('UPLOAD_FOLDER', 'data')
    folder = os.path.abspath(os.path.join(base_folder, f'prompt_{prompt_id}'))
    os.makedirs(folder, exist_ok=True)
    return folder


def _inputs_dir_for(prompt: Prompt) -> str:
    return os.path.abspath(os.path.join(prompt.file_path or '', 'inputs'))


def list_input_files(prompt: Prompt) -> list[dict]:
    """Returns metadata for every file in the prompt's inputs folder, for UI display."""
    inputs_dir = _inputs_dir_for(prompt)
    if not os.path.isdir(inputs_dir):
        return []
    items = []
    for name in sorted(os.listdir(inputs_dir)):
        full_path = os.path.join(inputs_dir, name)
        if not os.path.isfile(full_path):
            continue
        ext = os.path.splitext(name)[1].lower().lstrip('.')
        items.append({
            'name': name,
            'ext': ext,
            'is_image': ext in IMAGE_EXTENSIONS,
            'size_kb': round(os.path.getsize(full_path) / 1024, 1),
        })
    return items


def delete_input_file(prompt_id: int, filename: str) -> None:
    prompt = Prompt.query.get_or_404(prompt_id)
    inputs_dir = _inputs_dir_for(prompt)
    full_path = safe_join(inputs_dir, filename)
    if not full_path or not os.path.isfile(full_path):
        raise ValueError(f"File not found: {filename}")
    os.remove(full_path)


def _copy_prompt_files(source: Prompt, target: Prompt) -> str | None:
    """Copies every input file from source prompt's folder into target prompt's folder.
    Used when forking a prompt so the new version starts with the same inputs."""
    source_inputs = _inputs_dir_for(source)
    if not os.path.isdir(source_inputs):
        return None
    files = [n for n in os.listdir(source_inputs) if os.path.isfile(os.path.join(source_inputs, n))]
    if not files:
        return None
    target_folder = get_prompt_folder(target.id)
    target_inputs = os.path.join(target_folder, 'inputs')
    os.makedirs(target_inputs, exist_ok=True)
    for name in files:
        shutil.copy2(os.path.join(source_inputs, name), os.path.join(target_inputs, name))
    return target_folder


def save_uploaded_file(files, prompt_id: int) -> str | None:
    if not files:
        return None
    if not isinstance(files, list):
        files = [files]
    folder = get_prompt_folder(prompt_id)
    inputs_dir = os.path.join(folder, 'inputs')
    os.makedirs(inputs_dir, exist_ok=True)
    saved_any = False
    for file in files:
        if not file or not file.filename:
            continue
        if not allowed_file(file.filename):
            raise ValueError(f"File type not allowed: {file.filename}. Use PDF, PNG, JPG, or JPEG.")
        filename = _safe_disk_filename(file.filename)
        file.save(os.path.join(inputs_dir, filename))
        saved_any = True
    return folder if saved_any else None


def _parse_expected(expected_text: str | None, expected_file=None) -> str | None:
    raw = (expected_text or '').strip()
    if expected_file and getattr(expected_file, 'filename', ''):
        raw = expected_file.read().decode('utf-8').strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON is invalid: {exc.msg}") from exc
    return json.dumps(data, ensure_ascii=False, indent=2)


def remove_prompt_folder(prompt_id: int) -> None:
    prompt_folder = get_prompt_folder(prompt_id)
    if os.path.exists(prompt_folder):
        shutil.rmtree(prompt_folder)


def delete_prompt(prompt_id: int) -> None:
    prompt = Prompt.query.get_or_404(prompt_id)
    remove_prompt_folder(prompt_id)
    db.session.delete(prompt)
    db.session.commit()


def delete_prompt_run(run_id: int) -> int:
    run = PromptRun.query.get_or_404(run_id)
    prompt_id = run.prompt_id
    if run.artifacts_path and os.path.isdir(run.artifacts_path):
        shutil.rmtree(run.artifacts_path, ignore_errors=True)
    db.session.delete(run)
    db.session.commit()
    return prompt_id


def create_prompt(name: str, user_prompt: str, system_prompt: str, model: str,
                  workflow: str, dpi: int, files=None, expected_text: str | None = None,
                  expected_file=None, tile_size: int | None = None,
                  tile_overlap_pct: int | None = None) -> Prompt:
    if workflow not in WORKFLOWS:
        raise ValueError("Unknown workflow.")
    prompt = Prompt(
        name=name,
        content=user_prompt.strip(),
        system_prompt=system_prompt.strip(),
        expected_json=_parse_expected(expected_text, expected_file),
    )
    db.session.add(prompt)
    db.session.flush()
    file_path = save_uploaded_file(files, prompt.id)
    if file_path:
        prompt.file_path = file_path
    run = _create_run(
        prompt.id, model, workflow, dpi,
        tile_size=tile_size if tile_size is not None else TILE_SIZE_PX,
        tile_overlap_pct=tile_overlap_pct if tile_overlap_pct is not None else int(TILE_OVERLAP_FRAC * 100),
    )
    db.session.commit()
    _start_processing_thread(run.id)
    return prompt


def add_files_to_prompt(prompt_id: int, files) -> Prompt:
    prompt = Prompt.query.get_or_404(prompt_id)
    file_path = save_uploaded_file(files, prompt.id)
    if file_path and not prompt.file_path:
        prompt.file_path = file_path
        db.session.commit()
    return prompt


def rename_prompt(prompt_id: int, new_name: str) -> Prompt:
    prompt = Prompt.query.get_or_404(prompt_id)
    if new_name.strip():
        prompt.name = new_name.strip()
        db.session.commit()
    return prompt


def update_or_fork_prompt(prompt_id: int, user_prompt: str, system_prompt: str,
                          model: str, workflow: str, dpi: int,
                          expected_text: str | None = None, expected_file=None,
                          fork_name: str | None = None, tile_size: int | None = None,
                          tile_overlap_pct: int | None = None) -> tuple[Prompt, bool]:
    """Runs the given config against `prompt_id`. If user_prompt or system_prompt differs
    from the stored version by even one character, forks into a brand-new Prompt (copying
    input files) instead of mutating the original, so different prompt texts can be compared
    side by side. Returns (prompt, forked)."""

    original = Prompt.query.get_or_404(prompt_id)
    user_prompt = user_prompt.strip()
    system_prompt = system_prompt.strip()
    if not user_prompt:
        raise ValueError("User prompt cannot be empty.")
    if workflow not in WORKFLOWS:
        raise ValueError("Unknown workflow.")

    expected_provided = expected_text is not None or (expected_file and getattr(expected_file, 'filename', ''))
    expected_json = _parse_expected(expected_text, expected_file) if expected_provided else None

    tile_size = tile_size if tile_size is not None else TILE_SIZE_PX
    tile_overlap_pct = tile_overlap_pct if tile_overlap_pct is not None else int(TILE_OVERLAP_FRAC * 100)

    text_changed = (user_prompt != (original.content or '')) or (system_prompt != (original.system_prompt or ''))

    if text_changed:
        new_name = fork_name.strip() if fork_name and fork_name.strip() else user_prompt[:50]
        forked = Prompt(
            name=new_name,
            content=user_prompt,
            system_prompt=system_prompt,
            expected_json=expected_json if expected_provided else original.expected_json,
        )
        db.session.add(forked)
        db.session.flush()
        forked.file_path = _copy_prompt_files(original, forked)
        run = _create_run(forked.id, model, workflow, dpi, tile_size=tile_size, tile_overlap_pct=tile_overlap_pct)
        db.session.commit()
        _start_processing_thread(run.id)
        return forked, True

    # No text change: just a re-run / config tweak on the same prompt.
    if expected_provided:
        original.expected_json = expected_json
    run = _create_run(original.id, model, workflow, dpi, tile_size=tile_size, tile_overlap_pct=tile_overlap_pct)
    db.session.commit()
    _start_processing_thread(run.id)
    return original, False


def _create_run(prompt_id: int, model: str, workflow: str, dpi: int,
                tile_size: int = TILE_SIZE_PX,
                tile_overlap_pct: int = int(TILE_OVERLAP_FRAC * 100)) -> PromptRun:
    dpi = max(72, min(int(dpi), 600))
    tile_size = max(300, min(int(tile_size), 4000))
    tile_overlap_pct = max(0, min(int(tile_overlap_pct), 60))
    run = PromptRun(
        prompt_id=prompt_id, model=model, workflow=workflow, dpi=dpi,
        tile_size=tile_size, tile_overlap_pct=tile_overlap_pct,
        status=PromptStatus.PENDING,
    )
    db.session.add(run)
    db.session.flush()
    run.artifacts_path = os.path.join(get_prompt_folder(prompt_id), 'runs', str(run.id))
    return run


def _start_processing_thread(run_id: int) -> None:
    app = current_app._get_current_object()
    Thread(target=_process_prompt_run_in_context, args=(app, run_id), daemon=True).start()


def _process_prompt_run_in_context(app, run_id: int) -> None:
    with app.app_context():
        process_prompt_run(run_id)


def encode_image_to_base64(file_path: str) -> str:
    import base64
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _input_files(prompt: Prompt) -> list[str]:
    inputs = os.path.join(prompt.file_path or '', 'inputs')
    if not os.path.isdir(inputs):
        inputs = prompt.file_path or ''
    if not os.path.isdir(inputs):
        return []
    return sorted(os.path.join(inputs, name) for name in os.listdir(inputs) if os.path.isfile(os.path.join(inputs, name)))


def _relative_artifact(path: str, root: str) -> str:
    return Path(path).relative_to(root).as_posix()


def _call_vision_model(
    run: PromptRun,
    system_prompt: str | None,
    user_text: str,
    image_path: str,
    max_attempts: int = 3,
) -> tuple[str, Any, str | None]:
    """Sends one image plus a text prompt to the model.
    Returns (raw_response_text, parsed_json_or_None, error_message_or_None).

    Retries a couple of times on failure (including the "model returned an
    empty response" case, which is usually a transient provider/rate-limit
    glitch rather than a real problem with the prompt or image) so a single
    flaky request doesn't silently lose an entire crop's worth of detections.
    """
    message_content = [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_image_to_base64(image_path)}"}},
    ]
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            raw_response = current_app.extensions["openrouter_client"].generate_response(
                model=run.model,
                message_content=message_content,
                system_instruction=system_prompt,
                temperature=0,
            )
            return raw_response, extract_json(raw_response), None
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)  # brief backoff before retrying
    return "", None, last_error


def _process_page_single_pass(run: PromptRun, page_index: int, image_path: str) -> dict:
    """Original single-request behavior: one call, whatever labels the
    stored prompt asks for, all at full-sheet resolution."""
    raw_response, parsed, page_error = _call_vision_model(
        run, run.prompt.system_prompt or None, run.prompt.content, image_path
    )
    page_objects = extract_objects(parsed, page_index) if parsed is not None else []
    return {
        "objects": page_objects,
        "raw_response": raw_response,
        "error": page_error,
        "extra": {},
    }


def _process_page_two_pass(run: PromptRun, page_index: int, image_path: str, root: str) -> dict:
    """PASS 1: run the stored prompt on the full page, keep only its
    "elevation" and "elevation_callout" detections (cabinet/countertop
    guesses at full-sheet resolution, if any, are discarded — they always
    come from PASS 2 instead, where resolution is much higher).

    PASS 2: for every "elevation" box from pass 1, crop the ORIGINAL
    full-resolution page image to that box (with a small padding margin),
    send just that crop with the short cabinet/countertop-only prompt, and
    remap whatever boxes come back from crop-local 0..1000 coordinates into
    full-page 0..1000 coordinates before merging them in.
    """
    crops_dir = os.path.join(root, 'crops')
    os.makedirs(crops_dir, exist_ok=True)

    errors: list[str] = []

    raw_pass1, parsed_pass1, err1 = _call_vision_model(
        run, run.prompt.system_prompt or None, run.prompt.content, image_path
    )
    if err1:
        errors.append(f"Page {page_index + 1} pass 1: {err1}")

    pass1_objects = extract_objects(parsed_pass1, page_index) if parsed_pass1 is not None else []
    # cabinet/countertop are never trusted from the full-sheet pass — only
    # from the higher-resolution per-elevation crop in pass 2 below.
    full_page_objects = [o for o in pass1_objects if o["label"] in ("elevation", "elevation_callout")]

    with Image.open(image_path) as im:
        full_px_size = im.convert("RGB").size

    pass2_details = []
    elevations = [o for o in full_page_objects if o["label"] == "elevation"]

    for i, elevation_obj in enumerate(elevations, start=1):
        crop_filename = f"page_{page_index + 1:04d}_elevation_{i:02d}.png"
        crop_path = os.path.join(crops_dir, crop_filename)
        try:
            _, px_region, crop_full_px_size = crop_image_to_box(image_path, elevation_obj["box"], crop_path)
        except Exception as exc:
            errors.append(f"Page {page_index + 1} elevation {i} crop failed: {exc}")
            continue

        raw_pass2, parsed_pass2, err2 = _call_vision_model(
            run, CABINET_COUNTERTOP_SYSTEM_PROMPT, CABINET_COUNTERTOP_USER_PROMPT, crop_path
        )
        if err2:
            errors.append(f"Page {page_index + 1} elevation {i} pass 2: {err2}")

        crop_objects = extract_objects(parsed_pass2, page_index) if parsed_pass2 is not None else []
        crop_objects = [o for o in crop_objects if o["label"] in ("cabinet", "countertop")]

        # Draw the crop-local boxes on the crop itself (separate from the
        # full-page annotation) so each elevation's cabinet/countertop
        # detections can be inspected on their own, higher-resolution image.
        annotated_crop_path = None
        if crop_objects:
            annotated_crop_path = os.path.join(crops_dir, f"page_{page_index + 1:04d}_elevation_{i:02d}_annotated.png")
            try:
                annotate_image(crop_path, crop_objects, annotated_crop_path)
            except Exception:
                annotated_crop_path = None

        remapped_objects = []
        for obj in crop_objects:
            full_box = remap_crop_box_to_full(obj["box"], px_region, full_px_size)
            remapped = dict(obj)
            remapped["box"] = full_box
            remapped["left"], remapped["top"], remapped["right"], remapped["bottom"] = full_box
            remapped_objects.append(remapped)

        full_page_objects.extend(remapped_objects)
        pass2_details.append({
            "elevation_index": i,
            "elevation_box": elevation_obj["box"],
            "crop_image": _relative_artifact(crop_path, root),
            "annotated_crop_image": _relative_artifact(annotated_crop_path, root) if annotated_crop_path else None,
            "raw_response": raw_pass2,
            "cabinet_count": sum(1 for o in crop_objects if o["label"] == "cabinet"),
            "countertop_count": sum(1 for o in crop_objects if o["label"] == "countertop"),
            "objects_found": len(remapped_objects),
            "error": err2,
        })

    return {
        "objects": full_page_objects,
        "raw_response": raw_pass1,
        "error": "; ".join(errors) if errors else None,
        "extra": {"pass2": pass2_details},
    }


def _process_page_tiled(run: PromptRun, page_index: int, image_path: str, root: str) -> dict:
    """SAHI-style sliding-window tiling: cuts the ORIGINAL full-resolution page
    image into a grid of overlapping tile_size x tile_size tiles (compute_tile_grid),
    runs the full stored prompt on every tile independently — no viewport hierarchy,
    every tile is checked for all four labels — remaps each tile's local 0..1000
    boxes back into full-page coordinates, then fuses detections of the same object
    that showed up in more than one overlapping tile, whether as a true duplicate
    (same object seen whole twice) or as a fragment cut off by a tile boundary
    (stitched back together via fuse_tiled_objects; see tiling_utils.py).
    """
    tiles_dir = os.path.join(root, 'tiles')
    os.makedirs(tiles_dir, exist_ok=True)

    with Image.open(image_path) as im:
        full_px_size = im.convert("RGB").size
    width, height = full_px_size

    grid = compute_tile_grid(width, height, run.tile_size, run.tile_overlap_pct / 100.0)

    errors: list[str] = []
    raw_objects: list[dict] = []
    tile_details = []

    for i, px_box in enumerate(grid, start=1):
        tile_filename = f"page_{page_index + 1:04d}_tile_{i:03d}.png"
        tile_path = os.path.join(tiles_dir, tile_filename)
        crop_pixel_region(image_path, px_box, tile_path)

        raw_response, parsed, tile_error = _call_vision_model(
            run, run.prompt.system_prompt or None, run.prompt.content, tile_path
        )
        if tile_error:
            errors.append(f"Page {page_index + 1} tile {i}: {tile_error}")

        tile_objects = extract_objects(parsed, page_index) if parsed is not None else []
        for obj in tile_objects:
            # Flag this BEFORE remapping — "touches the edge" only means
            # something relative to the tile the model actually saw, not
            # relative to the full page.
            touches_edge = local_box_touches_edge(obj["box"])
            full_box = remap_crop_box_to_full(obj["box"], px_box, full_px_size)
            remapped = dict(obj)
            remapped["box"] = full_box
            remapped["left"], remapped["top"], remapped["right"], remapped["bottom"] = full_box
            remapped["touches_tile_edge"] = touches_edge
            raw_objects.append(remapped)

        tile_details.append({
            "tile_index": i,
            "tile_box_px": list(px_box),
            "tile_image": _relative_artifact(tile_path, root),
            "raw_response": raw_response,
            "objects_found": len(tile_objects),
            "error": tile_error,
        })

    # fuse_tiled_objects handles both true duplicates (high-IoU boxes from
    # overlapping tiles) and split fragments (low-IoU boxes that only
    # touch/nearly-touch, where at least one was flagged as cut off by its
    # own tile's edge) — see its docstring in tiling_utils.py.
    fused_objects = fuse_tiled_objects(
        raw_objects, iou_dup_threshold=TILE_FUSE_IOU_DUP, edge_gap_tolerance=TILE_FUSE_EDGE_GAP
    )

    return {
        "objects": fused_objects,
        "raw_response": "",
        "error": "; ".join(errors) if errors else None,
        "extra": {
            "tiles": tile_details,
            "tile_count": len(grid),
            "objects_before_fusion": len(raw_objects),
            "objects_after_fusion": len(fused_objects),
        },
    }


def process_prompt_run(run_id: int) -> None:
    run = PromptRun.query.get(run_id)
    if run is None:
        return
    run.status = PromptStatus.RUNNING
    run.started_at = datetime.now(UTC)
    run.error_message = None
    db.session.commit()

    try:
        root = run.artifacts_path
        images_dir = os.path.join(root, 'images')
        pages_json_dir = os.path.join(root, 'page_json')
        annotated_dir = os.path.join(root, 'annotated')
        os.makedirs(pages_json_dir, exist_ok=True)

        input_dir = os.path.join(run.prompt.file_path or '', 'inputs')
        if not os.path.isdir(input_dir):
            input_dir = run.prompt.file_path or ''
        image_paths = extract_images_from_folder(input_dir, dpi=run.dpi, output_dir=images_dir) if os.path.isdir(input_dir) else []
        page_results = []
        merged_objects = []
        total_counts = {key: 0 for key in CANONICAL_LABELS}
        errors = []

        if not image_paths:
            raise ValueError("No PDF or image files are available for this run.")

        for page_index, image_path in enumerate(image_paths):
            if run.workflow == 'locate_2pass':
                page_result = _process_page_two_pass(run, page_index, image_path, root)
            elif run.workflow == 'locate_tiled':
                page_result = _process_page_tiled(run, page_index, image_path, root)
            else:
                page_result = _process_page_single_pass(run, page_index, image_path)

            page_objects = page_result["objects"]
            raw_response = page_result["raw_response"]
            page_error = page_result["error"]
            if page_error:
                errors.append(f"Page {page_index + 1}: {page_error}")

            annotated_path = None
            if run.workflow in ('locate', 'locate_2pass', 'locate_tiled'):
                # For location runs, the extracted object list is the source of truth.
                page_counts = extract_counts(page_objects)
                merged_objects.extend(page_objects)
                annotated_path = os.path.join(annotated_dir, f"page_{page_index + 1:04d}.png")
                annotate_image(image_path, page_objects, annotated_path)
            else:
                # 'count' workflow: trust whatever counts the model reported directly.
                parsed_for_counts = extract_json(raw_response) if raw_response else None
                page_counts = extract_counts(parsed_for_counts) if parsed_for_counts is not None else {key: 0 for key in CANONICAL_LABELS}

            for label in CANONICAL_LABELS:
                total_counts[label] += int(page_counts.get(label, 0))

            page_json = {
                "image_index": page_index,
                "image": _relative_artifact(image_path, root),
                "annotated_image": _relative_artifact(annotated_path, root) if annotated_path else None,
                "counts": page_counts,
                "objects": page_objects,
                "raw_response": raw_response,
                "error": page_error,
                **page_result.get("extra", {}),
            }
            json_path = os.path.join(pages_json_dir, f"page_{page_index + 1:04d}.json")
            with open(json_path, 'w', encoding='utf-8') as fh:
                json.dump(page_json, fh, ensure_ascii=False, indent=2)
            page_json["json_file"] = _relative_artifact(json_path, root)
            page_results.append(page_json)

        expected = normalize_expected(json.loads(run.prompt.expected_json)) if run.prompt.expected_json else None
        comparison = build_comparison(total_counts, expected)

        location_score = None
        if run.workflow in ('locate', 'locate_2pass', 'locate_tiled') and run.prompt.expected_json:
            raw_expected = json.loads(run.prompt.expected_json)
            if is_bbox_ground_truth(raw_expected):
                try:
                    location_score = score_location(
                        merged_objects, raw_expected,
                        iou_threshold=current_app.config['SCORING_IOU_THRESHOLD'],
                    )
                except ValueError as exc:
                    # malformed ground-truth box — surface it, don't crash the whole run
                    errors.append(f"Ground truth scoring failed: {exc}")
                else:
                    if location_score['counts']['tp'] == 0 and merged_objects and raw_expected:
                        # Zero true positives despite non-empty predictions and ground truth
                        # almost always means the two sides aren't aligned, not that the model
                        # found nothing: either the "page" numbers in expected_json don't match
                        # image_index (0-based), or the bbox coordinates aren't in the same
                        # 0..1000 scale as the app's own detections.
                        errors.append(
                            "Location score shows zero true positives despite non-empty "
                            "predictions and ground truth — check that 'page' numbers in "
                            "expected_json match image_index (0-based) exactly, and that bbox "
                            "coordinates are normalized 0..1000 (not 0..1)."
                        )

        result = {
            "run_id": run.id,
            "created_at": datetime.now(UTC).isoformat(),
            "workflow": run.workflow,
            "model": run.model,
            "dpi": run.dpi,
            "system_prompt": run.prompt.system_prompt,
            "user_prompt": run.prompt.content,
            "files": [os.path.basename(path) for path in _input_files(run.prompt)],
            "pages": page_results,
            "counts": total_counts,
            "objects": merged_objects if run.workflow in ('locate', 'locate_2pass', 'locate_tiled') else [],
            "expected_summary": expected,
            "comparison": comparison,
            "location_score": location_score,
            "errors": errors,
        }
        result_path = os.path.join(root, 'result.json')
        with open(result_path, 'w', encoding='utf-8') as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)

        run.response = json.dumps(result, ensure_ascii=False, indent=2)
        run.result_json = run.response
        run.status = PromptStatus.COMPLETED
    except Exception as exc:
        run.error_message = str(exc)
        run.status = PromptStatus.FAILED
    finally:
        run.finished_at = datetime.now(UTC)
        db.session.commit()

def recompute_run_metrics(run_id: int) -> PromptRun:
    """Re-score a completed run's already-stored predictions against the prompt's
    current expected_json, without calling the model again. Overwrites comparison/
    location_score/errors in both result.json and PromptRun.result_json."""
    run = PromptRun.query.get_or_404(run_id)
    if run.status != PromptStatus.COMPLETED:
        raise ValueError("Can only recompute metrics for a completed run.")
    if not run.result_json:
        raise ValueError("This run has no stored result to recompute from.")

    result = json.loads(run.result_json)
    merged_objects = result.get("objects", [])
    total_counts = result.get("counts", {key: 0 for key in CANONICAL_LABELS})

    expected = normalize_expected(json.loads(run.prompt.expected_json)) if run.prompt.expected_json else None
    comparison = build_comparison(total_counts, expected)

    location_score = None
    errors = []
    if run.workflow in ('locate', 'locate_2pass', 'locate_tiled') and run.prompt.expected_json:
        raw_expected = json.loads(run.prompt.expected_json)
        if is_bbox_ground_truth(raw_expected):
            try:
                location_score = score_location(
                    merged_objects, raw_expected,
                    iou_threshold=current_app.config['SCORING_IOU_THRESHOLD'],
                )
            except ValueError as exc:
                errors.append(f"Ground truth scoring failed: {exc}")
            else:
                if location_score['counts']['tp'] == 0 and merged_objects and raw_expected:
                    errors.append(
                        "Location score shows zero true positives despite non-empty "
                        "predictions and ground truth — check that 'page' numbers in "
                        "expected_json match image_index (0-based) exactly, and that bbox "
                        "coordinates are normalized 0..1000 (not 0..1)."
                    )

    result["expected_summary"] = expected
    result["comparison"] = comparison
    result["location_score"] = location_score
    result["errors"] = errors

    result_path = os.path.join(run.artifacts_path, 'result.json')
    with open(result_path, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    run.response = json.dumps(result, ensure_ascii=False, indent=2)
    run.result_json = run.response
    db.session.commit()
    return run


def update_expected_json(prompt_id: int, expected_text: str | None, expected_file=None) -> Prompt:
    """Save a new expected_json on a Prompt only — no model call, no new run."""
    prompt = Prompt.query.get_or_404(prompt_id)
    prompt.expected_json = _parse_expected(expected_text, expected_file)
    db.session.commit()
    return prompt