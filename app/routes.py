from __future__ import annotations

import json
import os

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from werkzeug.security import safe_join

from app.models import Prompt, PromptRun
from app.services import (
    add_files_to_prompt,
    create_prompt,
    delete_input_file,
    delete_prompt,
    delete_prompt_run,
    list_input_files,
    rename_prompt,
    update_or_fork_prompt,
)

routes = Blueprint("routes", __name__)


def _sorted_runs(prompt: Prompt) -> list[PromptRun]:
    return sorted(prompt.runs, key=lambda r: r.id, reverse=True)


def _form_dpi() -> int:
    try:
        return max(72, min(int(request.form.get('dpi', 200)), 600))
    except (TypeError, ValueError):
        raise ValueError("DPI must be a number between 72 and 600.")


def _derive_name(user_prompt: str) -> str:
    return (user_prompt[:50] + '…') if len(user_prompt) > 50 else user_prompt


def _run_result(run: PromptRun | None) -> dict:
    if not run or not run.result_json:
        return {}
    try:
        return json.loads(run.result_json)
    except json.JSONDecodeError:
        return {}


@routes.route('/', methods=['GET'])
def index():
    prompts = Prompt.query.order_by(Prompt.created_at.desc()).all()
    prompt_rows = []
    for prompt in prompts:
        runs = _sorted_runs(prompt)
        prompt_rows.append({'prompt': prompt, 'latest_run': runs[0] if runs else None})
    return render_template('home.html', prompt_rows=prompt_rows)


@routes.route('/', methods=['POST'])
def create_prompt_route():
    user_prompt = request.form.get('user_prompt', '').strip()
    system_prompt = request.form.get('system_prompt', '').strip()
    model = request.form.get('model', '').strip() or 'default-model'
    workflow = request.form.get('workflow', 'count')
    files = request.files.getlist('file')
    if not user_prompt:
        flash('User prompt cannot be empty.', 'error')
        return redirect(url_for('routes.index'))
    try:
        prompt = create_prompt(
            name=_derive_name(user_prompt), user_prompt=user_prompt, system_prompt=system_prompt,
            model=model, workflow=workflow, dpi=_form_dpi(), files=files,
            expected_text=request.form.get('expected_json'),
            expected_file=request.files.get('expected_file'),
        )
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('routes.index'))
    return redirect(url_for('routes.prompt_detail', prompt_id=prompt.id))


@routes.route('/prompts/<int:prompt_id>', methods=['GET'])
def prompt_detail(prompt_id: int):
    prompt = Prompt.query.get_or_404(prompt_id)
    runs = _sorted_runs(prompt)
    run_id = request.args.get('run_id', type=int)
    selected_run = next((r for r in runs if r.id == run_id), None) if run_id else (runs[0] if runs else None)
    result = _run_result(selected_run)
    pages = result.get('pages', [])
    return render_template(
        'prompt_detail.html', prompt=prompt, runs=runs, selected_run=selected_run,
        result=result, pages=pages, input_files=list_input_files(prompt),
    )


@routes.route('/prompts/<int:prompt_id>/rename', methods=['POST'])
def prompt_rename(prompt_id: int):
    rename_prompt(prompt_id, request.form.get('name', ''))
    return redirect(url_for('routes.prompt_detail', prompt_id=prompt_id))


@routes.route('/prompts/<int:prompt_id>/update', methods=['POST'])
def prompt_update(prompt_id: int):
    user_prompt = request.form.get('user_prompt', '')
    try:
        prompt, forked = update_or_fork_prompt(
            prompt_id=prompt_id,
            user_prompt=user_prompt,
            system_prompt=request.form.get('system_prompt', ''),
            model=request.form.get('model', '').strip() or 'default-model',
            workflow=request.form.get('workflow', 'count'),
            dpi=_form_dpi(),
            expected_text=request.form.get('expected_json'),
            expected_file=request.files.get('expected_file'),
            fork_name=_derive_name(user_prompt.strip()),
        )
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('routes.prompt_detail', prompt_id=prompt_id))
    if forked:
        flash(f'Prompt text changed — created new project "{prompt.name}".', 'success')
    return redirect(url_for('routes.prompt_detail', prompt_id=prompt.id))


@routes.route('/prompts/<int:prompt_id>/files', methods=['POST'])
def prompt_add_files(prompt_id: int):
    try:
        add_files_to_prompt(prompt_id, request.files.getlist('file'))
        flash('Files added.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('routes.prompt_detail', prompt_id=prompt_id))


@routes.route('/prompts/<int:prompt_id>/files/<path:filename>/delete', methods=['POST'])
def prompt_delete_file(prompt_id: int, filename: str):
    try:
        delete_input_file(prompt_id, filename)
        flash('File deleted.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('routes.prompt_detail', prompt_id=prompt_id))


@routes.route('/prompts/<int:prompt_id>/delete', methods=['POST'])
def prompt_delete(prompt_id: int):
    delete_prompt(prompt_id)
    return redirect(url_for('routes.index'))


@routes.route('/runs/<int:run_id>/delete', methods=['POST'])
def prompt_run_delete(run_id: int):
    prompt_id = delete_prompt_run(run_id)
    return redirect(url_for('routes.prompt_detail', prompt_id=prompt_id))


@routes.route('/runs/<int:run_id>/status', methods=['GET'])
def prompt_run_status(run_id: int):
    run = PromptRun.query.get_or_404(run_id)
    result = _run_result(run)
    return {
        'id': run.id, 'status': run.status.value, 'response': run.response,
        'error_message': run.error_message,
        'started_at': run.started_at.strftime('%H:%M:%S') if run.started_at else None,
        'finished_at': run.finished_at.strftime('%H:%M:%S') if run.finished_at else None,
        'pages': result.get('pages', []),
        'comparison': result.get('comparison', []),
    }


@routes.route('/runs/<int:run_id>/download', methods=['GET'])
def prompt_run_download(run_id: int):
    run = PromptRun.query.get_or_404(run_id)
    result_path = os.path.join(run.artifacts_path or '', 'result.json')
    if not os.path.isfile(result_path):
        abort(404)
    return send_file(result_path, mimetype='application/json', as_attachment=True, download_name=f'run_{run.id}_result.json')


@routes.route('/runs/<int:run_id>/artifacts/<path:filename>', methods=['GET'])
def run_artifact(run_id: int, filename: str):
    run = PromptRun.query.get_or_404(run_id)
    if not run.artifacts_path:
        abort(404)
    full_path = safe_join(os.path.abspath(run.artifacts_path), filename)
    if not full_path or not os.path.isfile(full_path):
        abort(404)
    return send_file(full_path)