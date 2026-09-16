"""The Lab's write to the shared results store (scope 9, ticket 05).

A completed Run's per-model scores land in ``experiments.run_results`` on the team's shared
Postgres, where results produced by five people's five tools can finally be compared. This
module is the whole of the Lab's side of that.

**There is no client library, deliberately.** Tool branches never merge (ADR 0033), so a
shared package could only reach them as a pinned tag — and a pinned tag is a promise about a
thing with one version at a time, which a database is not (ADR 0038's amendment). The shared
contract is the database itself: the table, the roles and the grants. The columns below are
written against ``src/results_store/models.py`` on ``main``, which is *read* and never
imported; this file is the second copy of those column names, and that is the accepted cost.

Three properties this module exists to hold, none of which anything downstream enforces:

- **The metric columns are the writer's word.** Nothing in the database checks ``tp``/``fp``/
  ``fn``/``precision``/``recall``/``f1`` against the ``scorer_output`` beside them, so both
  are taken from one library return value here rather than assembled twice — and the mapping
  is tested against an asymmetric fixture, because a transposed pair produces a permanently
  wrong row that looks entirely normal.
- **``config_label`` is immutable by convention.** Two different flows sharing one label is a
  silent comparison bug. The Lab's half of the Configuration that is not already its own
  column is the prompt version, which the Lab never mutates (ADR 0009), so the label is
  stable by construction rather than by care.
- **Recording a result must never cost an experiment.** Every failure here — an unreachable
  store, a rejected row, a missing credential — is logged and swallowed. A Run is finished
  and committed before a single row is attempted, and nothing on this path writes to the
  Lab's own database at all.

Two seams: ``ResultsStoreService.rows_for_run`` maps the Lab's domain onto the shared
columns and touches no network, and ``SharedResultsStore.insert`` is the wire.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from sqlmodel import Session

from core.config import settings
from core.models.drawing import Drawing
from core.models.prompt import Prompt
from core.models.run import Result, Run, RunStatus
from core.services.scoring import (
    LocationBox,
    ScoringService,
    score_location,
    scorer_version,
)

# Every failure on this path is swallowed, so a log line is the only thing standing between a
# store that silently stopped recording and nobody noticing for a month. The Lab logs nowhere
# else because nowhere else swallows anything.
logger = logging.getLogger(__name__)

# The shape ``scorer_version`` is published in. The schema's own example is
# ``location-scorer-v0.3.0``, and the column is compared as a string across tools — so the
# distribution's bare ``0.1.0`` is qualified here rather than left to mean whatever the
# reader assumes. The version itself is read from installed metadata (ADR 0030), never
# written down.
SCORER_VERSION_PREFIX = "location-scorer-v"

# How long a publish may spend trying to reach the store before giving up. A store that
# *refuses* fails immediately and is caught; one that blackholes packets — a VPN dropped, a
# Railway edge gone quiet — would otherwise hold the run thread on the OS TCP timeout, which
# on Linux is over two minutes and is not something this code gets to choose. Short, because
# the shared store is a nicety and the Run is the work: better to lose the record of an
# experiment than to leave a developer watching a finished Run that will not say so.
CONNECT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class RunResultRow:
    """One ``experiments.run_results`` row: a single ``(model, document)`` measurement.

    Field names and order match the column declaration on ``main``. ``id`` is the writer's to
    supply — the column has no default, so a row identifies itself before it reaches the
    database. ``created_at`` is the one column deliberately absent: it defaults to ``now()``
    server-side, so provenance timing is the database's clock rather than a laptop's.
    """

    id: UUID
    model: str
    document_id: str
    config_label: str
    iou_threshold: float
    scorer_version: str
    author: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    scorer_output: dict
    latency_ms: int | None
    cost_usd: float | None


class SharedResultsStore(Protocol):
    """Where rows go. Narrow on purpose: the Lab appends and never reads, which is exactly
    what the ``experiments_rw`` credential is allowed to do."""

    def insert(self, rows: Sequence[RunResultRow]) -> None: ...


@dataclass(frozen=True)
class ResultsStoreConfig:
    """Everything needed to publish: a store to append to, and who the rows say produced
    them. One type rather than two arguments because neither is any use without the other —
    a store with no author would write rows whose provenance is a guess, and an author with
    no store has nowhere to write. ``None`` in place of this whole object is what "publishing
    is off" means, and it is the normal local state."""

    store: SharedResultsStore
    author: str


