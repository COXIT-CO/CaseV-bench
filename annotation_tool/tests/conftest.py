"""Shared fixtures: a real server on a free port, and PDFs built with PyMuPDF."""

import functools
import http.server
import sys
import threading
from pathlib import Path

import pymupdf
import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = TOOL_DIR.parent / "dataset" / "public"

sys.path.insert(0, str(TOOL_DIR))

import serve  # noqa: E402


@pytest.fixture(scope="session")
def server():
    handler = functools.partial(serve.Handler, directory=str(TOOL_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def make_pdf(*pages, marker=""):
    """A PDF with one page per ``(width, height, rotation)``. ``marker`` makes the bytes unique."""
    doc = pymupdf.open()
    for width, height, rotation in pages:
        page = doc.new_page(width=width, height=height)
        page.insert_text((10, 20), f"page {page.number} {marker}")
        page.set_rotation(rotation)
    data = doc.tobytes()
    doc.close()
    return data
