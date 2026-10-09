"""Browser tests for importing a project JSON: the Import button, dropping files on the page, and
the checks around them (PDF first, replacing unsaved boxes, skipped objects, broken files).

These drive the real page in headless Chromium with Playwright, against the real server. Chromium
has the File System Access API, so the Import button opens `showOpenFilePicker`, which Playwright
cannot answer; most tests remove it so the page falls back to its `<input type=file>`, the path
Firefox and Safari take. `test_import_with_the_file_picker_saves_back_to_the_same_file` keeps it
and stubs the picker instead, because that path is what lets Save write back to the imported file.
"""

import json
import os

import pytest
from conftest import DATASET_DIR, make_pdf

# Skipped locally without Playwright; in CI a missing Playwright must fail, not skip silently.
if os.environ.get("CI"):
    import playwright.sync_api as playwright_api
else:
    playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect

PAGE = (300, 200, 0)
NO_FILE_PICKER = "delete window.showOpenFilePicker; delete window.showSaveFilePicker;"


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser, server):
    context = browser.new_context()
    page = context.new_page()
    page.add_init_script(NO_FILE_PICKER)
    errors = []
    page.on("pageerror", lambda err: errors.append(err))
    page.goto(f"{server}/")
    yield page
    context.close()
    assert not errors, errors


@pytest.fixture
def pdf_file(tmp_path, request):
    path = tmp_path / "kitchen.pdf"
    path.write_bytes(make_pdf(PAGE, PAGE, marker=request.node.name))
    return path


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


def drop_files(page, *files):
    """Fires a drop event carrying ``files``, given as ``(name, mime type, bytes)``."""
    payload = [{"name": name, "type": mime, "bytes": list(data)} for name, mime, data in files]
    page.evaluate(
        """(files) => {
          const transfer = new DataTransfer();
          for (const f of files) transfer.items.add(new File([new Uint8Array(f.bytes)], f.name, { type: f.type }));
          window.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true, cancelable: true }));
        }""",
        payload,
    )


def boxes_shown(page):
    return page.locator("#overlay g.box")


def toast(page, text):
    return page.locator("#toasts .toast", has_text=text)


def fail_on_dialog(page):
    page.on("dialog", lambda dialog: pytest.fail(f"unexpected dialog: {dialog.message}"))


# --- the Import button --------------------------------------------------------------------


def test_import_loads_the_boxes_and_project_id(page, pdf_file, tmp_path):
    json_file = write_json(tmp_path, project(obj(1, 10, 20), obj(1, 100, 20, category="countertop"), obj(2, 10, 10)))
    open_pdf(page, pdf_file)
    click_import(page, json_file)

    expect(toast(page, f"Imported 3 objects from {json_file.name}.")).to_be_visible()
    expect(page.locator("#object-count")).to_have_text("3")
    expect(page.locator("#project-id")).to_have_value("kitchen-project")
    expect(page.locator("#doc-name")).to_contain_text(json_file.name)
    expect(page.locator("#status-saved")).to_have_text("All changes saved")
    expect(boxes_shown(page)).to_have_count(2)  # page 1 only


def test_imported_boxes_keep_their_coordinates(page, pdf_file, tmp_path):
    open_pdf(page, pdf_file)
    click_import(page, write_json(tmp_path, project(obj(1, 12, 34, 56, 78))))
    expect(page.locator("#object-list .object .geom")).to_have_text("12, 34 · 56×78")
    rect = page.locator("#overlay g.box rect.box-line")
    assert [rect.get_attribute(a) for a in ("x", "y", "width", "height")] == ["12", "34", "56", "78"]


def test_import_without_a_project_id_keeps_the_one_from_the_pdf_name(page, pdf_file, tmp_path):
    open_pdf(page, pdf_file)
    click_import(page, write_json(tmp_path, {"objects": [obj(1, 10, 10)]}))
    expect(page.locator("#object-count")).to_have_text("1")
    expect(page.locator("#project-id")).to_have_value("kitchen")