def document_id(drawing: Drawing) -> str:
    """The corpus filename this Drawing was ingested from, as the shared table wants it —
    verbatim as distributed, never the Lab's own Drawing id, which is local to one SQLite
    file and would make these rows incomparable with every other tool's while looking fine.

    The Lab keeps the filename's *stem* as the Drawing's name and renames the retained upload
    to ``source.pdf``, so the extension has to be restored from the retained file's suffix.
    That is exact for a PDF-ingested Drawing, which is the corpus. An image-ingested Drawing
    retains no source at all (ADR 0018) and its raster is normalized to PNG regardless of what
    was uploaded, so its distributed suffix is genuinely unrecoverable — the name is published
    as it stands rather than guessing one of four. Anyone joining on those rows sees a
    filename without an extension, which is visibly odd, where a wrong extension would not be.
    """
    if drawing.source_path:
        return f"{drawing.name}{Path(drawing.source_path).suffix}"
    return drawing.name


def measured_anything(result: Result) -> bool:
    """Whether this Result is a measurement of the model at all, rather than of the weather.

    A Prediction with no ``parsed_json`` produced nothing usable even after salvage (ADR
    0027) — a 429, a timeout, a response that was not JSON in any recoverable sense. When
    **every** Prediction on a Result is like that, scoring still yields a well-formed
    ``tp=0, f1=0``, which is what the Lab's own Leaderboard shows, and rightly: the drill-down
    sits beside it with the error badge and the raw response, so a reader can see what
    happened.

    The shared table has no such badge and no such neighbour. A published zero there is
    permanent, append-only, and reads as "this model cannot find cabinets" — so an outage on
    one laptop would be attributable to a model forever. The Lab therefore declines to
    publish what it cannot qualify, on the same principle that keeps a GT-less Drawing
    unscored rather than zero (spec: Runs 33).

    Deliberately narrow. A model that legitimately answered "no objects here" stored a
    ``parsed_json`` of ``[]`` and **is** published as a zero, because that is a real
    measurement. A partial failure is published too, recall taking the honest hit for the
    pages that died — matching what the Lab shows and what ADR 0027 already decided. Only the
    all-or-nothing case is withheld.
    """
    return any(prediction.parsed_json is not None for prediction in result.predictions)


def document_cost_usd(result: Result) -> float | None:
    """What this document cost this model to produce: every page's ``Prediction.cost_usd``
    summed, since dollars really do add across pages regardless of how they were scheduled.

    ``None`` only when not one page reported usage — the same rule ``_CallStats`` applies at
    the page level (``core.services.run``), carried up so a document scored by a provider
    that reports no ``usage`` block reads as *unmeasured* rather than a free run. A partial
    Result (some pages measured, some not, e.g. a retried page that never got a clean
    response) still sums what it has: an undercount that is visibly a sum-of-some is closer
    to the truth than discarding the pages that did report.
    """
    costs = [p.cost_usd for p in result.predictions if p.cost_usd is not None]
    return sum(costs) if costs else None


def document_latency_ms(result: Result) -> int | None:
    """The typical page latency for this document, not the total.

    Pages render up to ``DEFAULT_MAX_CONCURRENCY`` at a time (``core.services.run``), so
    summing every ``Prediction.latency_ms`` would measure the Run's scheduling more than the
    model's speed — a four-page document at concurrency 3 does not take four times as long as
    one page. The mean of the pages that were actually timed is comparable across documents
    with different page counts, which a sum is not.
    """
    latencies = [p.latency_ms for p in result.predictions if p.latency_ms is not None]
    return round(sum(latencies) / len(latencies)) if latencies else None


