"""Tests for ``assign()`` — public so a caller can get a colour by type name without
rendering anything (a legend) or share one resolved mapping across several `render()`
calls (see render()'s `colors` option and test_render.py's tests for it).
"""

from location_overlay import assign


def test_a_single_type_gets_its_hashed_colour():
    assert assign(["cabinet"]) == {"cabinet": (205, 25, 55)}


def test_every_distinct_type_gets_an_entry():
    result = assign(["cabinet", "countertop", "elevation"])

    assert set(result) == {"cabinet", "countertop", "elevation"}


def test_duplicate_types_collapse_to_one_entry():
    result = assign(["cabinet", "cabinet", "cabinet"])

    assert set(result) == {"cabinet"}


def test_empty_input_returns_an_empty_mapping():
    assert assign([]) == {}


def test_no_two_types_share_a_colour():
    result = assign(["cabinet", "countertop", "elevation", "callout", "floor plan"])

    assert len(set(result.values())) == len(result)


def test_a_colliding_pair_resolves_in_sorted_name_order():
    # Same pair render()'s own tests use: callout/floor plan hash to the same palette entry.
    alone_callout = assign(["callout"])["callout"]
    alone_plan = assign(["floor plan"])["floor plan"]
    assert (
        alone_callout == alone_plan
    )  # confirms the collision exists before testing the fix

    together = assign(["callout", "floor plan"])

    assert (
        together["callout"] == alone_callout
    )  # sorts first, keeps its preferred colour
    assert together["floor plan"] != alone_callout  # displaced to the next free entry


def test_the_result_does_not_depend_on_input_order():
    forward = assign(["callout", "floor plan", "cabinet"])
    backward = assign(["cabinet", "floor plan", "callout"])

    assert forward == backward


def test_accepts_any_iterable_not_just_a_list():
    result = assign(object_type for object_type in ("cabinet", "countertop"))

    assert set(result) == {"cabinet", "countertop"}


def test_is_stable_across_repeated_calls():
    first = assign(["cabinet", "countertop", "elevation"])
    second = assign(["cabinet", "countertop", "elevation"])

    assert first == second
