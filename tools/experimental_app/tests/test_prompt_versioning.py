"""Prompt authoring & immutable versioning (ticket 03; ADR 0009; glossary: Prompt).

Editing a prompt appends a new immutable version within its ``family`` lineage; prior
versions are never mutated, and history reads newest-first. A family is identified by name
alone (ADR 0032) — there is no task to scope it by — so ``(family, version)`` is the tuple
that can never be reused. The shipped prompt ``.md`` file seeds the ``default`` family.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from core.models.prompt import Prompt
from core.services.prompt import PromptService, seed_default_prompts


def test_two_edits_yield_two_immutable_versions_with_distinct_text(session):
    service = PromptService(session)

    v1 = service.create(family="kitchen", text="locate the cabinets")
    v2 = service.edit(family="kitchen", text="locate base cabinets only")
    v3 = service.edit(family="kitchen", text="locate base + wall cabinets")

    # Three distinct versions in the same family, each with distinct text.
    assert [v1.version, v2.version, v3.version] == [1, 2, 3]
    assert len({v1.text, v2.text, v3.text}) == 3

    # The two edits never mutated the earlier versions.
    assert session.get(Prompt, v1.id).text == "locate the cabinets"
    assert session.get(Prompt, v2.id).text == "locate base cabinets only"


def test_history_is_newest_first(session):
    service = PromptService(session)
    service.create(family="doors", text="v1")
    service.edit(family="doors", text="v2")
    service.edit(family="doors", text="v3")

    history = service.history(family="doors")

    assert [p.version for p in history] == [3, 2, 1]
    assert [p.text for p in history] == ["v3", "v2", "v1"]


def test_families_lists_every_family_by_name(session):
    service = PromptService(session)
    service.create(family="kitchen", text="locate")
    service.edit(family="kitchen", text="locate v2")
    service.create(family="doors", text="x")

    # One flat namespace of family names, sorted; a family appears once however many
    # versions it holds.
    assert service.families() == ["doors", "kitchen"]


def test_a_family_version_pair_can_never_be_reused(session):
    """The uniqueness that keeps a version pinned by a Run immutable is ``(family,
    version)`` alone (ADR 0032) — nothing else disambiguates two rows any more."""
    service = PromptService(session)
    service.create(family="doors", text="v1")

    session.add(Prompt(family="doors", version=1, text="a second v1"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_create_rejects_an_existing_family(session):
    service = PromptService(session)
    service.create(family="dupe", text="a")

    with pytest.raises(ValueError):
        service.create(family="dupe", text="b")


def test_edit_rejects_an_unknown_family(session):
    service = PromptService(session)

    with pytest.raises(ValueError):
        service.edit(family="ghost", text="a")


def test_seed_creates_the_initial_version_and_is_idempotent(session):
    seed_default_prompts(session)
    seed_default_prompts(session)  # a second call must not duplicate

    prompts = session.exec(select(Prompt)).all()
    assert len(prompts) == 1
    (seeded,) = prompts
    assert seeded.family == "default"
    assert seeded.version == 1

    default = PromptService(session).history(family="default")[0]
    assert "JSON" in default.text  # the shipped prompt's text carried over
