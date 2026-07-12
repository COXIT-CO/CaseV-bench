"""Web-layer smoke check for Runs (ticket 05): the launch form renders, and a
launched Run's detail page shows its per-page Predictions and knob snapshot. The
adapter is stubbed via the ``app`` fixture, so no network is hit."""

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


def test_launch_run_persists_and_renders_predictions(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {SONNET: COUNT_JSON}

    prompt_id = _counting_prompt_id(client, engine)
    launched = client.post(
        "/runs",
        data={"prompt_id": prompt_id, "drawing_id": drawing_id, "models": [SONNET]},
        follow_redirects=False,
    )
    assert launched.status_code == 303

    detail = client.get(launched.headers["location"])
    assert detail.status_code == 200
    assert "done" in detail.text
    assert SONNET in detail.text
    assert "cabinets" in detail.text  # parsed counts shown
    assert "max_tokens" in detail.text  # knob snapshot shown


def _counting_prompt_id(client, engine) -> int:
    from sqlmodel import select

    from models.prompt import Prompt, Task

    with Session(engine) as session:
        return (
            session.exec(select(Prompt).where(Prompt.task == Task.counting)).first().id
        )
