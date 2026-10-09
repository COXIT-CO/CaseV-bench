"""Tests for the annotation tool's local server, ``serve.py``, through its HTTP API.

Each test runs against a real server on a free port and talks to it the way the browser does:
upload PDF bytes, fetch page PNGs, request an export. PDFs are built here with PyMuPDF, so their
sizes and rotations are known exactly. Rotated pages get their own tests because the stored
boxes live in the rotated (displayed) space, and getting that wrong shifts every box the
scorer reads.
"""

import functools
import http.server
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf
import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import serve  # noqa: E402

DATASET_DIR = TOOL_DIR.parent / "dataset" / "public"


@pytest.fixture(scope="module")
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


def request(url, data=None, content_type="application/pdf"):
    """``(status, headers, body)``, for error statuses as well."""
    headers = {"Content-Type": content_type} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def upload(server, pdf):
    status, _, body = request(f"{server}/api/documents", pdf)
    assert status == 200, body
    return json.loads(body)


def export(server, doc_id, objects, colors=None):
    payload = json.dumps({"objects": objects, "colors": colors or {}}).encode()
    return request(f"{server}/api/documents/{doc_id}/export", payload, "application/json")


def png_size(png):
    pixmap = pymupdf.Pixmap(png)
    return pixmap.width, pixmap.height


# --- static files ---------------------------------------------------------------------------


def test_serves_the_page_uncached(server):
    status, headers, body = request(f"{server}/")
    assert status == 200
    assert b"<html" in body.lower()
    assert headers["Cache-Control"] == "no-store"


def test_serves_the_scripts(server):
    status, _, body = request(f"{server}/src/main.js")
    assert status == 200
    assert b"import" in body


# --- upload ---------------------------------------------------------------------------------


def test_upload_reports_page_sizes(server):
    doc = upload(server, make_pdf((300, 200, 0), (400, 500, 0), marker="sizes"))
    assert len(doc["id"]) == 16
    assert doc["pages"] == [{"width": 300, "height": 200}, {"width": 400, "height": 500}]


def test_upload_reports_rotated_page_sizes_as_displayed(server):
    doc = upload(server, make_pdf((300, 200, 90), (300, 200, 270), marker="rotated-sizes"))
    assert doc["pages"] == [{"width": 200, "height": 300}, {"width": 200, "height": 300}]


def test_same_pdf_gets_the_same_id(server):
    pdf = make_pdf((100, 100, 0), marker="same-id")
    assert upload(server, pdf)["id"] == upload(server, pdf)["id"]


def test_rejects_a_file_that_is_not_a_pdf(server):
    status, _, body = request(f"{server}/api/documents", b"this is not a pdf")
    assert status == 400
    assert "not a readable PDF" in json.loads(body)["error"]


def test_rejects_an_empty_upload(server):
    status, _, _ = request(f"{server}/api/documents", b"")
    assert status == 413


def test_unknown_post_path_is_404(server):
    status, _, _ = request(f"{server}/api/nothing-here", b"x")
    assert status == 404


def test_keeps_only_the_most_recent_documents_open(server):
    first = upload(server, make_pdf((100, 100, 0), marker="evict-0"))["id"]
    for n in range(1, serve.MAX_OPEN_DOCUMENTS + 1):
        upload(server, make_pdf((100, 100, 0), marker=f"evict-{n}"))
    status, _, body = request(f"{server}/api/documents/{first}/pages/0.png?scale=1")
    assert status == 404
    assert "not open" in json.loads(body)["error"]


# --- page rendering -------------------------------------------------------------------------


def test_renders_a_page_at_the_requested_scale(server):
    doc = upload(server, make_pdf((300, 200, 0), marker="render"))
    status, headers, png = request(f"{server}/api/documents/{doc['id']}/pages/0.png?scale=2")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert png_size(png) == (600, 400)


def test_renders_a_rotated_page_as_displayed(server):
    doc = upload(server, make_pdf((300, 200, 90), marker="render-rotated"))
    _, _, png = request(f"{server}/api/documents/{doc['id']}/pages/0.png?scale=1")
    assert png_size(png) == (200, 300)


def test_renders_only_the_clip(server):
    doc = upload(server, make_pdf((300, 200, 0), marker="clip"))
    status, _, png = request(f"{server}/api/documents/{doc['id']}/pages/0.png?scale=2&clip=10,20,60,120")
    assert status == 200
    assert png_size(png) == (100, 200)


def test_clip_is_cut_to_the_page(server):
    doc = upload(server, make_pdf((300, 200, 0), marker="clip-edge"))
    _, _, png = request(f"{server}/api/documents/{doc['id']}/pages/0.png?scale=1&clip=250,150,400,400")
    assert png_size(png) == (50, 50)


@pytest.mark.parametrize(
    "query, message",
    [
        ("scale=0", "scale out of range"),
        ("scale=17", "scale out of range"),
        ("scale=abc", "could not convert"),
        ("scale=1&clip=1,2,3", "four numbers"),
        ("scale=1&clip=4000,4000,5000,5000", "outside the page"),
        ("scale=16&clip=0,0,3000,3000", "too large"),
    ],
)
def test_bad_render_requests_are_400(server, query, message):
    doc = upload(server, make_pdf((3000, 3000, 0), marker="bad-render"))
    status, _, body = request(f"{server}/api/documents/{doc['id']}/pages/0.png?{query}")
    assert status == 400
    assert message in json.loads(body)["error"]


