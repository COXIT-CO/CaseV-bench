"""Tests for the Makefile targets that manage the server: ``make stop`` must stop a server
started in the background, and must not fail when nothing is running."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def is_listening(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def make_stop(port):
    return subprocess.run(
        ["make", "--no-print-directory", "stop", f"PORT={port}"],
        cwd=TOOL_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def running_server():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "serve.py", "--port", str(port), "--no-browser"],
        cwd=TOOL_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    while not is_listening(port):
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            pytest.fail("serve.py did not start")
        time.sleep(0.1)
    yield port, proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_stop_stops_a_background_server(running_server):
    port, proc = running_server
    result = make_stop(port)
    assert result.returncode == 0, result.stderr
    assert f"Stopped annotation tool on port {port}." in result.stdout
    proc.wait(timeout=10)
    assert not is_listening(port)


def test_stop_with_nothing_running_says_so():
    port = free_port()
    result = make_stop(port)
    assert result.returncode == 0, result.stderr
    assert f"Nothing is listening on port {port}." in result.stdout
