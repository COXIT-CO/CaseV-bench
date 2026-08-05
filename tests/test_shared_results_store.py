"""The Lab's write to the shared results store (scope 9, ticket 05).

Completing a Run appends one ``experiments.run_results`` row per scored ``(model,
document)`` to a Postgres nobody else on the team can alter — the Lab is that table's first
real writer. There is no client library to pin (ADR 0038 amendment): the Lab assembles its
own row against the columns declared in ``src/results_store/models.py`` on ``main``.

Two seams, and the split is the point:

- ``ResultsStoreService.rows_for_run`` — the mapping. Pure with respect to the store: it
  reads the Lab's own SQLite and returns rows, so every claim the shared table makes about a
  Lab number is testable here with no Postgres anywhere. **The transposition test lives
  here**: nothing downstream checks that ``tp``/``fp``/``fn`` agree with the
  ``scorer_output`` beside them, so a swapped pair would produce a permanently wrong row
  that looks entirely normal.
- ``SharedResultsStore.insert`` — the wire. Stubbed by a recorder, because what a real
  ``INSERT`` proves is Postgres's behaviour, not ours.

The fixture is deliberately **asymmetric** — ``tp=1, fp=2, fn=3`` with three distinct rates
— so any two of the six metric columns swapping places fails a test.
"""

import json
from pathlib import Path

import pymupdf
import pytest
from conftest import LOCATION_PAGE_PT, seed_location_drawing
from sqlmodel import Session, select

from core.models.run import Run, RunStatus
from core.models.score import Score
from core.services.results_store import ResultsStoreConfig, ResultsStoreService
from core.services.run import BackgroundRunner, RunService
from core.services.scoring import ScoringService

SONNET = "anthropic/claude-sonnet-4.5"
GPT = "openai/gpt-5-mini"

AUTHOR = "achumak"

# Four ground-truth objects on a single 100pt-square page (conftest's LOCATION_PAGE_PT), so
# an absolute box divides straight through to its normalized corners.
GT_OBJECTS = {
    "objects": [
        # Predicted exactly — the one true positive.
        {
            "id": "a",
            "category": "cabinet",
            "page": 1,
            "bbox": {"x": 10, "y": 10, "width": 30, "height": 30},
        },
        # Predicted loosely enough to miss at IoU 0.5 (the near miss, best_iou 0.4).
        {
            "id": "b",
            "category": "cabinet",
            "page": 1,
            "bbox": {"x": 50, "y": 10, "width": 20, "height": 20},
        },
        # Never predicted at all.
        {
            "id": "c",
            "category": "countertop",
            "page": 1,
            "bbox": {"x": 10, "y": 50, "width": 20, "height": 20},
        },
        {
            "id": "d",
            "category": "countertop",
            "page": 1,
            "bbox": {"x": 50, "y": 50, "width": 20, "height": 20},
        },
    ]
}


def _detection(label, x_min, y_min, x_max, y_max):
    return {
        "label": label,
        "bounding_box": {
            "x_min": x_min,
            "y_min": y_min,
            "x_max": x_max,
            "y_max": y_max,
        },
    }


# One hit, one near miss, one invention: tp=1, fp=2, fn=3 against GT_OBJECTS, and no two of
# precision (1/3), recall (1/4) and f1 (2/7) equal either.
PREDICTED_JSON = json.dumps(
    [
        _detection("cabinet", 0.1, 0.1, 0.4, 0.4),
        _detection("cabinet", 0.5, 0.1, 0.58, 0.3),  # IoU 0.4 against GT "b"
        _detection("countertop", 0.75, 0.05, 0.85, 0.15),
    ]
)

EXPECTED_TP, EXPECTED_FP, EXPECTED_FN = 1, 2, 3
EXPECTED_PRECISION = 1 / 3
EXPECTED_RECALL = 1 / 4
EXPECTED_F1 = 2 / 7


class RecordingStore:
    """A stand-in for the shared Postgres that keeps what it was handed. ``fail`` makes
    every write raise, which is how the outage case is exercised without an outage."""

    def __init__(self, fail: bool = False):
        self.rows = []
        self.inserts = 0
        self.fail = fail

    def insert(self, rows):
        self.inserts += 1
        if self.fail:
            raise RuntimeError("shared store is unreachable")
        self.rows.extend(rows)


@pytest.fixture
def store():
    return RecordingStore()


