"""Web-layer checks for prompts: authoring creates a version-1 family, editing
appends a new immutable version, history renders newest-first, and the two shipped
prompts are seeded on startup (ticket 03)."""


def test_startup_seeds_default_prompt_families(client):
    listing = client.get("/prompts")
    assert listing.status_code == 200
    # Both seeded families land under their own Task's group.
    assert "default" in listing.text
    assert client.get("/prompts/counting/default").status_code == 200
    assert client.get("/prompts/location/default").status_code == 200


def test_author_creates_family_and_lists_it(client):
    response = client.post(
        "/prompts",
        data={"task": "counting", "family": "kitchen", "text": "count cabinets"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "kitchen" in client.get("/prompts").text


def test_edit_appends_new_version_shown_newest_first(client):
    client.post(
        "/prompts",
        data={"task": "location", "family": "doors", "text": "locate v1"},
        follow_redirects=False,
    )
    client.post(
        "/prompts/location/doors",
        data={"text": "locate v2"},
        follow_redirects=False,
    )

    history = client.get("/prompts/location/doors")
    assert history.status_code == 200
    # Newest-first: the v2 text renders above the v1 text.
    assert history.text.index("locate v2") < history.text.index("locate v1")


def test_authoring_a_duplicate_family_is_rejected(client):
    client.post(
        "/prompts",
        data={"task": "counting", "family": "dupe", "text": "a"},
        follow_redirects=False,
    )
    again = client.post(
        "/prompts",
        data={"task": "counting", "family": "dupe", "text": "b"},
        follow_redirects=False,
    )
    assert again.status_code == 400


def test_unknown_family_returns_404(client):
    assert client.get("/prompts/counting/ghost").status_code == 404
