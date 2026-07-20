from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, UTC
from pathlib import Path
from threading import Thread

from flask import current_app
from werkzeug.utils import secure_filename

from app.annotate import annotate_image
from app.extensions import db
from app.models import Prompt, PromptRun, PromptStatus
from app.pdf_procesing import extract_images_from_folder
from app.result_parser import (
    CANONICAL_LABELS,
    build_comparison,
    extract_counts,
    extract_json,
    extract_objects,
    normalize_expected,
)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
WORKFLOWS = {'count', 'locate'}


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
                  expected_file=None) -> Prompt:
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
    run = _create_run(prompt.id, model, workflow, dpi)
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
                          expected_text: str | None = None, expected_file=None) -> Prompt:
    original = Prompt.query.get_or_404(prompt_id)
    user_prompt = user_prompt.strip()
    system_prompt = system_prompt.strip()
    if not user_prompt:
        raise ValueError("User prompt cannot be empty.")
    expected_json = _parse_expected(expected_text, expected_file)
    # Update configuration in place: runs remain the immutable history.
    original.content = user_prompt
    original.system_prompt = system_prompt
    if expected_text is not None or (expected_file and getattr(expected_file, 'filename', '')):
        original.expected_json = expected_json
    run = _create_run(original.id, model, workflow, dpi)
    db.session.commit()
    _start_processing_thread(run.id)
    return original


def _create_run(prompt_id: int, model: str, workflow: str, dpi: int) -> PromptRun:
    dpi = max(72, min(int(dpi), 600))
    run = PromptRun(prompt_id=prompt_id, model=model, workflow=workflow, dpi=dpi, status=PromptStatus.PENDING)
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
            message_content = [
                {"type": "text", "text": run.prompt.content},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_image_to_base64(image_path)}"}},
            ]
            raw_response = ""
            parsed = None
            page_error = None
            try:
                raw_response = current_app.extensions["openrouter_client"].generate_response(
                    model=run.model,
                    message_content=message_content,
                    system_instruction=run.prompt.system_prompt or None,
                    temperature=0,
                )
                parsed = extract_json(raw_response)
            except Exception as exc:
                page_error = str(exc)
                errors.append(f"Page {page_index + 1}: {exc}")

            page_objects = extract_objects(parsed, page_index) if parsed is not None else []
            page_counts = extract_counts(parsed) if parsed is not None else {key: 0 for key in CANONICAL_LABELS}
            if run.workflow == 'locate':
                # For location runs, object count is the source of truth.
                page_counts = extract_counts(page_objects)
                merged_objects.extend(page_objects)
                annotated_path = os.path.join(annotated_dir, f"page_{page_index + 1:04d}.png")
                annotate_image(image_path, page_objects, annotated_path)
            else:
                annotated_path = None

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
            }
            json_path = os.path.join(pages_json_dir, f"page_{page_index + 1:04d}.json")
            with open(json_path, 'w', encoding='utf-8') as fh:
                json.dump(page_json, fh, ensure_ascii=False, indent=2)
            page_json["json_file"] = _relative_artifact(json_path, root)
            page_results.append(page_json)

        expected = normalize_expected(json.loads(run.prompt.expected_json)) if run.prompt.expected_json else None
        comparison = build_comparison(total_counts, expected)
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
            "objects": merged_objects if run.workflow == 'locate' else [],
            "expected_summary": expected,
            "comparison": comparison,
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
