"""Prompt authoring & immutable versioning (spec: Prompts; ADR 0009).

Every edit appends a new version within a ``(task, family)`` lineage; prior versions
are never mutated, so a Run can pin exact text forever. The two shipped prompt ``.md``
files seed each Task's initial ``default`` family so prior POC work carries over.
"""

from pathlib import Path

from sqlmodel import Session, select

from core.models.prompt import Prompt, Task

CORE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPTS_DIR = CORE_ROOT / "prompts"
DEFAULT_FAMILY = "default"

# The shipped prompt directory each Task seeds its initial version from.
_SEED_DIRS = {
    Task.counting: "object_counting",
    Task.location: "object_location",
}


class PromptService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, task: Task, family: str, text: str) -> Prompt:
        """Author a new prompt family at version 1. Fails if the family exists."""
        if self.latest(task, family) is not None:
            raise ValueError(
                f"prompt family {family!r} already exists for task {task.value}"
            )
        return self._append(task, family, version=1, text=text)

    def edit(self, task: Task, family: str, text: str) -> Prompt:
        """Append the next immutable version to an existing family."""
        latest = self.latest(task, family)
        if latest is None:
            raise ValueError(
                f"no prompt family {family!r} for task {task.value} to edit"
            )
        return self._append(task, family, version=latest.version + 1, text=text)

    def latest(self, task: Task, family: str) -> Prompt | None:
        """The newest version of a family, or None if it doesn't exist."""
        return self.session.exec(
            select(Prompt)
            .where(Prompt.task == task, Prompt.family == family)
            .order_by(Prompt.version.desc())
        ).first()

    def get(self, task: Task, family: str, version: int) -> Prompt | None:
        """One exact ``(task, family, version)``, or None — so a caller can pin a
        specific immutable version without hand-rolling the by-key query."""
        return self.session.exec(
            select(Prompt).where(
                Prompt.task == task,
                Prompt.family == family,
                Prompt.version == version,
            )
        ).first()

    def history(self, task: Task, family: str) -> list[Prompt]:
        """Every version of a family, newest-first (spec: browse version history)."""
        return list(
            self.session.exec(
                select(Prompt)
                .where(Prompt.task == task, Prompt.family == family)
                .order_by(Prompt.version.desc())
            )
        )

    def families(self, task: Task) -> list[str]:
        """Distinct family names for a Task, sorted — so a counting prompt is never
        offered for a location run (Task-scoping)."""
        rows = self.session.exec(
            select(Prompt.family).where(Prompt.task == task).distinct()
        )
        return sorted(rows)

    def _append(self, task: Task, family: str, version: int, text: str) -> Prompt:
        prompt = Prompt(task=task, family=family, version=version, text=text)
        self.session.add(prompt)
        self.session.commit()
        self.session.refresh(prompt)
        return prompt


def seed_default_prompts(
    session: Session, prompts_dir: Path = DEFAULT_PROMPTS_DIR
) -> None:
    """Seed each Task's shipped ``.md`` as version 1 of the ``default`` family.

    Idempotent: a Task whose default family already exists is left untouched, so it is
    safe to call on every startup.
    """
    service = PromptService(session)
    for task, dirname in _SEED_DIRS.items():
        if service.latest(task, DEFAULT_FAMILY) is not None:
            continue
        text = (prompts_dir / dirname / "v0001.md").read_text().strip()
        service.create(task, family=DEFAULT_FAMILY, text=text)
