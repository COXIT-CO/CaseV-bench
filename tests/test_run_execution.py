"""Run-execution integration test (spec: Testing Decisions — the single primary
seam). With the OpenRouter adapter stubbed to canned responses, launching a Run through
the shared service against a temp SQLite DB persists the expected Results + Predictions,
and a response that fails to parse twice is recorded as a failure Prediction without
aborting the Run or affecting other models.
"""

from conftest import LOCATION_BOXES_JSON, seed_location_drawing
from sqlmodel import select

from core.models.results import LocationResult
from core.models.run import Prediction, PredictionStatus, Result, RunStatus
from core.services.run import RunService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"


def test_run_persists_results_and_predictions(
    session, stub_adapter, location_prompt, overlay_root
):
    drawing = seed_location_drawing(session, n_pages=2)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON, GPT: LOCATION_BOXES_JSON}

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        location_prompt.id, drawing.id, [SONNET, GPT]
    )

    assert run.status == RunStatus.done
    assert run.total_units == 4  # 2 models * 2 pages
    assert run.progress == 4
    # The per-run knobs are snapshotted on the Run and are what the adapter was called with.
    assert run.max_tokens == 4096
    assert stub_adapter.calls[0]["max_tokens"] == run.max_tokens
    assert stub_adapter.calls[0]["temperature"] == run.temperature
    # No prefill argument survives — the request is a single model-agnostic turn (ADR 0019).
    assert "prefill_json" not in stub_adapter.calls[0]

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
            assert pred.raw_content == LOCATION_BOXES_JSON
            parsed = LocationResult.model_validate_json(pred.parsed_json)
            assert [d.label for d in parsed.detections] == ["cabinet"]


def test_unparseable_model_records_failure_without_aborting(
    session, stub_adapter, location_prompt, overlay_root
):
    drawing = seed_location_drawing(session, n_pages=1)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON, GPT: "not json at all"}

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        location_prompt.id, drawing.id, [SONNET, GPT]
    )

    # A model failure does not abort the Run.
    assert run.status == RunStatus.done
    assert run.progress == 2

    by_model = {r.model: r for r in run.results}
    good = by_model[SONNET].predictions
    assert [p.status for p in good] == [PredictionStatus.ok]

    bad = by_model[GPT].predictions
    assert len(bad) == 1
    assert bad[0].status == PredictionStatus.error
    assert bad[0].raw_content == "not json at all"
    assert bad[0].parse_error
    assert bad[0].parsed_json is None

    # The failing page was retried exactly once before being recorded (2 calls total).
    gpt_calls = [c for c in stub_adapter.calls if c["model"] == GPT]
    assert len(gpt_calls) == 2


def test_raising_adapter_is_recorded_not_aborting(
    session, stub_adapter, location_prompt, overlay_root
):
    """A model whose OpenRouter call raises (e.g. a network error) is recorded as a
    failure Prediction and does not abort the Run or other models (spec: Runs 21)."""
    drawing = seed_location_drawing(session, n_pages=1)
    stub_adapter.responses = {SONNET: LOCATION_BOXES_JSON}

    real_send = stub_adapter.send_image_prompt

    def send(image_path, model, prompt, **kwargs):
        if model == GPT:
            raise RuntimeError("boom: openrouter unreachable")
        return real_send(image_path, model, prompt, **kwargs)

    stub_adapter.send_image_prompt = send

    run = RunService(session, stub_adapter, overlay_root=overlay_root).launch(
        location_prompt.id, drawing.id, [SONNET, GPT]
    )

    assert run.status == RunStatus.done
    by_model = {r.model: r for r in run.results}
    assert [p.status for p in by_model[SONNET].predictions] == [PredictionStatus.ok]
    bad = by_model[GPT].predictions
    assert len(bad) == 1
    assert bad[0].status == PredictionStatus.error
    assert "boom" in bad[0].parse_error
