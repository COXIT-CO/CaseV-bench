#!/usr/bin/env python3
"""Serve the annotation tool on localhost and open it in the browser.

Pages are rendered here with PyMuPDF, the renderer the benchmark runner uses, so the boxes
drawn in the browser are in exactly the coordinate space the scorer reads. The browser only
uploads the PDF and shows the PNGs; nothing leaves this machine.

    POST /api/documents                       body: PDF bytes → {"id", "pages": [{"width", "height"}]}
    GET  /api/documents/<id>/pages/<n>.png    ?scale=<s>[&clip=x0,y0,x1,y1]  (n is 0-based, clip in PDF points)
    POST /api/documents/<id>/export           body: {"objects": [...], "colors": {category: "#rrggbb"}}
                                              → the PDF with every object drawn on its page
"""

import argparse
import functools
import hashlib
import http.server
import json
import re
import threading
import webbrowser
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymupdf

ROOT = Path(__file__).resolve().parent
MAX_OPEN_DOCUMENTS = 4
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_RENDER_PIXELS = 40_000_000
PAGE_URL = re.compile(r"^/api/documents/([0-9a-f]{16})/pages/(\d+)\.png$")
EXPORT_URL = re.compile(r"^/api/documents/([0-9a-f]{16})/export$")
EXPORT_LINE_WIDTH = 1.5  # points
EXPORT_FILL_OPACITY = 0.12
EXPORT_LABEL_SIZE = 8  # points
DEFAULT_EXPORT_COLOR = "#64748b"

# PyMuPDF is not thread-safe; the server is, so every document access goes through this lock.
_lock = threading.Lock()
_documents: "OrderedDict[str, pymupdf.Document]" = OrderedDict()
_sources: "dict[str, bytes]" = {}  # original bytes, so exports start from an untouched copy


def open_document(data: bytes) -> dict:
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    with _lock:
        doc = _documents.get(doc_id)
        if doc is None:
            doc = pymupdf.open(stream=data, filetype="pdf")
            _documents[doc_id] = doc
            _sources[doc_id] = data
            while len(_documents) > MAX_OPEN_DOCUMENTS:
                old_id, old_doc = _documents.popitem(last=False)
                old_doc.close()
                del _sources[old_id]
        _documents.move_to_end(doc_id)
        # page.rect has the page rotation applied — the space boxes are stored in.
        pages = [{"width": page.rect.width, "height": page.rect.height} for page in doc]
    return {"id": doc_id, "pages": pages}


def render_page(doc_id: str, index: int, scale: float, clip: "list[float] | None") -> bytes:
    with _lock:
        doc = _documents.get(doc_id)
        if doc is None:
            raise LookupError("document is not open (the server may have restarted)")
        if not 0 <= index < doc.page_count:
            raise LookupError("no such page")
        page = doc[index]
        rect = page.rect if clip is None else pymupdf.Rect(clip) & page.rect
        if rect.is_empty:
            raise ValueError("clip is outside the page")
        if rect.width * rect.height * scale * scale > MAX_RENDER_PIXELS:
            raise ValueError("requested image is too large")
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
        return pixmap.tobytes("png")


def export_document(doc_id: str, objects: list, colors: dict) -> bytes:
    """A copy of the PDF with each object drawn as an outlined, tinted box with its id."""
    with _lock:
        data = _sources.get(doc_id)
    if data is None:
        raise LookupError("document is not open (the server may have restarted)")
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        by_page: "dict[int, list]" = {}
        for obj in objects:
            index = int(obj["page"]) - 1
            if 0 <= index < doc.page_count:
                by_page.setdefault(index, []).append(obj)
        # One shape per page, committed once: committing per box rewrites the page every time.
        for index, page_objects in by_page.items():
            page = doc[index]
            shape = page.new_shape()
            for obj in page_objects:
                bbox = obj["bbox"]
                # Boxes are in the rotated (displayed) page space; drawing uses the unrotated one.
                shown = pymupdf.Rect(bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"])
                color = hex_to_rgb(colors.get(obj.get("category"), DEFAULT_EXPORT_COLOR))
                shape.draw_rect(shown * page.derotation_matrix)
                shape.finish(color=color, fill=color, width=EXPORT_LINE_WIDTH, fill_opacity=EXPORT_FILL_OPACITY)
                # Label just above the top-left corner as displayed, kept upright on rotated pages.
                label = f"{obj.get('id', '')} {obj.get('category', '')}".strip()
                anchor = pymupdf.Point(shown.x0 + 1, shown.y0 - 2) * page.derotation_matrix
                shape.insert_text(anchor, label, fontsize=EXPORT_LABEL_SIZE, color=color, rotate=page.rotation)
            shape.commit()
        return doc.tobytes(garbage=1, deflate=True)
    finally:
        doc.close()


def hex_to_rgb(value: str) -> "tuple[float, float, float]":
    value = value.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = DEFAULT_EXPORT_COLOR.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Always serve the current files, so a `git pull` shows up on reload.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        match = PAGE_URL.match(url.path)
        if not match:
            super().do_GET()
            return
        try:
            query = parse_qs(url.query)
            scale = float(query.get("scale", ["1"])[0])
            if not 0.01 <= scale <= 16:
                raise ValueError("scale out of range")
            clip = [float(v) for v in query["clip"][0].split(",")] if "clip" in query else None
            if clip is not None and len(clip) != 4:
                raise ValueError("clip needs four numbers")
            png = render_page(match[1], int(match[2]), scale, clip)
        except LookupError as err:
            self.send_json(404, {"error": str(err)})
            return
        except ValueError as err:
            self.send_json(400, {"error": str(err)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.end_headers()
        self.wfile.write(png)

    def do_POST(self):
        path = urlparse(self.path).path
        export = EXPORT_URL.match(path)
        if export:
            self.export(export[1])
            return
        if path != "/api/documents":
            self.send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_UPLOAD_BYTES:
            self.send_json(413, {"error": "missing or too large upload"})
            return
        try:
            self.send_json(200, open_document(self.rfile.read(length)))
        except Exception as err:  # PyMuPDF raises its own types for broken files
            self.send_json(400, {"error": f"not a readable PDF: {err}"})

    def export(self, doc_id):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            pdf = export_document(doc_id, body.get("objects", []), body.get("colors", {}))
        except LookupError as err:
            self.send_json(404, {"error": str(err)})
            return
        except (ValueError, KeyError, TypeError) as err:
            self.send_json(400, {"error": f"bad export request: {err}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(pdf)))
        self.end_headers()
        self.wfile.write(pdf)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    handler = functools.partial(Handler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        url = f"http://localhost:{args.port}/"
        print(f"Annotation tool running at {url}  (Ctrl+C to stop)")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