def test_import_is_disabled_until_a_pdf_is_open(page, pdf_file):
    expect(page.locator("#import-json")).to_be_disabled()
    open_pdf(page, pdf_file)
    expect(page.locator("#import-json")).to_be_enabled()


def test_import_reports_objects_on_pages_the_pdf_does_not_have(page, pdf_file, tmp_path):
    open_pdf(page, pdf_file)
    click_import(page, write_json(tmp_path, project(obj(1, 10, 10), obj(2, 10, 10), obj(3, 10, 10))))
    expect(toast(page, "Imported 2 objects")).to_contain_text("Skipped 1 that reference pages outside this PDF.")
    expect(page.locator("#object-count")).to_have_text("2")


@pytest.mark.parametrize(
    "content, reason",
    [("{ not json", "JSON"), ('{"project_id": "p"}', 'expected an "objects" list')],
)
def test_a_broken_file_is_reported_and_changes_nothing(page, pdf_file, tmp_path, content, reason):
    open_pdf(page, pdf_file)
    click_import(page, write_json(tmp_path, project(obj(1, 10, 10))))
    expect(page.locator("#object-count")).to_have_text("1")

    click_import(page, write_json(tmp_path, content, "broken.json"))
    expect(toast(page, "Could not import broken.json")).to_contain_text(reason)
    expect(page.locator("#object-count")).to_have_text("1")


# --- replacing boxes ----------------------------------------------------------------------


def test_import_asks_before_replacing_unsaved_boxes_and_can_be_cancelled(page, pdf_file, tmp_path):
    open_pdf(page, pdf_file)
    draw_box(page)
    expect(page.locator("#object-count")).to_have_text("1")

    with page.expect_event("dialog") as dialog:
        click_import(page, write_json(tmp_path, project(obj(1, 10, 10), obj(1, 60, 10))))
    assert dialog.value.message == "Replace the 1 unsaved boxes with the 2 in kitchen-obj-location.json?"
    dialog.value.dismiss()

    expect(page.locator("#object-count")).to_have_text("1")
    expect(page.locator("#status-saved")).to_have_text("Unsaved changes")
    expect(toast(page, "Imported")).to_have_count(0)


def test_import_replaces_unsaved_boxes_when_confirmed(page, pdf_file, tmp_path):
    open_pdf(page, pdf_file)
    draw_box(page)
    page.once("dialog", lambda dialog: dialog.accept())
    click_import(page, write_json(tmp_path, project(obj(1, 10, 10), obj(1, 60, 10))))
    expect(page.locator("#object-count")).to_have_text("2")
    expect(page.locator("#status-saved")).to_have_text("All changes saved")


def test_importing_over_saved_boxes_does_not_ask(page, pdf_file, tmp_path):
    fail_on_dialog(page)
    open_pdf(page, pdf_file)
    click_import(page, write_json(tmp_path, project(obj(1, 10, 10))))
    expect(page.locator("#object-count")).to_have_text("1")
    click_import(page, write_json(tmp_path, project(obj(1, 10, 10), obj(1, 60, 10)), "other.json"))
    expect(page.locator("#object-count")).to_have_text("2")


def test_import_cannot_be_undone_into_the_previous_boxes(page, pdf_file, tmp_path):
    open_pdf(page, pdf_file)
    draw_box(page)
    page.once("dialog", lambda dialog: dialog.accept())
    click_import(page, write_json(tmp_path, project(obj(1, 10, 10), obj(1, 60, 10))))
    expect(page.locator("#undo")).to_be_disabled()


# --- drag and drop ------------------------------------------------------------------------


