"""Run-execution integration test (spec: Testing Decisions — the single primary
seam). With the OpenRouter adapter stubbed to canned counting responses, launching
a Run through the shared service against a temp SQLite DB persists the expected
Results + Predictions, and a response that fails to parse twice is recorded as a
failure Prediction without aborting the Run or affecting other models.
"""

import json

from conftest import seed_page_images
from sqlmodel import select

from core.models.drawing import Drawing, Page
from core.models.prompt import Task
from core.models.run import Prediction, PredictionStatus, Result, RunStatus
from core.services.prompt import PromptService
from core.services.run import RunService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"

COUNT_JSON = (
    '{"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)


def _seed_drawing(session, tmp_path, n_pages: int) -> Drawing:
    drawing = Drawing(name="sample")
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    # Pages point at real native rasters so render-on-demand has an image to hand the Model.
    images = seed_page_images(tmp_path / str(drawing.id), n_pages)
    for page_number, image in enumerate(images, start=1):
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=page_number,
                image_path=str(image),
                width_px=100,
                height_px=100,
            )
        )
    session.commit()
    session.refresh(drawing)
    return drawing


def _seed_prompt(session):
    return PromptService(session).create(Task.counting, "default", "count them")


def test_run_persists_results_and_predictions(session, stub_adapter, tmp_path):
    drawing = _seed_drawing(session, tmp_path, n_pages=2)
    prompt = _seed_prompt(session)
    stub_adapter.responses = {SONNET: COUNT_JSON, GPT: COUNT_JSON}

    run = RunService(session, stub_adapter).launch(
        Task.counting, prompt.id, drawing.id, [SONNET, GPT]
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
            assert pred.raw_content == COUNT_JSON
            assert json.loads(pred.parsed_json)["cabinets"] == 3


def test_unparseable_model_records_failure_without_aborting(
    session, stub_adapter, tmp_path
):
    drawing = _seed_drawing(session, tmp_path, n_pages=1)
    prompt = _seed_prompt(session)
    stub_adapter.responses = {SONNET: COUNT_JSON, GPT: "not json at all"}

    run = RunService(session, stub_adapter).launch(
        Task.counting, prompt.id, drawing.id, [SONNET, GPT]
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


def test_raising_adapter_is_recorded_not_aborting(session, stub_adapter, tmp_path):
    """A model whose OpenRouter call raises (e.g. a network error) is recorded as a
    failure Prediction and does not abort the Run or other models (spec: Runs 21)."""
    drawing = _seed_drawing(session, tmp_path, n_pages=1)
    prompt = _seed_prompt(session)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    real_send = stub_adapter.send_image_prompt

    def send(image_path, model, prompt, **kwargs):
        if model == GPT:
            raise RuntimeError("boom: openrouter unreachable")
        return real_send(image_path, model, prompt, **kwargs)

    stub_adapter.send_image_prompt = send

    run = RunService(session, stub_adapter).launch(
        Task.counting, prompt.id, drawing.id, [SONNET, GPT]
    )

    assert run.status == RunStatus.done
    by_model = {r.model: r for r in run.results}
    assert [p.status for p in by_model[SONNET].predictions] == [PredictionStatus.ok]
    bad = by_model[GPT].predictions
    assert len(bad) == 1
    assert bad[0].status == PredictionStatus.error
    assert "boom" in bad[0].parse_error