def test_missing_page_is_404(server):
    doc = upload(server, make_pdf((100, 100, 0), marker="missing-page"))
    status, _, body = request(f"{server}/api/documents/{doc['id']}/pages/1.png?scale=1")
    assert status == 404
    assert json.loads(body)["error"] == "no such page"


def test_unknown_document_is_404(server):
    status, _, _ = request(f"{server}/api/documents/{'0' * 16}/pages/0.png?scale=1")
    assert status == 404


# --- export ---------------------------------------------------------------------------------


def drawn_rects(page):
    """The export's boxes on ``page``, in the displayed (rotated) space, rounded to points."""
    rects = []
    for drawing in page.get_drawings():
        shown = drawing["rect"] * page.rotation_matrix
        rects.append(tuple(round(v) for v in (shown.x0, shown.y0, shown.x1, shown.y1)))
    return sorted(rects)


def box(page, x, y, width, height, category="cabinet", id="cab-001"):
    return {"id": id, "category": category, "page": page, "bbox": {"x": x, "y": y, "width": width, "height": height}}


def test_export_draws_each_box_with_its_label(server):
    doc = upload(server, make_pdf((300, 200, 0), (300, 200, 0), marker="export"))
    objects = [
        box(1, 10, 30, 50, 40, id="cab-001"),
        box(2, 100, 50, 80, 60, "countertop", "ctp-001"),
    ]
    status, headers, body = export(server, doc["id"], objects, {"cabinet": "#2563eb"})
    assert status == 200
    assert headers["Content-Type"] == "application/pdf"

    out = pymupdf.open(stream=body, filetype="pdf")
    assert out.page_count == 2
    assert drawn_rects(out[0]) == [(10, 30, 60, 70)]
    assert drawn_rects(out[1]) == [(100, 50, 180, 110)]
    assert "cab-001 cabinet" in out[0].get_text()
    assert "ctp-001 countertop" in out[1].get_text()
    assert out[0].get_drawings()[0]["color"] == pytest.approx((0x25 / 255, 0x63 / 255, 0xEB / 255), abs=0.01)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_export_puts_boxes_where_they_were_drawn_on_rotated_pages(server, rotation):
    doc = upload(server, make_pdf((300, 200, rotation), marker=f"export-rot-{rotation}"))
    width, height = doc["pages"][0]["width"], doc["pages"][0]["height"]
    _, _, body = export(server, doc["id"], [box(1, 10, 30, 50, 40)])

    page = pymupdf.open(stream=body, filetype="pdf")[0]
    assert (page.rect.width, page.rect.height) == (width, height)
    assert drawn_rects(page) == [(10, 30, 60, 70)]


def test_export_skips_objects_on_pages_the_pdf_does_not_have(server):
    doc = upload(server, make_pdf((300, 200, 0), marker="export-skip"))
    status, _, body = export(server, doc["id"], [box(1, 10, 10, 20, 20), box(5, 10, 10, 20, 20, id="cab-002")])
    assert status == 200
    out = pymupdf.open(stream=body, filetype="pdf")
    assert drawn_rects(out[0]) == [(10, 10, 30, 30)]


def test_export_leaves_the_uploaded_document_untouched(server):
    doc = upload(server, make_pdf((300, 200, 0), marker="export-twice"))
    export(server, doc["id"], [box(1, 10, 10, 20, 20)])
    _, _, body = export(server, doc["id"], [box(1, 100, 100, 20, 20)])
    out = pymupdf.open(stream=body, filetype="pdf")
    assert drawn_rects(out[0]) == [(100, 100, 120, 120)]


def test_export_with_a_bad_color_falls_back_to_the_default(server):
    doc = upload(server, make_pdf((300, 200, 0), marker="export-color"))
    _, _, body = export(server, doc["id"], [box(1, 10, 10, 20, 20)], {"cabinet": "red"})
    color = pymupdf.open(stream=body, filetype="pdf")[0].get_drawings()[0]["color"]
    assert color == pytest.approx(serve.hex_to_rgb(serve.DEFAULT_EXPORT_COLOR), abs=0.01)


def test_export_of_an_unknown_document_is_404(server):
    status, _, _ = export(server, "0" * 16, [])
    assert status == 404


@pytest.mark.parametrize(
    "payload",
    [
        b"{not json",
        json.dumps({"objects": [{"page": 1}]}).encode(),
        json.dumps({"objects": [{"page": "one", "bbox": {}}]}).encode(),
    ],
)
def test_bad_export_requests_are_400(server, payload):
    doc = upload(server, make_pdf((300, 200, 0), marker="export-bad"))
    status, _, body = request(f"{server}/api/documents/{doc['id']}/export", payload, "application/json")
    assert status == 400
    assert "bad export request" in json.loads(body)["error"]


# --- the public dataset ---------------------------------------------------------------------


def test_public_dataset_boxes_fit_their_pages(server):
    """Every ground-truth box in the public dataset lies on a page of its drawing."""
    doc = upload(server, (DATASET_DIR / "drawing.pdf").read_bytes())
    project = json.loads((DATASET_DIR / "obj-location.json").read_text())
    assert project["objects"], "the dataset has no objects"
    for obj in project["objects"]:
        assert 1 <= obj["page"] <= len(doc["pages"]), obj["id"]
        page = doc["pages"][obj["page"] - 1]
        bbox = obj["bbox"]
        assert bbox["width"] > 0 and bbox["height"] > 0, obj["id"]
        assert 0 <= bbox["x"] and bbox["x"] + bbox["width"] <= page["width"] + 1, obj["id"]
        assert 0 <= bbox["y"] and bbox["y"] + bbox["height"] <= page["height"] + 1, obj["id"]