def config_label(author: str, prompt: Prompt) -> str:
    """The author-namespaced label identifying the Lab Configuration behind a score.

    A Configuration is ``(prompt version, model)`` and the model is already its own column, so
    the label carries the prompt lineage. It is stable because a Lab prompt version is
    immutable — "editing" a prompt inserts the next version in the family (ADR 0009) — which
    is what lets the shared table treat the label as immutable too.

    **Accepted risk:** the Run's knobs (dpi, downsample, max_tokens, temperature) are not in
    the label, following ADR 0018, which keeps them off the Configuration axis. Two rows can
    therefore share a label having been measured at different DPI. The Lab records the knobs
    on its own Run row; the shared table does not see them.
    """
    return f"{author}/lab-{prompt.family}-v{prompt.version}"


class ResultsStoreService:
    """Publishes a completed Run's scores to the shared table.

    ``config`` falls back to what the environment configures
    (``CASEV_EXPERIMENTS_DATABASE_URL`` + ``CASEV_EXPERIMENTS_AUTHOR``). Unconfigured is the
    normal local state and means publishing is simply off — there is nothing to opt out of
    and nothing fails.
    """

    def __init__(self, session: Session, config: ResultsStoreConfig | None = None):
        self.session = session
        self.config = config if config is not None else configured_store()

    def publish_run(self, run_id: int) -> int:
        """Append this Run's scored measurements to the shared table, returning how many rows
        landed. **Never raises.** The Run's terminal state is committed before this is called
        and nothing here writes to the Lab's own database, so the worst an unreachable store
        can cost is the record of an experiment, never the experiment (ticket 05).
        """
        if self.config is None:
            return 0
        try:
            rows = self.rows_for_run(run_id)
            if not rows:
                return 0
            self.config.store.insert(rows)
            return len(rows)
        except Exception:
            logger.exception(
                "could not publish run %s to the shared results store; "
                "the Run and its Lab-side Scores are unaffected",
                run_id,
            )
            return 0

    def rows_for_run(self, run_id: int) -> list[RunResultRow]:
        """This Run's shared-table rows: one per **scored** ``(model, document)``.

        Empty when the Drawing has no ground truth — unscored is not scored zero (spec: Runs
        33), and a row with no answer key behind it would read as a genuine failure to
        everyone querying the table. Empty too when the installed scorer has no version
        metadata: that column is the reproducibility anchor, so the Lab declines to write
        rather than inventing a version it cannot vouch for.

        Scoring is recomputed here against current GT rather than read from the Lab's ``Score``
        rows, for two reasons: the ``Score`` row keeps no aggregate ``tp``/``fp``/``fn`` and no
        blob at all, and recomputing is what the Leaderboard does (ADR 0004), so a published
        row says what the Lab would show for the same Result today.
        """
        version = scorer_version()
        if version is None:
            logger.warning(
                "location-scorer has no distribution metadata, so run %s cannot be published "
                "with the version it was scored at",
                run_id,
            )
            return []

        run = self.session.get(Run, run_id)
        if run is None:
            raise ValueError(f"no run with id {run_id}")
        drawing = self.session.get(Drawing, run.drawing_id)
        prompt = self.session.get(Prompt, run.prompt_id)

        scoring = ScoringService(self.session)
        gt_by_page = scoring.location_gt_boxes(run.drawing_id)
        if not gt_by_page:
            return []

        return [
            row
            for result in run.results
            if (row := self._row(scoring, result, drawing, prompt, gt_by_page, version))
        ]

    def _row(
        self,
        scoring: ScoringService,
        result: Result,
        drawing: Drawing,
        prompt: Prompt,
        gt_by_page: Mapping[int, Sequence[LocationBox]],
        version: str,
    ) -> RunResultRow | None:
        """One Result's row, or ``None`` when it did not score.

        The six metric columns and the blob come from **one** ``score()`` return value, so the
        only way they can disagree is a mistake in the assignment below — which is the one
        thing nothing downstream would catch, and is what the transposition test covers.
        ``include_objects`` is on because the blob is all a reader of the shared table has:
        ``best_iou`` on a miss is what separates a loose box from one the model never drew.
        """
        if not measured_anything(result):
            return None
        computed = score_location(
            scoring.predicted_boxes_by_page(result),
            gt_by_page,
            include_objects=True,
        )
        if computed is None:
            return None
        counts = computed.scorer_output["counts"]
        return RunResultRow(
            id=uuid4(),
            model=result.model,
            document_id=document_id(drawing),
            config_label=config_label(self.config.author, prompt),
            # The library's echo of the operating point it actually scored at, not the
            # constant it was asked for (ADR 0030) — so the column can never disagree with
            # the blob beside it.
            iou_threshold=computed.iou_threshold,
            scorer_version=f"{SCORER_VERSION_PREFIX}{version}",
            author=self.config.author,
            tp=counts["tp"],
            fp=counts["fp"],
            fn=counts["fn"],
            precision=computed.precision,
            recall=computed.recall,
            f1=computed.f1,
            scorer_output=computed.scorer_output,
            latency_ms=document_latency_ms(result),
            cost_usd=document_cost_usd(result),
        )