def test_dropping_the_pdf_and_json_together_opens_both(page, pdf_file, tmp_path):
    json_file = write_json(tmp_path, project(obj(1, 10, 10), obj(2, 10, 10)))
    drop_files(
        page,
        (json_file.name, "application/json", json_file.read_bytes()),
        (pdf_file.name, "application/pdf", pdf_file.read_bytes()),
    )
    expect(toast(page, "Imported 2 objects")).to_be_visible()
    expect(page.locator("#object-count")).to_have_text("2")
    expect(page.locator("#doc-name")).to_contain_text(pdf_file.name)
    expect(page.locator("#doc-name")).to_contain_text(json_file.name)


def test_dropping_a_json_after_the_pdf_imports_it(page, pdf_file, tmp_path):
    open_pdf(page, pdf_file)
    json_file = write_json(tmp_path, project(obj(1, 10, 10)))
    drop_files(page, (json_file.name, "application/json", json_file.read_bytes()))
    expect(page.locator("#object-count")).to_have_text("1")


def test_dropping_a_json_without_a_pdf_asks_for_the_pdf(page, tmp_path):
    json_file = write_json(tmp_path, project(obj(1, 10, 10)))
    drop_files(page, (json_file.name, "application/json", json_file.read_bytes()))
    expect(toast(page, "Drop the PDF together with (or before) its JSON.")).to_be_visible()
    expect(page.locator("#object-count")).to_have_text("")


# --- the File System Access picker (Chromium, Edge) ---------------------------------------

FAKE_PICKER = """
window.__written = null;
window.showOpenFilePicker = async () => {
  if (window.__cancelPicker) throw new DOMException("The user aborted a request.", "AbortError");
  const file = new File([window.__json], "picked-obj-location.json", { type: "application/json" });
  return [{
    name: file.name,
    getFile: async () => file,
    queryPermission: async () => "granted",
    createWritable: async () => {
      let text = "";
      return { write: async (chunk) => { text += chunk; }, close: async () => { window.__written = text; } };
    },
  }];
};
window.showSaveFilePicker = async () => { throw new Error("Save should write to the imported file"); };
"""


@pytest.fixture
def picker_page(browser, server):
    context = browser.new_context()
    page = context.new_page()
    page.add_init_script(FAKE_PICKER)
    page.goto(f"{server}/")
    yield page
    context.close()


def test_import_with_the_file_picker_saves_back_to_the_same_file(picker_page, pdf_file):
    page = picker_page
    open_pdf(page, pdf_file)
    page.evaluate("(json) => { window.__json = json; }", json.dumps(project(obj(1, 10, 20), obj(2, 30, 40))))
    page.click("#import-json")
    expect(page.locator("#object-count")).to_have_text("2")

    draw_box(page)
    expect(page.locator("#status-saved")).to_have_text("Unsaved changes")
    page.click("#save")
    expect(toast(page, "Saved 3 objects to picked-obj-location.json.")).to_be_visible()

    saved = json.loads(page.evaluate("window.__written"))
    assert saved["project_id"] == "kitchen-project"
    assert len(saved["objects"]) == 3
    assert {"page": 2, "bbox": {"x": 30, "y": 40, "width": 40, "height": 30}}.items() <= {
        k: v for o in saved["objects"] if o["page"] == 2 for k, v in o.items()
    }.items()


def test_cancelling_the_file_picker_is_not_an_error(picker_page, pdf_file):
    page = picker_page
    open_pdf(page, pdf_file)
    page.evaluate("window.__cancelPicker = true")
    page.click("#import-json")
    page.wait_for_timeout(300)
    expect(page.locator("#toasts .toast.error")).to_have_count(0)


# --- the public dataset -------------------------------------------------------------------


def test_the_public_dataset_imports_completely(page):
    open_pdf(page, DATASET_DIR / "drawing.pdf")
    expected = len(json.loads((DATASET_DIR / "obj-location.json").read_text())["objects"])
    click_import(page, DATASET_DIR / "obj-location.json")
    expect(toast(page, f"Imported {expected} objects from obj-location.json.")).to_be_visible()
    expect(toast(page, "Skipped")).to_have_count(0)
    expect(page.locator("#object-count")).to_have_text(str(expected))
