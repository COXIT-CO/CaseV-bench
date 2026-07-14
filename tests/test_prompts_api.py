"""JSON contract for Prompts (spec §A.5, ticket 05). These are the twins of the Jinja
``/prompts`` list, its authoring form, the version-history page, and its "edit" form:
families grouped by Task (Task-scoping, ADR 0009), creating a new family's v1, browsing a
family's immutable version history newest-first with each version's text (so compare needs
no extra round-trip), and appending the next immutable version. The two shipped prompts are
seeded on app startup, so both Tasks start with a ``default`` family."""


def test_list_groups_seeded_families_by_task(client):
    body = client.get("/api/prompts").json()

    assert body["tasks"] == ["counting", "location"]
    groups = {g["task"]: g["families"] for g in body["groups"]}
    assert set(groups) == {"counting", "location"}
    # Each Task's seeded ``default`` family reports its latest version and version count.
    counting_default = next(f for f in groups["counting"] if f["name"] == "default")
    assert counting_default == {"name": "default", "latest_version": 1, "count": 1}
    assert any(f["name"] == "default" for f in groups["location"])


def test_create_makes_v1_and_lists_it_under_its_task(client):
    resp = client.post(
        "/api/prompts",
        json={"task": "counting", "family": "kitchen", "text": "count cabinets"},
    )
    assert resp.status_code == 201
    assert resp.json() == {"task": "counting", "family": "kitchen", "version": 1}

    groups = {
        g["task"]: g["families"] for g in client.get("/api/prompts").json()["groups"]
    }
    kitchen = next(f for f in groups["counting"] if f["name"] == "kitchen")
    assert kitchen == {"name": "kitchen", "latest_version": 1, "count": 1}
    # Task-scoped: a counting family is never offered under location.
    assert not any(f["name"] == "kitchen" for f in groups["location"])


def test_create_duplicate_family_is_400_with_service_message(client):
    client.post(
        "/api/prompts",
        json={"task": "counting", "family": "dupe", "text": "a"},
    )
    again = client.post(
        "/api/prompts",
        json={"task": "counting", "family": "dupe", "text": "b"},
    )
    assert again.status_code == 400
    assert "already exists" in again.json()["detail"]


def test_history_is_newest_first_with_each_versions_text(client):
    client.post(
        "/api/prompts",
        json={"task": "location", "family": "doors", "text": "locate v1"},
    )
    client.post(
        "/api/prompts/location/doors/versions",
        json={"text": "locate v2"},
    )

    body = client.get("/api/prompts/location/doors").json()
    assert body["task"] == "location"
    assert body["family"] == "doors"
    # Newest-first, and each version carries its text so compare needs no follow-up fetch.
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert [v["text"] for v in body["versions"]] == ["locate v2", "locate v1"]
    assert all(v["created_at"] for v in body["versions"])


def test_append_version_never_mutates_prior_versions(client):
    client.post(
        "/api/prompts",
        json={"task": "counting", "family": "cab", "text": "v1 text"},
    )
    resp = client.post(
        "/api/prompts/counting/cab/versions",
        json={"text": "v2 text"},
    )
    assert resp.status_code == 201
    assert resp.json() == {"task": "counting", "family": "cab", "version": 2}

    versions = {
        v["version"]: v["text"]
        for v in client.get("/api/prompts/counting/cab").json()["versions"]
    }
    # The v1 row is untouched by the edit (immutable append, ADR 0009).
    assert versions == {1: "v1 text", 2: "v2 text"}


def test_history_unknown_family_is_404(client):
    assert client.get("/api/prompts/counting/ghost").status_code == 404


def test_append_to_unknown_family_is_404(client):
    resp = client.post(
        "/api/prompts/counting/ghost/versions",
        json={"text": "x"},
    )
    assert resp.status_code == 404
