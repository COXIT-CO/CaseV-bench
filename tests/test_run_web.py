"""Web-layer smoke check for Runs (tickets 05, 06): the launch form renders, a
launched Run returns immediately as ``queued``, and its detail page polls a status
endpoint that swaps in per-page Predictions and the knob snapshot once the background
run finishes. The adapter is stubbed via the ``app`` fixture, so no network is hit."""

import time

from sqlmodel import Session

from models.drawing import Drawing, Page

SONNET = "anthropic/claude-sonnet-4.5"
COUNT_JSON = (
    '{"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)


def _seed_drawing(engine) -> int:
    with Session(engine) as session:
        drawing = Drawing(name="sample")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        session.add(
            Page(
                drawing_id=drawing.id,
                page_number=1,
                image_path="/tmp/page_1.png",
                width_px=100,
                height_px=100,
            )
        )
        session.commit()
        return drawing.id


def test_launch_form_renders_prompt_drawing_and_models(client, engine):
    _seed_drawing(engine)
    page = client.get("/runs")
    assert page.status_code == 200
    # Seeded counting prompt, the drawing, and the model selection component all render.
    assert "default" in page.text
    assert "sample" in page.text
    assert 'name="models"' in page.text


def test_launch_run_is_async_then_polls_to_done(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    prompt_id = _counting_prompt_id(client, engine)
    launched = client.post(
        "/runs",
        data={"prompt_id": prompt_id, "drawing_id": drawing_id, "models": [SONNET]},
        follow_redirects=False,
    )
    # Launch returns immediately with a redirect to the (still-running) detail page.
    assert launched.status_code == 303
    location = launched.headers["location"]

    detail = client.get(location)
    assert detail.status_code == 200
    # The detail page wires up the HTMX poll of the status endpoint.
    assert f"{location}/status" in detail.text
    assert "max_tokens" in detail.text  # knob snapshot shown

    # Poll the status fragment like HTMX would until the background run finishes.
    status = _poll_status(client, f"{location}/status")
    assert "done" in status.text
    assert SONNET in status.text
    assert "cabinets" in status.text  # parsed counts swapped in


def _poll_status(client, url, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(url)
        assert status.status_code == 200
        if "done" in status.text or "failed" in status.text:
            return status
        time.sleep(0.02)
    raise AssertionError(f"run did not finish within {timeout}s")


def _counting_prompt_id(client, engine) -> int:
    from sqlmodel import select

    from models.prompt import Prompt, Task

    with Session(engine) as session:
        return (
            session.exec(select(Prompt).where(Prompt.task == Task.counting)).first().id
        )