def seed_pdf_drawing(session: Session, name: str = "elevation-01"):
    """A location Drawing that was ingested from a PDF — the corpus case. Ingestion renames
    the retained upload to ``source.pdf`` and keeps only the *stem* as the Drawing's name, so
    a real (if blank) PDF has to be on disk for the published ``document_id`` to be anything
    but a guess. It also keeps render-on-demand honest: the Run really re-rasterizes it.
    """
    drawing = seed_location_drawing(session, name=name)
    page_dir = (
        Path(session.get_bind().url.database).parent / "drawings" / str(drawing.id)
    )
    source = page_dir / "source.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=LOCATION_PAGE_PT, height=LOCATION_PAGE_PT)
        doc.save(source)
    drawing.source_path = str(source)
    session.add(drawing)
    session.commit()
    session.refresh(drawing)
    return drawing


def _launch(
    session,
    stub_adapter,
    prompt,
    overlay_root,
    drawing,
    models=(SONNET,),
    responses=None,
):
    """A finished single-page location Run — the state a completed Lab Run leaves behind,
    which is the only state this ticket publishes from. ``responses`` overrides what each
    model answers, for the tests that need something other than a good prediction."""
    stub_adapter.responses = responses or {model: PREDICTED_JSON for model in models}
    service = RunService(session, stub_adapter, overlay_root=overlay_root)
    return service.launch(prompt.id, drawing.id, list(models))


def _publish(session, run_id, store, author=AUTHOR):
    config = ResultsStoreConfig(store=store, author=author)
    return ResultsStoreService(session, config).publish_run(run_id)