# The columns, in the order ``INSERT`` writes them. Spelled out rather than derived from
# ``RunResultRow`` so that the statement sent to Postgres is readable in this file — this is
# the Lab's copy of a contract that lives on another branch, and a reviewer comparing the two
# should be able to do it by eye.
_INSERT = """
    INSERT INTO experiments.run_results (
        id, model, document_id, config_label, iou_threshold, scorer_version, author,
        tp, fp, fn, precision, recall, f1, scorer_output, latency_ms, cost_usd
    ) VALUES (
        %(id)s, %(model)s, %(document_id)s, %(config_label)s, %(iou_threshold)s,
        %(scorer_version)s, %(author)s, %(tp)s, %(fp)s, %(fn)s, %(precision)s,
        %(recall)s, %(f1)s, %(scorer_output)s, %(latency_ms)s, %(cost_usd)s
    )
"""


class PostgresResultsStore:
    """The shared Postgres, written to with the Lab's own ``INSERT``.

    Holds the ``experiments_rw`` credential, which has ``SELECT, INSERT`` and nothing else —
    so a mistake on this path cannot damage anyone else's results; Postgres refuses, rather
    than this code being careful. A connection is opened per publish and closed after: a Run
    finishes every few minutes at most, and a pool held open across a laptop's sleep is more
    failure than it saves.

    All of a Run's rows go in **one transaction**, so a half-published Run is not a state
    anyone can query.
    """

    def __init__(
        self, database_url: str, connect_timeout: int = CONNECT_TIMEOUT_SECONDS
    ):
        self.database_url = database_url
        self.connect_timeout = connect_timeout

    def insert(self, rows: Sequence[RunResultRow]) -> None:
        # Parameters come straight off the dataclass, so the column names exist twice in this
        # file (the row's fields and the statement above) rather than three times. The only
        # field needing a hand is the blob: ``Jsonb`` adapts it rather than pre-serializing,
        # so it reaches the JSONB column as the structure it is.
        params = [
            asdict(row) | {"scorer_output": Jsonb(row.scorer_output)} for row in rows
        ]
        with psycopg.connect(
            self.database_url, connect_timeout=self.connect_timeout
        ) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(_INSERT, params)


def configured_store() -> ResultsStoreConfig | None:
    """What this machine is configured to publish to, or ``None`` when it is configured to
    publish to nothing — the normal local state, and not an error.

    Both env vars are required together: a URL without an author would produce rows whose
    provenance is a guess, which is worse than no rows, so that combination is refused
    loudly rather than filled in with a default. Read at call time rather than frozen at
    import, so a developer who exports the credential mid-session does not have to restart.
    """
    if not settings.experiments_database_url:
        return None
    if not settings.experiments_author:
        logger.warning(
            "CASEV_EXPERIMENTS_DATABASE_URL is set but CASEV_EXPERIMENTS_AUTHOR is not; "
            "results will not be published, since a row with no author is worse than no row"
        )
        return None
    return ResultsStoreConfig(
        store=PostgresResultsStore(settings.experiments_database_url),
        author=settings.experiments_author,
    )


def publish_completed_run(
    session: Session, run: Run, config: ResultsStoreConfig | None
) -> int:
    """Publish a Run that has just reached a terminal state, if it earned it.

    Only a ``done`` Run is published: a ``failed`` one hit a genuine persistence error, so
    what it stored is incomplete in a way no row could express, and the shared table takes
    finished work only. Never raises, for the same reason ``publish_run`` does not.
    """
    if run.status is not RunStatus.done:
        return 0
    return ResultsStoreService(session, config).publish_run(run.id)
