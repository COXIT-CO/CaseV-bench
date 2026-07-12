"""Prompt authoring & immutable versioning (ticket 03; ADR 0009; glossary: Prompt).

Editing a prompt appends a new immutable version within its ``(task, family)``
lineage; prior versions are never mutated, history reads newest-first, and prompts
are Task-scoped. The two shipped prompt ``.md`` files seed each Task's initial version.
"""

import pytest
from sqlmodel import select

from models.prompt import Prompt, Task
from services.prompt import PromptService, seed_default_prompts


def test_two_edits_yield_two_immutable_versions_with_distinct_text(session):
    service = PromptService(session)

    v1 = service.create(Task.counting, family="kitchen", text="count the cabinets")
    v2 = service.edit(Task.counting, family="kitchen", text="count base cabinets only")
    v3 = service.edit(
        Task.counting, family="kitchen", text="count base + wall cabinets"
    )

    # Three distinct versions in the same family, each with distinct text.
    assert [v1.version, v2.version, v3.version] == [1, 2, 3]
    assert len({v1.text, v2.text, v3.text}) == 3

    # The two edits never mutated the earlier versions.
    assert session.get(Prompt, v1.id).text == "count the cabinets"
    assert session.get(Prompt, v2.id).text == "count base cabinets only"


def test_history_is_newest_first(session):
    service = PromptService(session)
    service.create(Task.location, family="doors", text="v1")
    service.edit(Task.location, family="doors", text="v2")
    service.edit(Task.location, family="doors", text="v3")

    history = service.history(Task.location, family="doors")

    assert [p.version for p in history] == [3, 2, 1]
    assert [p.text for p in history] == ["v3", "v2", "v1"]


def test_prompts_are_task_scoped(session):
    service = PromptService(session)
    service.create(Task.counting, family="kitchen", text="count")
    service.edit(Task.counting, family="kitchen", text="count v2")
    service.create(Task.location, family="kitchen", text="locate")
    service.create(Task.counting, family="counting-only", text="x")

    # Same family name under two Tasks are independent lineages: a counting prompt
    # is never returned when picking a location prompt.
    assert [p.text for p in service.history(Task.location, family="kitchen")] == [
        "locate"
    ]
    assert "counting-only" not in service.families(Task.location)
    assert service.families(Task.location) == ["kitchen"]
    assert service.families(Task.counting) == ["counting-only", "kitchen"]


def test_create_rejects_an_existing_family(session):
    service = PromptService(session)
    service.create(Task.counting, family="dupe", text="a")

    with pytest.raises(ValueError):
        service.create(Task.counting, family="dupe", text="b")


def test_edit_rejects_an_unknown_family(session):
    service = PromptService(session)

    with pytest.raises(ValueError):
        service.edit(Task.counting, family="ghost", text="a")


def test_seed_creates_two_initial_versions_and_is_idempotent(session):
    seed_default_prompts(session)
    seed_default_prompts(session)  # a second call must not duplicate

    prompts = session.exec(select(Prompt)).all()
    assert {p.task for p in prompts} == {Task.counting, Task.location}
    assert all(p.version == 1 for p in prompts)
    assert len(prompts) == 2

    counting = PromptService(session).history(Task.counting, family="default")[0]
    assert "JSON" in counting.text  # the shipped counting prompt's text carried over
