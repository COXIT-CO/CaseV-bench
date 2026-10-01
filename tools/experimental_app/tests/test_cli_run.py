"""CLI-run integration test (ticket 13 — the CLI runs through the shared services).

With the OpenRouter adapter stubbed to canned responses, the CLI's own path — ingest a
real sample PDF into a Drawing, resolve the seeded prompt, launch through the shared
run-execution + persistence services — runs end-to-end against a temp SQLite and lands
the same Run / Results / Predictions a UI run would (there is no JSON-log path anymore).
"""

from pathlib import Path

import pytest
from conftest import LOCATION_BOXES_JSON
from sqlmodel import select

from core.cli import execute_cli_run, parse_args
from core.models.prompt import Prompt
from core.models.results import LocationResult
from core.models.run import Prediction, PredictionStatus, Result, RunStatus
from core.services.prompt import PromptService, seed_default_prompts
from core.services.run import RunKnobs

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"


def test_cli_takes_no_task_flag():
    """The task alias map and its ``--task`` flag are gone (ADR 0032): a plain invocation
    parses into a launch, and one still carrying the flag is rejected outright rather than
    quietly ignored."""
    assert parse_args(["--models", SONNET]).models == [SONNET]
    with pytest.raises(SystemExit):
        parse_args(["--task", "location"])


def test_cli_run_persists_run_results_predictions(
    session, stub_adapter, sample_pdf, tmp_path, overlay_root
):
    seed_default_prompts(session)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON, GPT: LOCATION_BOXES_JSON}

    run = execute_cli_run(
        session,
        stub_adapter,
        pdf_path=sample_pdf,
        name="sample",
        models=[SONNET, GPT],
        cache_root=tmp_path / "cache",
        overlay_root=overlay_root,
    )

    assert run.status == RunStatus.done
    # sample_pdf has 2 pages, 2 models -> 4 (model, page) units.
    assert run.total_units == 4
    assert run.progress == 4

    # Indistinguishable from a UI run: the same fixed knob snapshot the shared service
    # records, so a CLI Run and an equivalent UI Run are the same rows in the DB.
    knobs = RunKnobs()
    assert (
        run.dpi,
        run.downsample_px,
        run.max_tokens,
        run.temperature,
    ) == (
        knobs.dpi,
        knobs.downsample_px,
        knobs.max_tokens,
        knobs.temperature,
    )

    results = session.exec(select(Result).where(Result.run_id == run.id)).all()
    assert {r.model for r in results} == {SONNET, GPT}
    for result in results:
        preds = session.exec(
            select(Prediction)
            .where(Prediction.result_id == result.id)
            .order_by(Prediction.page_number)
        ).all()
        assert [p.page_number for p in preds] == [1, 2]
        for pred in preds:
            assert pred.status == PredictionStatus.ok
            detections = LocationResult.model_validate_json(pred.parsed_json).detections
            assert [d.label for d in detections] == ["cabinet"]


def test_cli_run_defaults_to_latest_prompt_version(
    session, stub_adapter, sample_pdf, tmp_path, overlay_root
):
    seed_default_prompts(session)
    # Append a second immutable version of the default location family.
    PromptService(session).edit("default", "find them, carefully")
    stub_adapter.default = LOCATION_BOXES_JSON

    run = execute_cli_run(
        session,
        stub_adapter,
        pdf_path=sample_pdf,
        name="sample",
        models=[SONNET],
        cache_root=tmp_path / "cache",
        overlay_root=overlay_root,
    )

    assert session.get(Prompt, run.prompt_id).version == 2


def test_cli_run_pins_requested_prompt_version(
    session, stub_adapter, sample_pdf, tmp_path, overlay_root
):
    seed_default_prompts(session)
    PromptService(session).edit("default", "find them, carefully")
    stub_adapter.default = LOCATION_BOXES_JSON

    run = execute_cli_run(
        session,
        stub_adapter,
        pdf_path=sample_pdf,
        name="sample",
        models=[SONNET],
        prompt_version=1,
        cache_root=tmp_path / "cache",
        overlay_root=overlay_root,
    )

    assert session.get(Prompt, run.prompt_id).version == 1


def test_cli_run_caches_pages_under_given_root(
    session, stub_adapter, sample_pdf, tmp_path, overlay_root
):
    seed_default_prompts(session)
    stub_adapter.default = LOCATION_BOXES_JSON
    cache_root = tmp_path / "cache"

    execute_cli_run(
        session,
        stub_adapter,
        pdf_path=sample_pdf,
        name="sample",
        models=[SONNET],
        cache_root=cache_root,
        overlay_root=overlay_root,
    )

    # The ingest wrote page images under the injected cache root, not the default.
    assert list(cache_root.rglob("*.png"))


def test_cli_run_location_persists_predictions_and_overlays(
    session, stub_adapter, sample_pdf, tmp_path, overlay_root
):
    """A location Run through the CLI parses boxes into location Predictions and renders
    overlays under the injected overlay root — never the repo default."""
    seed_default_prompts(session)
    stub_adapter.default = LOCATION_BOXES_JSON

    run = execute_cli_run(
        session,
        stub_adapter,
        pdf_path=sample_pdf,
        name="sample",
        models=[SONNET],
        cache_root=tmp_path / "cache",
        overlay_root=overlay_root,
    )

    assert run.status == RunStatus.done
    (result,) = session.exec(select(Result).where(Result.run_id == run.id)).all()
    preds = session.exec(
        select(Prediction)
        .where(Prediction.result_id == result.id)
        .order_by(Prediction.page_number)
    ).all()
    assert [p.page_number for p in preds] == [1, 2]
    for pred in preds:
        assert pred.status == PredictionStatus.ok
        detections = LocationResult.model_validate_json(pred.parsed_json).detections
        assert detections[0].label == "cabinet"
        # The overlay landed under the injected root, not the repo default data/overlays.
        assert pred.overlay_path
        assert Path(pred.overlay_path).is_relative_to(overlay_root)
        assert Path(pred.overlay_path).exists()
