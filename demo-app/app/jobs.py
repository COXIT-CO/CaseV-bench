"""In-memory tracking for in-flight detect() runs, polled by the frontend instead of
kept alive over one long-lived streaming HTTP connection. A large multi-page run can
take minutes end to end; a single request/response staying open that whole time is
exactly what risks being cut by a reverse-proxy timeout (idle or hard-duration — we
don't control which Railway enforces, or its threshold). Polling means no single
request is ever more than a couple seconds old, so it doesn't matter which kind of
timeout is in play.

Single-process, in-memory only — fine for this app's single-uvicorn-worker deployment.
Would need a shared store (e.g. Redis) if this ever ran with multiple workers/replicas,
since a poll could then land on a process that never saw the job created.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Safety net for abandoned jobs (tab closed mid-run, client never came back to poll or
# delete): swept regardless of status. Not the normal cleanup path — see Job.poll's
# docstring and the DELETE /api/jobs/{id} the frontend calls once a run finishes.
JOB_TTL_SECONDS = 30 * 60


@dataclass
class _Event:
    seq: int
    type: str
    data: Any


@dataclass
class Job:
    id: str
    status: str = "running"  # "running" | "done" | "error"
    created_at: float = field(default_factory=time.monotonic)
    _events: dict[int, _Event] = field(default_factory=dict)
    # Starts at 1, not 0: the client's initial poll uses after=0 to mean "I have nothing
    # yet", so seq 0 would be silently evicted as "already seen" before ever being sent.
    _next_seq: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def push(self, event_type: str, data: Any) -> None:
        """Called from whichever worker thread produced this event — progress messages
        from many concurrent pages, or a single page's full result (images included)
        the moment that page is done."""
        with self._lock:
            self._events[self._next_seq] = _Event(self._next_seq, event_type, data)
            self._next_seq += 1

    def finish(self, status: str) -> None:
        with self._lock:
            self.status = status

    def poll(self, after: int) -> tuple[str, int, list[dict[str, Any]]]:
        """Returns (status, next_after, events with seq > after), and evicts everything
        at or before `after` in the same call. The client always polls with the
        `next_after` its previous poll returned, so anything at or before that has
        already been delivered — there's no reason to keep holding a completed page's
        images in server memory once the one poll that carried them has gone out. This
        is what keeps a long multi-page run's memory bounded by "events since the last
        poll" rather than "every page the whole run has produced so far", the same
        property the old streaming-response version got by never holding a page after
        yielding it.
        """
        with self._lock:
            for seq in [seq for seq in self._events if seq <= after]:
                del self._events[seq]
            pending = sorted(self._events.values(), key=lambda event: event.seq)
            events = [{"seq": event.seq, "type": event.type, "data": event.data} for event in pending]
            next_after = pending[-1].seq if pending else after
            return self.status, next_after, events


_jobs: dict[str, Job] = {}
_registry_lock = threading.Lock()


def create() -> Job:
    _sweep_stale()
    job = Job(id=str(uuid.uuid4()))
    with _registry_lock:
        _jobs[job.id] = job
    return job


def get(job_id: str) -> Job | None:
    with _registry_lock:
        return _jobs.get(job_id)


def delete(job_id: str) -> None:
    with _registry_lock:
        _jobs.pop(job_id, None)


def _sweep_stale() -> None:
    cutoff = time.monotonic() - JOB_TTL_SECONDS
    with _registry_lock:
        stale_ids = [job_id for job_id, job in _jobs.items() if job.created_at < cutoff]
        for job_id in stale_ids:
            del _jobs[job_id]
