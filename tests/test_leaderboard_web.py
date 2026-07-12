"""Web-layer check for the Leaderboard + drill-down (ticket 08). With the adapter
stubbed via the ``app`` fixture, a launched Run then entered GT renders a ranked board;
clicking a row reaches the Result's per-page Predictions + raw JSON; a Drawing with no
GT shows its Results as unscored, distinct from a zero score."""

import time

from sqlmodel import Session, select

from models.drawing import Drawing, Page
from models.prompt import Prompt, Task

ACCURATE = "anthropic/claude-sonnet-4.5"
SLOPPY = "openai/gpt-5-mini"
ACCURATE_JSON = (
    '{"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)
SLOPPY_JSON = (
    '{"cabinets": 9, "countertops": 1, "elevations": 2, "elevation_callout": 0}'
)
GT = {"cabinets": 3, "countertops": 1, "elevations": 2, "elevation_callout": 0}


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


def _counting_prompt_id(engine) -> int:
    with Session(engine) as session:
        return (
            session.exec(select(Prompt).where(Prompt.task == Task.counting)).first().id
        )


def _launch_and_wait(client, engine, drawing_id, prompt_id):
    launched = client.post(
        "/runs",
        data={
            "prompt_id": prompt_id,
            "drawing_id": drawing_id,
            "models": [ACCURATE, SLOPPY],
        },
        follow_redirects=False,
    )
    location = launched.headers["location"]
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if "done" in client.get(f"{location}/status").text:
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_leaderboard_ranks_and_drills_into_result(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))

    # Enter GT so the board can score.
    client.post(f"/drawings/{drawing_id}/counting-ground-truth", data=GT)

    board = client.get(f"/leaderboard?drawing_id={drawing_id}")
    assert board.status_code == 200
    # Accurate model (0 error) is ranked above the sloppy one (its row appears first).
    assert board.text.index(ACCURATE) < board.text.index(SLOPPY)
    assert "/ 4" in board.text  # exact-match column rendered

    # Drill into the accurate Result via its linked drill-down page.
    with Session(engine) as session:
        from models.run import Result

        result_id = (
            session.exec(select(Result).where(Result.model == ACCURATE)).first().id
        )
    detail = client.get(f"/results/{result_id}")
    assert detail.status_code == 200
    assert "cabinets" in detail.text  # per-page parsed counts
    assert "Total absolute error" in detail.text
    assert "Raw model output" in detail.text  # raw JSON section present


def test_leaderboard_shows_unscored_without_ground_truth(client, engine, stub_adapter):
    drawing_id = _seed_drawing(engine)
    stub_adapter.responses = {ACCURATE: ACCURATE_JSON, SLOPPY: SLOPPY_JSON}
    _launch_and_wait(client, engine, drawing_id, _counting_prompt_id(engine))

    board = client.get(f"/leaderboard?drawing_id={drawing_id}")
    assert board.status_code == 200
    # No GT entered → unscored, and the word distinguishes it from a zero score.
    assert "unscored" in board.text.lower()
