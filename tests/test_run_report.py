"""Downloadable standalone HTML run report — endpoint contract, self-contained output, layout
invariants, and the Operating-point stamp its frozen metrics are read against (ADR 0026,
spec-run-report; spec-scorer-library-implementation, "Stating the operating point").

``GET /api/runs/{id}/report`` assembles one location Run's per-model Results into a single
self-contained HTML file streamed as a download. Results/Predictions are seeded directly so the
report's scores are exact without a live model, and overlay PNGs are written to disk so the
report can base64-inline them.
"""

import re
from importlib.metadata import version

from PIL import Image
from sqlmodel import Session

from core.models.drawing import Drawing, Page
from core.models.location_ground_truth import LocationGroundTruth
from core.models.prompt import Prompt, Task
from core.models.results import BoundingBox, LocationDetection, LocationResult
from core.models.run import Prediction, PredictionStatus, Result, Run, RunStatus
from core.models.score import Score
from core.services.scoring import LOCATION_IOU_THRESHOLD, score_location

GOOD = "anthropic/claude-sonnet-4.5"
SALVAGED = "openai/gpt-5-mini"
FAILED = "meta/llama-3"


def _boxes(*coords, label="cabinet") -> str:
    """A ``LocationResult`` JSON string of the given ``(x_min, y_min, x_max, y_max)`` boxes."""
    return LocationResult(
        detections=[
            LocationDetection(
                label=label,
                bounding_box=BoundingBox(
                    x_min=c[0], y_min=c[1], x_max=c[2], y_max=c[3]
                ),
            )
            for c in coords
        ]
    ).model_dump_json()


def _overlay(tmp_path, name) -> str:
    """Write a real overlay PNG and return its path (the report inlines it)."""
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (80, 60), "white").save(path)
    return str(path)


def _run(session, drawing_id, prompt_id, *, task=Task.location, status=RunStatus.done):
    run = Run(
        task=task,
        prompt_id=prompt_id,
        drawing_id=drawing_id,
        status=status,
        progress=1,
        total_units=1,
        dpi=200,
        downsample_px=1568,
        max_tokens=1024,
        temperature=0.0,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _seed_report_run(
    engine, tmp_path, *, task=Task.location, status=RunStatus.done, with_gt=True
) -> int:
    """A two-page location Run over three models: a perfect model (F1 1.0), a salvaged model
    (one matching + one spurious box, ``error`` status → F1 0.67), and a wholly-failed model
    (no boxes → F1 0). GT sits on page 1 only, so page 2 is visual-only. Returns the Run id.
    """
    with Session(engine) as session:
        drawing = Drawing(name="Kitchen Plan #7")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)

        pages = []
        for n in (1, 2):
            image_path = tmp_path / f"page_{n}.png"
            Image.new("RGB", (80, 60), "white").save(image_path)
            page = Page(
                drawing_id=drawing.id,
                page_number=n,
                image_path=str(image_path),
                width_px=80,
                height_px=60,
            )
            session.add(page)
            pages.append(page)
        session.commit()
        for page in pages:
            session.refresh(page)

        if with_gt:
            # One GT box on page 1 only.
            session.add(
                LocationGroundTruth(
                    page_id=pages[0].id,
                    label="cabinet",
                    x_min=0.0,
                    y_min=0.0,
                    x_max=0.5,
                    y_max=0.5,
                )
            )
            session.commit()

        prompt = Prompt(task=Task.location, family="boxes", version=3, text="find")
        session.add(prompt)
        session.commit()
        session.refresh(prompt)

        run = _run(session, drawing.id, prompt.id, task=task, status=status)

        # Perfect model: exact match on p1, a spare box on the GT-less p2.
        good = Result(run_id=run.id, model=GOOD)
        session.add(good)
        session.commit()
        session.refresh(good)
        session.add(
            Prediction(
                result_id=good.id,
                page_id=pages[0].id,
                page_number=1,
                status=PredictionStatus.ok,
                parsed_json=_boxes((0.0, 0.0, 0.5, 0.5)),
                overlay_path=_overlay(tmp_path, "good_p1"),
            )
        )
        session.add(
            Prediction(
                result_id=good.id,
                page_id=pages[1].id,
                page_number=2,
                status=PredictionStatus.ok,
                parsed_json=_boxes((0.1, 0.1, 0.2, 0.2)),
                overlay_path=_overlay(tmp_path, "good_p2"),
            )
        )

        # Salvaged model: one matching + one spurious box, error status (→ salvaged badge).
        salv = Result(run_id=run.id, model=SALVAGED)
        session.add(salv)
        session.commit()
        session.refresh(salv)
        session.add(
            Prediction(
                result_id=salv.id,
                page_id=pages[0].id,
                page_number=1,
                status=PredictionStatus.error,
                parsed_json=_boxes((0.0, 0.0, 0.5, 0.5), (0.6, 0.0, 0.7, 0.1)),
                parse_error="response was truncated; salvaged intact array elements",
                overlay_path=_overlay(tmp_path, "salv_p1"),
            )
        )
        session.add(
            Prediction(
                result_id=salv.id,
                page_id=pages[1].id,
                page_number=2,
                status=PredictionStatus.ok,
                parsed_json=_boxes((0.3, 0.3, 0.4, 0.4)),
                overlay_path=_overlay(tmp_path, "salv_p2"),
            )
        )

        # Failed model: no parsed boxes, no overlay, on either page.
        failed = Result(run_id=run.id, model=FAILED)
        session.add(failed)
        session.commit()
        session.refresh(failed)
        for page in pages:
            session.add(
                Prediction(
                    result_id=failed.id,
                    page_id=page.id,
                    page_number=page.page_number,
                    status=PredictionStatus.error,
                    parse_error="response was not valid JSON",
                )
            )
        session.commit()
        return run.id