def test_one_row_per_scored_model_document(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """The headline: a completed two-model Run over one Drawing publishes two rows, one per
    ``(model, document)``, and nothing else."""
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(
        session,
        stub_adapter,
        location_prompt,
        overlay_root,
        drawing,
        models=(SONNET, GPT),
    )

    published = _publish(session, run.id, store)

    assert published == 2
    assert {row.model for row in store.rows} == {SONNET, GPT}
    assert {row.document_id for row in store.rows} == {"elevation-01.pdf"}
    assert len({row.id for row in store.rows}) == 2  # each row its own primary key


def test_document_id_is_the_corpus_filename_not_the_lab_drawing_id(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """A Lab Drawing id is local to one SQLite file and means nothing to anyone else, so it
    must appear nowhere in the shared table — and the filename must carry the extension it
    was distributed with, which the Lab strips at ingest.

    Ingesting the same corpus file twice is what makes the difference visible: two Drawings,
    two ids, one ``document_id``. Publishing the Lab's id instead would look perfectly fine
    here and make every one of these rows incomparable with every other tool's."""
    first = seed_pdf_drawing(session, name="A-201_kitchen")
    second = seed_pdf_drawing(session, name="A-201_kitchen")
    assert first.id != second.id
    for drawing in (first, second):
        seed_location_gt(session, drawing.id, GT_OBJECTS)
        run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)
        _publish(session, run.id, store)

    assert [row.document_id for row in store.rows] == ["A-201_kitchen.pdf"] * 2


def test_document_id_of_an_image_drawing_keeps_the_name_it_has(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """An image-ingested Drawing retains no source file, so there is no extension to
    restore. The Lab publishes the name it holds rather than inventing one of four
    plausible suffixes."""
    drawing = seed_location_drawing(session, name="scan-07")  # no source_path
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    _publish(session, run.id, store)

    assert store.rows[0].document_id == "scan-07"


def test_config_label_is_author_namespaced_and_pins_the_prompt_version(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """The label identifies the Lab Configuration that produced the score. The model half is
    already its own column, so the label carries the other half — the prompt lineage and its
    version, which the Lab makes immutable (ADR 0009), so the label is stable by
    construction."""
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    _publish(session, run.id, store)

    label = store.rows[0].config_label
    assert label.startswith(f"{AUTHOR}/")
    assert label == f"{AUTHOR}/lab-{location_prompt.family}-v{location_prompt.version}"


def test_author_is_recorded_on_every_row(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(
        session,
        stub_adapter,
        location_prompt,
        overlay_root,
        drawing,
        models=(SONNET, GPT),
    )

    _publish(session, run.id, store)

    assert {row.author for row in store.rows} == {AUTHOR}


def test_scorer_version_and_iou_threshold_are_what_the_lab_scored_at(
    session,
    stub_adapter,
    location_prompt,
    overlay_root,
    seed_location_gt,
    store,
    monkeypatch,
):
    """The reproducibility anchor (ADR 0030). The version is read from the installed
    distribution rather than written down, and it is published in the same
    ``location-scorer-vX.Y.Z`` shape the schema documents, so two tools' rows compare as
    strings."""
    monkeypatch.setattr(
        "core.services.results_store.scorer_version", lambda: "9.9.9-from-metadata"
    )
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    _publish(session, run.id, store)

    row = store.rows[0]
    assert row.scorer_version == "location-scorer-v9.9.9-from-metadata"
    assert row.iou_threshold == row.scorer_output["iou_threshold"] == 0.5


def test_nothing_is_published_when_the_scorer_version_is_unknown(
    session,
    stub_adapter,
    location_prompt,
    overlay_root,
    seed_location_gt,
    store,
    monkeypatch,
):
    """A path-installed scorer has no distribution metadata. The column is NOT NULL and the
    whole point of it is provenance, so the Lab declines to write rather than inventing a
    version it cannot vouch for."""
    monkeypatch.setattr("core.services.results_store.scorer_version", lambda: None)
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    assert _publish(session, run.id, store) == 0
    assert store.inserts == 0


def test_metric_columns_agree_with_the_scorer_output_beside_them(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """Nothing downstream checks these six against the blob (ADR 0038) — a transposed
    ``fp``/``fn`` is a permanently wrong row that looks entirely normal, so it is caught
    here or not at all. The fixture is asymmetric on purpose: no two of the six values are
    equal, so any swap fails."""
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    _publish(session, run.id, store)

    row = store.rows[0]
    assert (row.tp, row.fp, row.fn) == (EXPECTED_TP, EXPECTED_FP, EXPECTED_FN)
    assert row.precision == pytest.approx(EXPECTED_PRECISION)
    assert row.recall == pytest.approx(EXPECTED_RECALL)
    assert row.f1 == pytest.approx(EXPECTED_F1)
    # …and the same six as the blob states them, field by field.
    counts, metrics = row.scorer_output["counts"], row.scorer_output["metrics"]
    assert (row.tp, row.fp, row.fn) == (counts["tp"], counts["fp"], counts["fn"])
    assert row.precision == metrics["precision"]
    assert row.recall == metrics["recall"]
    assert row.f1 == metrics["f1"]


def test_scorer_output_is_stored_whole(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """``per_type``, ``per_page`` and ``best_iou`` intact. The blob is the source of truth,
    and a summary cannot be un-summarized later: ``best_iou`` on the near miss is what tells
    "the model drew a loose box" from "the model never saw it"."""
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    _publish(session, run.id, store)

    output = store.rows[0].scorer_output
    assert output["per_type"]["cabinet"]["counts"] == {"tp": 1, "fp": 1, "fn": 1}
    assert output["per_type"]["countertop"]["counts"] == {"tp": 0, "fp": 1, "fn": 2}
    assert [page["page"] for page in output["per_page"]] == [1]
    near_miss = max(fn["best_iou"] for fn in output["objects"]["fn"])
    assert near_miss == pytest.approx(0.4)
    # It survives the trip to a JSONB column as it stands, without a custom encoder.
    assert json.loads(json.dumps(output)) == output


def test_a_drawing_without_ground_truth_publishes_nothing(
    session, stub_adapter, location_prompt, overlay_root, store
):
    """Unscored is not scored zero (spec: Runs 33). A row with no answer key behind it would
    rank as a genuine failure for everyone querying the table."""
    drawing = seed_pdf_drawing(session)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    assert _publish(session, run.id, store) == 0
    assert store.inserts == 0


def test_a_result_that_measured_nothing_is_withheld(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """A model whose every page failed unsalvageably scores a well-formed zero, which is what
    the Lab's Leaderboard shows — beside an error badge and the raw response. The shared table
    has neither, and its rows are permanent, so a 429 on one laptop would be attributable to
    the model forever. The Lab publishes only what it can qualify."""
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(
        session,
        stub_adapter,
        location_prompt,
        overlay_root,
        drawing,
        models=(SONNET, GPT),
        responses={SONNET: PREDICTED_JSON, GPT: "upstream rate limit, try again"},
    )

    published = _publish(session, run.id, store)

    # The model that answered is published; the one that never did is not.
    assert published == 1
    assert [row.model for row in store.rows] == [SONNET]
    # …and the Lab still scored it, exactly as before: this changes what is *published*.
    failed = next(r for r in run.results if r.model == GPT)
    assert ScoringService(session).score_location_result(failed.id).f1 == 0.0


def test_a_model_that_found_nothing_is_published_as_a_real_zero(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """The other side of that line, and why it is drawn where it is. An empty JSON array is a
    model saying "no objects on this page" — a real measurement, and a real zero. Withholding
    it would quietly delete the negative results the store exists to keep."""
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(
        session,
        stub_adapter,
        location_prompt,
        overlay_root,
        drawing,
        responses={SONNET: "[]"},
    )

    assert _publish(session, run.id, store) == 1
    row = store.rows[0]
    assert (row.tp, row.fp, row.fn) == (0, 0, 4)
    assert row.f1 == 0.0


def test_publishing_is_off_when_no_credential_is_configured(
    session, stub_adapter, location_prompt, overlay_root, seed_location_gt
):
    """Both env vars unset is the normal local state — the Lab then behaves exactly as it
    did before this ticket, with no error and no configuration to opt out of."""
    drawing = seed_pdf_drawing(session)
    seed_location_gt(session, drawing.id, GT_OBJECTS)
    run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)

    assert ResultsStoreService(session).publish_run(run.id) == 0


def test_a_store_outage_does_not_fail_the_run_or_lose_a_score(
    engine, stub_adapter, location_prompt, overlay_root, seed_location_gt
):
    """Recording a result must never cost the Lab an experiment. The Run is complete before
    a row is ever attempted, so an unreachable store leaves ``done`` and every Lab-side
    Score exactly as they were."""
    failing = RecordingStore(fail=True)
    with Session(engine) as session:
        drawing = seed_pdf_drawing(session)
        seed_location_gt(session, drawing.id, GT_OBJECTS)
        run = _launch(session, stub_adapter, location_prompt, overlay_root, drawing)
        run_id, result_id = run.id, run.results[0].id
        before = ScoringService(session).score_location_result(result_id).f1

        assert _publish(session, run_id, failing) == 0

    with Session(engine) as session:
        assert failing.inserts == 1  # it really did try
        assert session.get(Run, run_id).status == RunStatus.done
        score = session.exec(select(Score).where(Score.result_id == result_id)).one()
        assert score.f1 == before


def test_completing_a_run_publishes_without_being_asked(
    engine, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """The wiring: publishing is a consequence of a Run finishing, not a second thing a
    developer has to remember. A background Run is the path the web launch takes."""
    with Session(engine) as session:
        drawing = seed_pdf_drawing(session)
        seed_location_gt(session, drawing.id, GT_OBJECTS)
        stub_adapter.responses = {SONNET: PREDICTED_JSON}
        run_id = (
            RunService(session, stub_adapter, overlay_root=overlay_root)
            .create_run(location_prompt.id, drawing.id, [SONNET])
            .id
        )

    BackgroundRunner(
        engine,
        stub_adapter,
        overlay_root=overlay_root,
        results_store=ResultsStoreConfig(store=store, author=AUTHOR),
    ).execute_run(run_id)

    assert [row.model for row in store.rows] == [SONNET]
    assert store.rows[0].document_id == "elevation-01.pdf"


def test_a_failed_run_publishes_nothing(
    engine, stub_adapter, location_prompt, overlay_root, seed_location_gt, store
):
    """A Run that ended ``failed`` hit a genuine persistence error, so what it stored is
    incomplete in a way the row could not express. The shared table takes finished work
    only."""
    with Session(engine) as session:
        drawing = seed_pdf_drawing(session)
        seed_location_gt(session, drawing.id, GT_OBJECTS)
        stub_adapter.responses = {SONNET: PREDICTED_JSON}
        run_id = (
            RunService(session, stub_adapter, overlay_root=overlay_root)
            .create_run(location_prompt.id, drawing.id, [SONNET])
            .id
        )

    runner = BackgroundRunner(
        engine,
        stub_adapter,
        overlay_root=overlay_root,
        results_store=ResultsStoreConfig(store=store, author=AUTHOR),
    )
    runner._execute_result = _raise_persistence_error
    runner.execute_run(run_id)

    with Session(engine) as session:
        assert session.get(Run, run_id).status == RunStatus.failed
    assert store.inserts == 0


def _raise_persistence_error(*args, **kwargs):
    raise RuntimeError("disk went away mid-run")
