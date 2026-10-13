"""Helpers for the browser tests: Playwright, the page fixtures and the gestures they repeat.

Importing this module skips the importing test module locally when Playwright is not installed;
in CI a missing Playwright must fail, not skip silently.
"""

import json
import os

import pytest

if os.environ.get("CI"):
    import playwright.sync_api as playwright_api
else:
    playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect

# Chromium has the File System Access API, which Playwright cannot answer. Without it the page
# falls back to `<input type=file>` for opening and a download for saving, as in Firefox and Safari.
NO_FILE_PICKER = "delete window.showOpenFilePicker; delete window.showSaveFilePicker;"


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def context(browser):
    context = browser.new_context(accept_downloads=True)
    yield context
    context.close()


@pytest.fixture
def page(context, server):
    page = new_page(context, server)
    yield page
    assert not page.errors, page.errors


def new_page(context, server):
    """A page with the fallback file inputs, collecting uncaught errors in ``page.errors``."""
    page = context.new_page()
    page.add_init_script(NO_FILE_PICKER)
    page.errors = []
    page.on("pageerror", lambda err: page.errors.append(err))
    page.goto(f"{server}/")
    return page


def project(*objects, project_id="kitchen-project"):
    return {"project_id": project_id, "objects": list(objects)}


def obj(page, x, y, width=40, height=30, category="cabinet"):
    return {"id": "x", "category": category, "page": page, "bbox": {"x": x, "y": y, "width": width, "height": height}}


def write_json(tmp_path, data, name="kitchen-obj-location.json"):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def open_pdf(page, path):
    page.set_input_files("#pdf-input", path)
    expect(page.locator("#page")).to_be_visible()
    expect(page.locator("#loading")).to_be_hidden()


def click_import(page, path):
    with page.expect_file_chooser() as chooser:
        page.click("#import-json")
    chooser.value.set_files(path)


def draw_box(page):
    """Drags out a box in the middle of the page with the default box tool."""
    area = page.locator("#overlay").bounding_box()
    x, y = area["x"] + area["width"] * 0.4, area["y"] + area["height"] * 0.4
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 60, y + 40, steps=5)
    page.mouse.up()


def to_screen(page, x, y):
    """PDF points on the current page → screen pixels, at the current zoom and scroll."""
    area = page.locator("#page").bounding_box()
    zoom = page.evaluate("window.annotator.viewer.zoom")
    return area["x"] + x * zoom, area["y"] + y * zoom


def drag(page, start, end, button="left"):
    """Drags between two points given in PDF points."""
    page.mouse.move(*to_screen(page, *start))
    page.mouse.down(button=button)
    page.mouse.move(*to_screen(page, *end), steps=5)
    page.mouse.up(button=button)


def click_at(page, x, y):
    page.mouse.click(*to_screen(page, x, y))


def boxes(page):
    """The boxes in the store, as ``(page, category, x0, y0, x1, y1)``, in saved order."""
    return [
        tuple(b)
        for b in page.evaluate(
            """() => window.annotator.store.boxes
                .map((b) => [b.page, b.category, b.x0, b.y0, b.x1, b.y1])
                .sort((a, b) => a[0] - b[0] || a[3] - b[3] || a[2] - b[2])"""
        )
    ]


def selected(page):
    """The selected box as ``(page, category, x0, y0, x1, y1)``, or None."""
    box = page.evaluate(
        "() => { const b = window.annotator.store.selected; return b && [b.page, b.category, b.x0, b.y0, b.x1, b.y1]; }"
    )
    return tuple(box) if box else None


def boxes_shown(page):
    return page.locator("#overlay g.box")


def toast(page, text):
    return page.locator("#toasts .toast", has_text=text)


def fail_on_dialog(page):
    page.on("dialog", lambda dialog: pytest.fail(f"unexpected dialog: {dialog.message}"))