# --- endpoint contract ---------------------------------------------------------------


def test_report_returns_html_download_for_terminal_location_run(
    client, engine, tmp_path
):
    run_id = _seed_report_run(engine, tmp_path)
    resp = client.get(f"/api/runs/{run_id}/report")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    disposition = resp.headers["content-disposition"]
    assert "attachment" in disposition
    # Filename: casev-report-run{id}-{drawing-slug}-{YYYYMMDD}.html
    assert re.search(
        rf'filename="casev-report-run{run_id}-kitchen-plan-7-\d{{8}}\.html"',
        disposition,
    )


def test_report_400s_for_non_terminal_run(client, engine, tmp_path):
    run_id = _seed_report_run(engine, tmp_path, status=RunStatus.running)
    resp = client.get(f"/api/runs/{run_id}/report")
    assert resp.status_code == 400


def test_report_404s_for_unknown_run(client):
    assert client.get("/api/runs/9999/report").status_code == 404


# --- self-contained output -----------------------------------------------------------


def test_report_is_self_contained(client, engine, tmp_path):
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    # Every image is a data: URI; no network asset references at all.
    assert "data:image/png;base64," in html
    assert "http://" not in html
    assert "https://" not in html
    # No external stylesheet; and the only script is inline classic JS (the ticket-03
    # lightbox) — never an external <script src> or an ES module.
    assert "<link" not in html
    assert "<script src" not in html
    assert 'type="module"' not in html


# --- image lightbox (ticket 03) ------------------------------------------------------


def test_report_has_file_safe_inline_lightbox(client, engine, tmp_path):
    """The report ships a single inline classic-JS lightbox: clickable overlays, a lightbox
    container to project the enlarged image into, and no file://-hostile constructs (no ES
    modules, no fetch, no external script src)."""
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    # An inline script is present, and it is file://-safe classic JS.
    assert "<script>" in html
    assert "<script src" not in html
    assert 'type="module"' not in html
    assert "fetch(" not in html
    assert "import " not in html

    # Overlay images are marked as zoom targets, and there is a lightbox to open.
    assert "zoomable" in html
    assert 'id="lightbox"' in html
    # Esc and backdrop close are wired up.
    assert "Escape" in html
    # The opened image can be magnified further (click-to-zoom around the point).
    assert "zoomed" in html


def test_report_images_render_without_javascript(client, engine, tmp_path):
    """The lightbox is enhancement only: every overlay is a plain <img> with a data: URI, so
    the report is fully readable with JavaScript disabled."""
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    # The images themselves carry the inlined pixels — no JS needed to see them.
    assert 'src="data:image/png;base64,' in html
    assert "<img " in html


# --- summary block -------------------------------------------------------------------


