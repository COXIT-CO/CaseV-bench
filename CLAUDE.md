# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

CaseV-Bench is a benchmark dataset + tooling repo for evaluating an object-detection system (referred to as "Autoscan/Archiscan") that finds cabinets, countertops, elevations, etc. in architectural PDF drawings. It has two parts:

- `dataset/` — the benchmark itself: per-project PDF drawings with hand-annotated ground-truth JSON (object counts and bounding-box locations).

## Running the annotation tool

Requires **Python 3.12** specifically, plus system Tk (`brew install python-tk@3.12` on macOS).

```bash
cd annotation_tool
make start-test-data-annotator
```

This creates/reuses `.venv_quality_tests_annotation`, installs `annotation/requirements.txt` (pillow, PyMuPDF), and launches `annotation/test_data_annotator.py`. There is no separate lint/test command configured — `annotation/test_data_annotator.py` is the app entry point, not a test file.

Usage docs and walkthrough videos are in `annotation_tool/annotation/README.md`.

## Annotation tool architecture

`test_data_annotator.py` is a thin composition root (`TestDataAnnotator` class) that wires together independent manager modules under `annotation_tool/annotation/`. There's no framework — each module owns one concern and talks to the others via direct references passed in `__init__`:

- `pdf/pdf_handler.py` (`PDFHandler`) — opens PDFs via PyMuPDF (fitz), tracks current page/zoom, renders pages to Tk-displayable images, and converts between canvas pixel coordinates and PDF coordinates (PDF DPI is fixed at 72 in `config/constants.py`).
- `ui/ui_components.py` (`UIComponents`) — builds the static Tk widget tree (toolbar, canvas, rectangles list panel, details panel, status bar) and exposes update methods; holds no business logic.
- `rectangles/rectangle_manager.py` (`RectangleManager`) — the largest module; owns the in-memory list of annotated bounding boxes ("cabinets") per page, draws/redraws them on the canvas, handles the rectangles-list sidebar, click/double-click selection, delete icons, and JSON import/export of per-page annotations.
- `events/event_handler.py` (`EventHandler`) — binds and dispatches raw Tk mouse events (left button = draw/select/edit rectangle, middle button = pan, right button = draw a reference line for scale, wheel = zoom) and forwards results into `RectangleManager`/`PDFHandler`.
- `dialogs/dialog_manager.py` (`DialogManager`) — modal dialogs for entering/editing a cabinet's details (subclass description, width/height/depth in inches, times-referenced, rooms) and for choosing a save location.
- `data_manager/data_manager.py` (`DataManager`) — pure JSON (de)serialization between the in-app rectangle representation and the on-disk annotation schema; also does field-level validation (e.g. `x1 > x0`, subclass description required).
- `config/constants.py` — all magic strings/numbers live here: JSON key names, UI dimensions, colors, dialog sizes, validation/status messages. Prefer adding new constants here over inlining literals.

Coordinate flow: mouse events (canvas pixels) → `PDFHandler.convert_canvas_to_pdf_coordinates` → stored as PDF-space `x0,y0,x1,y1` in rectangle data → converted back for drawing via `convert_pdf_to_canvas_coordinates`. Dimensions in inches are computed separately from a user-drawn reference line (`calculate_line_length_inches`), not derived from the bbox.

macOS gets special handling throughout (`test_data_annotator.py`, `rectangle_manager.py`, `pdf_handler.py`): `NSAutoreleasePool` management, periodic forced `gc.collect()`, and a custom `while True: root.update_idletasks(); root.update()` loop instead of `root.mainloop()`, all to work around PyMuPDF/Tk memory/rendering issues on macOS. Preserve this pattern when touching PDF rendering or page navigation code.

## Annotation JSON schema

Per-page ground truth is saved as one JSON file per PDF page, named `<page_number+1>.json`, under:

```
<dir-with-name-of-the-PDF>/expected_per_page_data/<page>.json
```

Shape (see `config/constants.py` `JSON_*` keys and `data_manager.py` for the canonical mapping):

```json
{
  "page_number": "Page 1",
  "cabinets": [
    {
      "identifying_properties": {
        "subclass_description": "...",
        "x0": 0, "y0": 0, "x1": 0, "y1": 0
      },
      "dimensions_in_inches": { "width": 0, "height": 0, "depth": 0 },
      "ecall_data": { "times_referenced": 0, "rooms": [] }
    }
  ]
}
```

This differs from the top-level `dataset/project-*/prj*-obj-location.json` files, which use a flatter `{"project_id", "objects": [{"id","category","page","bbox":{"x","y","width","height"}}]}` shape, and `prj*-obj-count.json`, which is just a category → count summary. When adding conversion/scoring code, don't conflate these two schemas.

Example annotated pages for reference/testing the tool live in `annotation_tool/test_data/example(1)/` and `example(2)/`.

## Testing scope
When testing or verifying cabinet-detection changes, always use only this file:
`dataset/project-0001/prj0001.pdf.pdf`

Use only page 1 of this PDF for tests — do not process or test against any other page or any other file in `dataset/`, even if other project folders are present.