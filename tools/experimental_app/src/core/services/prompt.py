"""Prompt authoring & immutable versioning (spec: Prompts; ADR 0009).

Every edit appends a new version within a ``family`` lineage; prior versions are never
mutated, so a Run can pin exact text forever. The shipped prompt ``.md`` file seeds the
initial ``default`` family so prior POC work carries over.
"""

from pathlib import Path

from sqlmodel import Session, func, select

from core.config import settings
from core.models.prompt import Prompt
from core.models.run import Result, Run
from core.services.deletion import RunCascadeCounts, cascade_delete_runs

CORE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPTS_DIR = CORE_ROOT / "prompts"
DEFAULT_FAMILY = "default"

# Where a deleted version's/family's cascaded Runs clean up their per-Result overlay PNGs
# (ADR-0016). Production default under the single data root; tests inject a temp root
# (ADR-0014). Only the delete path touches it; authoring never does.
DEFAULT_OVERLAY_ROOT = settings.overlays_root

# The shipped prompt directory the ``default`` family seeds its initial version from.
_SEED_DIR = "object_location"


class PromptService:
    def __init__(self, session: Session, overlay_root: Path = DEFAULT_OVERLAY_ROOT):
        self.session = session
        # Needed only by the delete methods, which cascade into the pinning Runs and their
        # per-Result overlay files (ADR-0016); authoring never touches it.
        self.overlay_root = Path(overlay_root)

    def create(self, family: str, text: str) -> Prompt:
        """Author a new prompt family at version 1. Fails if the family exists."""
        if self.latest(family) is not None:
            raise ValueError(f"prompt family {family!r} already exists")
        return self._append(family, version=1, text=text)

    def edit(self, family: str, text: str) -> Prompt:
        """Append the next immutable version to an existing family."""
        latest = self.latest(family)
        if latest is None:
            raise ValueError(f"no prompt family {family!r} to edit")
        return self._append(family, version=latest.version + 1, text=text)

    def latest(self, family: str) -> Prompt | None:
        """The newest version of a family, or None if it doesn't exist."""
        return self.session.exec(
            select(Prompt)
            .where(Prompt.family == family)
            .order_by(Prompt.version.desc())
        ).first()

    def get(self, family: str, version: int) -> Prompt | None:
        """One exact ``(family, version)``, or None — so a caller can pin a specific
        immutable version without hand-rolling the by-key query."""
        return self.session.exec(
            select(Prompt).where(
                Prompt.family == family,
                Prompt.version == version,
            )
        ).first()

    def history(self, family: str) -> list[Prompt]:
        """Every version of a family, newest-first (spec: browse version history)."""
        return list(
            self.session.exec(
                select(Prompt)
                .where(Prompt.family == family)
                .order_by(Prompt.version.desc())
            )
        )

    def families(self) -> list[str]:
        """Every distinct family name, sorted — the flat namespace the Prompts list
        renders (ADR 0032)."""
        return sorted(self.session.exec(select(Prompt.family).distinct()))

    def collateral_by_version(self, family: str) -> dict[int, RunCascadeCounts]:
        """Per-version ``(runs, results)`` a delete of each version would cascade, keyed by
        version number (ADR-0016), so the history view can state each version's blast radius
        before committing — and a family delete's total is their sum, since a Run pins exactly
        one version. Keeping the count logic in the service holds it on the domain side of the
        ``api → core`` boundary (ADR-0015)."""
        return {
            p.version: self._collateral([p.id])
            for p in self.session.exec(select(Prompt).where(Prompt.family == family))
        }

    def delete_version(self, family: str, version: int) -> RunCascadeCounts:
        """Permanently delete one immutable version and cascade the Runs that pinned it —
        their Results, Predictions, Scores, and overlay files — returning the collateral
        counts the confirm dialog showed (ADR-0016). The family's other versions and their
        Runs are untouched, even when this one sat mid-lineage (a cosmetic v1, v3 gap). Raises
        ``ValueError`` when there is no such version so the route can 404."""
        prompt = self.get(family, version)
        if prompt is None:
            raise ValueError(f"no prompt {family!r} v{version}")
        return self._cascade_and_delete([prompt])

    def delete_family(self, family: str) -> RunCascadeCounts:
        """Permanently delete an entire family — every version and every Run pinning any of
        them (reusing the Run-deletion machinery, so their Results/Predictions/Scores/overlay
        files go too) — returning the collateral counts the confirm dialog showed (ADR-0016).
        Raises ``ValueError`` when the family doesn't exist so the route can 404."""
        prompts = self.history(family)
        if not prompts:
            raise ValueError(f"no prompt family {family!r}")
        return self._cascade_and_delete(prompts)

    def _collateral(self, prompt_ids: list[int]) -> RunCascadeCounts:
        """The ``(runs, results)`` a delete of the given prompt versions would cascade —
        the Runs pinning any of them and those Runs' Results. The same set the cascade
        removes, so the preview and the delete's receipt agree."""
        run_ids = self._run_ids_pinning(prompt_ids)
        result_count = self.session.exec(
            select(func.count()).select_from(Result).where(Result.run_id.in_(run_ids))
        ).one()
        return RunCascadeCounts(runs=len(run_ids), results=result_count)

    def _cascade_and_delete(self, prompts: list[Prompt]) -> RunCascadeCounts:
        """Cascade every Run pinning one of ``prompts`` (via the shared Run-deletion
        machinery), then delete the prompt rows themselves, and return the collateral counts.
        Runs first: that clears every pin on these versions, so the Prompt rows then delete
        FK-clean."""
        run_ids = self._run_ids_pinning([p.id for p in prompts])
        counts = cascade_delete_runs(self.session, run_ids, self.overlay_root)
        for prompt in prompts:
            self.session.delete(prompt)
        self.session.commit()
        return counts

    def _run_ids_pinning(self, prompt_ids: list[int]) -> list[int]:
        """The ids of every Run that pinned one of the given prompt versions."""
        return list(
            self.session.exec(select(Run.id).where(Run.prompt_id.in_(prompt_ids))).all()
        )

    def _append(self, family: str, version: int, text: str) -> Prompt:
        prompt = Prompt(family=family, version=version, text=text)
        self.session.add(prompt)
        self.session.commit()
        self.session.refresh(prompt)
        return prompt


def seed_default_prompts(
    session: Session, prompts_dir: Path = DEFAULT_PROMPTS_DIR
) -> None:
    """Seed the shipped ``.md`` as version 1 of the ``default`` family.

    Idempotent: an existing ``default`` family is left untouched, so it is safe to call on
    every startup.
    """
    service = PromptService(session)
    if service.latest(DEFAULT_FAMILY) is not None:
        return
    text = (prompts_dir / _SEED_DIR / "v0001.md").read_text().strip()
    service.create(family=DEFAULT_FAMILY, text=text)