def test_report_summary_has_verdict_ranking_and_coverage(client, engine, tmp_path):
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    # Headline verdict names the best model and its F1.
    assert "Best model:" in html
    assert GOOD in html
    # Coverage note: GT is on 1 of the 2 pages.
    assert "Scored on 1 of 2 page(s)" in html

    # Ranking is best-first: perfect model precedes salvaged, which precedes failed.
    assert html.index(GOOD) < html.index(SALVAGED) < html.index(FAILED)
    # The winner is starred.
    assert "★" in html


def test_report_badges_salvaged_and_failed_models(client, engine, tmp_path):
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text
    assert ">salvaged</span>" in html
    # The wholly-failed model contributes a failed cell placeholder and ranks last at F1 0.
    assert "failed — no output" in html
    assert ">no detections</span>" in html


# --- page-major grid -----------------------------------------------------------------


def test_report_page_grid_gt_and_visual_only(client, engine, tmp_path):
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    # The colour legend heads the file (before the first page section).
    assert "Object types" in html
    assert html.index("Object types") < html.index("Page 1")

    # Page 1 has GT → a Ground truth column + per-page F1 for each model.
    assert "Ground truth" in html
    assert "F1 1.00" in html  # the perfect model's per-page F1 on the GT page

    # Page 2 lacks GT → flagged visual-only, no per-page F1 there.
    assert "visual-only" in html


def test_report_without_ground_truth_is_visual_only(client, engine, tmp_path):
    run_id = _seed_report_run(engine, tmp_path, with_gt=False)
    html = client.get(f"/api/runs/{run_id}/report").text

    # A drawing with no GT at all yields a verdict-less, purely visual report.
    assert "No ground truth" in html
    assert "Scored on 0 of 2 page(s)" in html
    # Every model still appears as a column.
    for model in (GOOD, SALVAGED, FAILED):
        assert model in html


# --- operating-point stamp (scorer-library adoption, ticket 03) ----------------------


def test_report_stamps_iou_threshold_and_scorer_version_by_the_verdict(
    client, engine, tmp_path
):
    """A downloaded report outlives the code that made it, so it states the Operating point its
    metrics were computed under — the IoU threshold and the scorer version — where a reader
    meets the headline metric, not in a footer."""
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    assert f"IoU ≥ {LOCATION_IOU_THRESHOLD:.2f}" in html
    assert f"location-scorer v{version('location-scorer')}" in html

    # Near the headline verdict: after it, and well before the first page section.
    assert html.index("Best model:") < html.index("IoU ≥") < html.index("Page 1")


def test_report_scorer_version_is_read_from_installed_package_metadata(
    client, engine, tmp_path, monkeypatch
):
    """The version is whatever the installed distribution reports, not a literal in the
    renderer — a hardcoded string would keep printing the old version after a bump."""
    monkeypatch.setattr(
        "core.services.report.scorer_version", lambda: "9.9.9-from-metadata"
    )
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    assert "location-scorer v9.9.9-from-metadata" in html


def test_report_stamp_is_in_the_downloaded_bytes(client, engine, tmp_path):
    """The stamp is markup in the attachment itself, so it survives on a file:// copy that has
    no access to the running application (self-containment at large: test_report_is_self_
    contained)."""
    run_id = _seed_report_run(engine, tmp_path)
    resp = client.get(f"/api/runs/{run_id}/report")

    assert "attachment" in resp.headers["content-disposition"]
    body = resp.content.decode("utf-8")
    assert "IoU ≥" in body and "location-scorer v" in body


def test_report_stamps_the_threshold_the_scorer_echoed_back(
    client, engine, tmp_path, monkeypatch
):
    """The stamped threshold is the one the library echoed back with the rates it produced, not
    a constant re-read at render time: score the same Run at a different operating point and the
    stamp moves with it. A stamp read from a constant would keep printing 0.50 here."""

    def scored_at_0_75(predicted, gt, iou_threshold=0.75):
        return score_location(predicted, gt, iou_threshold)

    monkeypatch.setattr("core.services.report.score_location", scored_at_0_75)
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    assert "IoU ≥ 0.75" in html
    assert f"IoU ≥ {LOCATION_IOU_THRESHOLD:.2f}" not in html


