"""Tests for app.jobs — the in-memory job registry the frontend polls instead of
holding one long-lived streaming connection open. See jobs.py's module docstring for
why: no single request should ever be long-lived, so this can't gate on any HTTP
connection surviving a reverse-proxy timeout."""

from __future__ import annotations

import pytest

from app import jobs


class TestRegistry:
    def test_a_fresh_job_starts_running(self) -> None:
        job = jobs.create()
        assert job.status == "running"

    def test_get_returns_none_for_an_unknown_id(self) -> None:
        assert jobs.get("does-not-exist") is None

    def test_get_finds_a_job_created_through_the_registry(self) -> None:
        job = jobs.create()
        assert jobs.get(job.id) is job

    def test_delete_is_idempotent(self) -> None:
        job = jobs.create()
        jobs.delete(job.id)
        jobs.delete(job.id)  # should not raise
        assert jobs.get(job.id) is None

    def test_stale_jobs_are_swept_when_a_new_one_is_created(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old_job = jobs.create()
        monkeypatch.setattr(jobs, "JOB_TTL_SECONDS", 0)  # everything is "stale" now

        jobs.create()  # create() sweeps as a side effect before making the new job

        assert jobs.get(old_job.id) is None


class TestPoll:
    def test_the_very_first_poll_returns_the_very_first_event(self) -> None:
        # Regression test: sequence numbers used to start at 0, the same value the
        # client's first poll sends as "after" (meaning "I have nothing yet") — that
        # made the very first pushed event look already-acknowledged and evicted before
        # it was ever actually returned.
        job = jobs.Job(id="t1")
        job.push("progress", "hello")

        status, next_after, events = job.poll(0)

        assert status == "running"
        assert len(events) == 1
        assert events[0]["type"] == "progress"
        assert events[0]["data"] == "hello"
        assert next_after == events[0]["seq"]

    def test_events_are_returned_in_the_order_pushed(self) -> None:
        job = jobs.Job(id="t2")
        job.push("progress", "first")
        job.push("progress", "second")

        _, _, events = job.poll(0)

        assert [event["data"] for event in events] == ["first", "second"]

    def test_polling_again_with_the_same_after_is_idempotent(self) -> None:
        job = jobs.Job(id="t3")
        job.push("progress", "hello")
        _, after, _first_poll = job.poll(0)

        _, after_again, second_poll = job.poll(after)

        assert after_again == after
        assert second_poll == []  # already acknowledged, not an error

    def test_acknowledged_events_are_evicted_from_memory(self) -> None:
        # The property that keeps a long multi-page run's server-side memory bounded:
        # once a poll has carried an event out, there's no reason to keep holding its
        # (potentially large — a page's images) payload. Eviction happens on the poll
        # *after* the one that delivered it — the client's next `after` value is what
        # signals acknowledgment, so it takes a second poll to act on that.
        job = jobs.Job(id="t4")
        job.push("page", {"big": "payload"})

        _, after, _ = job.poll(0)
        job.poll(after)

        assert len(job._events) == 0

    def test_events_pushed_after_an_ack_are_still_delivered(self) -> None:
        job = jobs.Job(id="t5")
        job.push("progress", "first")
        _, after, _ = job.poll(0)
        job.push("progress", "second")

        _, _, events = job.poll(after)

        assert [event["data"] for event in events] == ["second"]

    def test_finish_is_visible_on_the_next_poll(self) -> None:
        job = jobs.Job(id="t6")
        job.finish("done")

        status, _, _ = job.poll(0)

        assert status == "done"

    def test_a_poll_with_no_new_events_keeps_the_same_after(self) -> None:
        job = jobs.Job(id="t7")

        status, next_after, events = job.poll(0)

        assert status == "running"
        assert next_after == 0
        assert events == []