def test_report_without_ground_truth_has_no_operating_point(client, engine, tmp_path):
    """Nothing scored means no Operating point exists to state, and no metrics for it to
    qualify — the line is omitted rather than invented."""
    run_id = _seed_report_run(engine, tmp_path, with_gt=False)
    html = client.get(f"/api/runs/{run_id}/report").text

    assert "IoU ≥" not in html
    assert "location-scorer v" not in html


def test_report_drops_the_version_clause_when_metadata_is_absent(
    client, engine, tmp_path, monkeypatch
):
    """A path/PYTHONPATH install has no distribution metadata. The report then says nothing
    about the version rather than inventing one — and still states the threshold, and still
    downloads."""
    monkeypatch.setattr("core.services.report.scorer_version", lambda: None)
    run_id = _seed_report_run(engine, tmp_path)
    html = client.get(f"/api/runs/{run_id}/report").text

    assert f"IoU ≥ {LOCATION_IOU_THRESHOLD:.2f}" in html
    assert "location-scorer" not in html


def test_score_table_gains_no_threshold_column():
    """Live Scores recompute on read, so they are current-code by construction; provenance
    belongs only on the frozen artifact. Asserted as the exact column set rather than a
    "threshold" substring scan, so any provenance column — whatever it gets called — fails
    here."""
    assert set(Score.model_fields) == {
        "id",
        "result_id",
        "total_absolute_error",
        "exact_match_count",
        "precision",
        "recall",
        "f1",
        "per_label_json",
    }


def _seed_single_model_run(engine, tmp_path, *, parsed_json, status) -> int:
    """A one-page, one-model, one-GT-box location Run whose single Prediction carries the
    given ``parsed_json`` + ``status``. Lets a test isolate one badge condition. Returns the
    Run id."""
    with Session(engine) as session:
        drawing = Drawing(name="plan")
        session.add(drawing)
        session.commit()
        session.refresh(drawing)
        image_path = tmp_path / "p1.png"
        Image.new("RGB", (80, 60), "white").save(image_path)
        page = Page(
            drawing_id=drawing.id,
            page_number=1,
            image_path=str(image_path),
            width_px=80,
            height_px=60,
        )
        session.add(page)
        session.commit()
        session.refresh(page)
        session.add(
            LocationGroundTruth(
                page_id=page.id,
                label="cabinet",
                x_min=0.0,
                y_min=0.0,
                x_max=0.5,
                y_max=0.5,
            )
        )
        prompt = Prompt(task=Task.location, family="boxes", version=1, text="f")
        session.add(prompt)
        session.commit()
        session.refresh(prompt)
        run = _run(session, drawing.id, prompt.id)
        result = Result(run_id=run.id, model=GOOD)
        session.add(result)
        session.commit()
        session.refresh(result)
        session.add(
            Prediction(
                result_id=result.id,
                page_id=page.id,
                page_number=1,
                status=status,
                parsed_json=parsed_json,
                overlay_path=_overlay(tmp_path, "p1_ov"),
            )
        )
        session.commit()
        return run.id


def test_zero_f1_model_with_detections_is_not_badged_no_detections(
    client, engine, tmp_path
):
    """A model that *did* emit boxes which all missed GT scores F1 0 but has made detections —
    the "no detections" badge is gated on producing nothing, not on F1 0 (spec: a failed model
    ranks last, not any zero-F1 one)."""
    # One spurious box (no overlap with the GT box) → F1 0, but a real detection.
    run_id = _seed_single_model_run(
        engine,
        tmp_path,
        parsed_json=_boxes((0.8, 0.8, 0.95, 0.95)),
        status=PredictionStatus.ok,
    )
    html = client.get(f"/api/runs/{run_id}/report").text
    assert ">no detections</span>" not in html


def test_errored_empty_prediction_is_not_badged_salvaged(client, engine, tmp_path):
    """The salvaged badge tracks ADR 0027's "has usable detections" gate: an ``error`` whose
    recovered box list is empty contributed nothing, so it is not badged salvaged."""
    run_id = _seed_single_model_run(
        engine,
        tmp_path,
        parsed_json=LocationResult(detections=[]).model_dump_json(),
        status=PredictionStatus.error,
    )
    html = client.get(f"/api/runs/{run_id}/report").text
    assert ">salvaged</span>" not in html
