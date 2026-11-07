"""Browser tests for editing boxes: drawing, selecting, moving, resizing, the inspector, undo,
pages, the view tools, keyboard shortcuts, saving and exporting, and drafts kept in localStorage.

Like the import tests, these drive the real page in headless Chromium against the real server,
without the File System Access API, so Save downloads the JSON (the Firefox and Safari path).
Positions are given in PDF points and converted to screen pixels at the current zoom.
"""

import json

import pymupdf
import pytest
from conftest import make_pdf
from ui import (  # noqa: F401  (browser, context and page are fixtures)
    boxes,
    boxes_shown,
    browser,
    click_at,
    click_import,
    context,
    drag,
    draw_box,
    expect,
    fail_on_dialog,
    new_page,
    obj,
    open_pdf,
    page,
    project,
    selected,
    to_screen,
    toast,
    write_json,
)

PAGES = [(300, 200, 0), (300, 200, 0), (200, 400, 0)]
BIG = obj(1, 10, 10, 200, 150, category="elevation")
SMALL = obj(1, 50, 50, 40, 30)


@pytest.fixture
def pdf_file(tmp_path, request):
    path = tmp_path / "kitchen.pdf"
    path.write_bytes(make_pdf(*PAGES, marker=request.node.name))
    return path


@pytest.fixture
def opened(page, pdf_file):
    open_pdf(page, pdf_file)
    return page


@pytest.fixture
def nested(opened, tmp_path):
    """The PDF with a cabinet inside an elevation on page 1 and a countertop on page 2."""
    click_import(opened, write_json(tmp_path, project(BIG, SMALL, obj(2, 20, 20, category="countertop"))))
    expect(opened.locator("#object-count")).to_have_text("3")
    return opened


def near(actual, expected, tolerance=1):
    """Box tuples equal up to ``tolerance`` points; mouse positions are rounded to pixels."""
    assert actual[:2] == expected[:2], (actual, expected)
    assert all(abs(a - e) <= tolerance for a, e in zip(actual[2:], expected[2:])), (actual, expected)


def inspector_coord(page, name):
    return page.locator(f"#inspector [data-coord={name}]")


# --- before a PDF is open ---------------------------------------------------------------------


def test_the_empty_page_invites_a_pdf_and_disables_the_editing_controls(page):
    expect(page.locator("#empty")).to_be_visible()
    expect(page.locator("#object-list")).to_have_text("Open a PDF to start.")
    for control in ["#import-json", "#save", "#export-pdf", "#undo", "#redo", "#prev-page", "#next-page", "#page-select"]:
        expect(page.locator(control)).to_be_disabled()
    expect(page.locator("#status-saved")).to_have_text("")


def test_opening_a_pdf_shows_its_first_page(opened, pdf_file):
    page = opened
    expect(page.locator("#empty")).to_be_hidden()
    expect(page.locator("#project-id")).to_have_value("kitchen")
    expect(page.locator("#doc-name")).to_have_text(pdf_file.name)
    expect(page.locator("#status-page")).to_have_text("300 × 200 pt")
    expect(page.locator("#page-select option")).to_have_text(["Page 1 / 3", "Page 2 / 3", "Page 3 / 3"])
    expect(page.locator("#prev-page")).to_be_disabled()
    expect(page.locator("#next-page")).to_be_enabled()
    expect(page.locator("#object-list")).to_contain_text("Drag on the page to draw the first box")


def test_a_file_the_server_cannot_open_is_reported(page, tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    page.set_input_files("#pdf-input", broken)
    expect(page.locator("#toasts .toast.error", has_text="Could not open broken.pdf")).to_be_visible()
    expect(page.locator("#empty")).to_be_visible()
    expect(page.locator("#loading")).to_be_hidden()


# --- drawing --------------------------------------------------------------------------------


def test_dragging_draws_a_selected_box_in_the_active_category(opened, pdf_file):
    page = opened
    drag(page, (20, 30), (120, 90))

    near(boxes(page)[0], (0, "cabinet", 20, 30, 120, 90))
    assert selected(page) == boxes(page)[0]
    expect(page.locator("#object-count")).to_have_text("1")
    expect(page.locator("#object-list .object.selected .oid")).to_have_text("cab-001")
    expect(page.locator("#inspector")).to_be_visible()
    expect(page.locator("#status-saved")).to_have_text("Unsaved changes")
    expect(page.locator("#doc-name .dirty")).to_have_text("— edited")
    expect(page).to_have_title(f"• {pdf_file.name} — Annotator")


def test_a_box_can_be_drawn_from_any_corner(opened):
    drag(opened, (120, 90), (20, 30))
    near(boxes(opened)[0], (0, "cabinet", 20, 30, 120, 90))


def test_a_drag_past_the_edge_stops_at_the_page(opened):
    drag(opened, (250, 150), (310, 199.9))
    x1 = boxes(opened)[0][4]
    assert x1 == 300


def test_a_click_is_not_a_box(opened):
    click_at(opened, 50, 50)
    assert boxes(opened) == []
    expect(opened.locator("#status-saved")).to_have_text("All changes saved")


def test_the_category_picked_in_the_sidebar_is_used_for_new_boxes(opened):
    page = opened
    page.click("#categories [data-category=countertop]")
    expect(page.locator("#categories .category.active")).to_contain_text("countertop")
    drag(page, (20, 30), (120, 90))
    assert boxes(page)[0][1] == "countertop"
    expect(page.locator("#categories [data-category=countertop] .num")).to_have_text("1")
    expect(page.locator("#object-list .object .oid")).to_have_text("ctp-001")


def test_number_keys_pick_the_category(opened):
    page = opened
    page.keyboard.press("5")
    expect(page.locator("#categories .category.active")).to_contain_text("floor plan")
    drag(page, (20, 30), (120, 90))
    assert boxes(page)[0][1] == "floor plan"


def test_ids_are_numbered_per_category_in_reading_order(opened):
    page = opened
    drag(page, (150, 100), (200, 150))
    drag(page, (20, 20), (60, 60))
    page.keyboard.press("2")  # recategorises the selected box, too
    page.keyboard.press("Escape")
    drag(page, (20, 100), (60, 150))
    expect(page.locator("#object-list .object .oid")).to_have_text(["ctp-001", "ctp-002", "cab-001"])


# --- selecting ------------------------------------------------------------------------------


def test_a_click_selects_the_smallest_box_under_the_cursor(nested):
    page = nested
    click_at(page, 60, 60)
    near(selected(page), (0, "cabinet", 50, 50, 90, 80), 0)
    click_at(page, 20, 20)
    near(selected(page), (0, "elevation", 10, 10, 210, 160), 0)
    click_at(page, 250, 20)
    assert selected(page) is None
    expect(page.locator("#inspector")).to_be_hidden()


def test_dragging_inside_a_box_draws_a_new_one(nested):
    page = nested
    drag(page, (100, 100), (150, 140))
    assert len(boxes(page)) == 4


def test_the_inspector_shows_the_selected_box(nested):
    page = nested
    click_at(page, 60, 60)
    expect(page.locator("#inspector-id")).to_have_text("cab-001 · page 1")
    expect(page.locator("#inspector .chip.active")).to_have_text("cabinet")
    for name, value in {"x0": "50", "y0": "50", "x1": "90", "y1": "80"}.items():
        expect(inspector_coord(page, name)).to_have_value(value)
    expect(page.locator("#inspector-size")).to_have_text("40 × 30 pt")
    expect(page.locator("#overlay .selection .box-label")).to_have_text("cabinet · 40×30")
    expect(page.locator("#overlay .selection .handle")).to_have_count(8)


def test_tab_cycles_through_the_boxes_on_the_page(nested):
    page = nested
    page.keyboard.press("Tab")
    assert selected(page)[1] == "elevation"
    page.keyboard.press("Tab")
    assert selected(page)[1] == "cabinet"
    page.keyboard.press("Tab")
    assert selected(page)[1] == "elevation"
    page.keyboard.press("Shift+Tab")
    assert selected(page)[1] == "cabinet"


def test_escape_deselects(nested):
    page = nested
    click_at(page, 60, 60)
    page.keyboard.press("Escape")
    assert selected(page) is None


def test_clicking_a_listed_box_on_another_page_goes_there_and_selects_it(nested):
    page = nested
    page.click("#object-list .object:has-text('ctp-001')")
    expect(page.locator("#object-list .object.selected .oid")).to_have_text("ctp-001")
    expect(page.locator("#page-select")).to_have_value("1")
    assert selected(page) == (1, "countertop", 20, 20, 60, 50)
    expect(boxes_shown(page)).to_have_count(1)


# --- moving, resizing and nudging -----------------------------------------------------------


def test_dragging_the_edge_of_the_selected_box_moves_it(nested):
    page = nested
    click_at(page, 60, 60)
    drag(page, (50, 55), (70, 75))  # the left edge, clear of its handle at y 65
    near(selected(page), (0, "cabinet", 70, 70, 110, 100))
    assert len(boxes(page)) == 3


def test_a_moved_box_stays_on_the_page(nested):
    page = nested
    click_at(page, 60, 60)
    drag(page, (50, 55), (0, 0))
    assert selected(page) == (0, "cabinet", 0, 0, 40, 30)


def test_dragging_a_handle_resizes_the_box(nested):
    page = nested
    click_at(page, 60, 60)
    drag(page, (90, 80), (120, 100))
    near(selected(page), (0, "cabinet", 50, 50, 120, 100))
    drag(page, (50, 50), (30, 40))
    near(selected(page), (0, "cabinet", 30, 40, 120, 100))


def test_alt_drag_on_the_selected_edge_draws_instead_of_moving(nested):
    page = nested
    click_at(page, 60, 60)
    page.keyboard.down("Alt")
    drag(page, (50, 55), (80, 100))
    page.keyboard.up("Alt")
    assert len(boxes(page)) == 4


def test_escape_during_a_move_puts_the_box_back(nested):
    page = nested
    click_at(page, 60, 60)
    page.mouse.move(*to_screen(page, 50, 55))
    page.mouse.down()
    page.mouse.move(*to_screen(page, 100, 100), steps=5)
    page.keyboard.press("Escape")
    page.mouse.up()
    assert selected(page) == (0, "cabinet", 50, 50, 90, 80)
    expect(page.locator("#undo")).to_be_disabled()


def test_arrow_keys_nudge_the_selected_box(nested):
    page = nested
    click_at(page, 60, 60)
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowDown")
    assert selected(page) == (0, "cabinet", 51, 51, 91, 81)
    page.keyboard.press("Shift+ArrowLeft")
    page.keyboard.press("Shift+ArrowUp")
    assert selected(page) == (0, "cabinet", 41, 41, 81, 71)
    for _ in range(5):
        page.keyboard.press("Shift+ArrowUp")
    assert selected(page) == (0, "cabinet", 41, 0, 81, 30)


# --- the inspector --------------------------------------------------------------------------


def test_typing_coordinates_moves_the_box(nested):
    page = nested
    click_at(page, 60, 60)
    inspector_coord(page, "x1").fill("100")
    inspector_coord(page, "y1").fill("95")
    inspector_coord(page, "y1").press("Enter")
    assert selected(page) == (0, "cabinet", 50, 50, 100, 95)
    expect(page.locator("#inspector-size")).to_have_text("50 × 45 pt")
    expect(page.locator("#object-list .object.selected .geom")).to_have_text("50, 50 · 50×45")


def test_typing_into_a_coordinate_undoes_in_one_step(nested):
    page = nested
    click_at(page, 60, 60)
    field = inspector_coord(page, "x1")
    field.select_text()
    field.press_sequentially("123")  # 1, 12 (both invalid), then 123
    field.press("Enter")
    assert selected(page) == (0, "cabinet", 50, 50, 123, 80)

    page.click("#undo")
    assert selected(page) == (0, "cabinet", 50, 50, 90, 80)
    expect(page.locator("#undo")).to_be_disabled()


def test_coordinates_that_make_an_empty_box_are_marked_and_not_applied(nested):
    page = nested
    click_at(page, 60, 60)
    inspector_coord(page, "x1").fill("40")
    expect(inspector_coord(page, "x0")).to_have_class("input invalid")
    expect(inspector_coord(page, "x1")).to_have_class("input invalid")
    expect(inspector_coord(page, "y0")).not_to_have_class("input invalid")
    assert selected(page) == (0, "cabinet", 50, 50, 90, 80)

    inspector_coord(page, "x1").press("Escape")
    expect(inspector_coord(page, "x1")).to_have_value("90")
    expect(inspector_coord(page, "x1")).not_to_have_class("input invalid")


def test_a_category_chip_recategorises_the_selected_box(nested):
    page = nested
    click_at(page, 60, 60)
    page.click("#inspector .chip[data-category=callout]")
    assert selected(page)[1] == "callout"
    expect(page.locator("#inspector-id")).to_have_text("cal-001 · page 1")
    expect(page.locator("#categories .category.active")).to_contain_text("cabinet")  # new boxes unchanged


def test_a_number_key_recategorises_the_selected_box_and_new_ones(nested):
    page = nested
    click_at(page, 60, 60)
    page.keyboard.press("2")
    assert selected(page)[1] == "countertop"
    expect(page.locator("#categories .category.active")).to_contain_text("countertop")


def test_the_delete_button_and_key_remove_the_selected_box(nested):
    page = nested
    click_at(page, 60, 60)
    page.click("#delete-box")
    expect(page.locator("#object-count")).to_have_text("2")
    assert selected(page) is None

    click_at(page, 20, 20)
    page.keyboard.press("Backspace")
    expect(page.locator("#object-count")).to_have_text("1")
    expect(boxes_shown(page)).to_have_count(0)


# --- undo and redo --------------------------------------------------------------------------


def test_undo_and_redo_with_the_buttons(opened):
    page = opened
    drag(page, (20, 30), (120, 90))
    drag(page, (150, 30), (250, 90))
    expect(page.locator("#object-count")).to_have_text("2")

    page.click("#undo")
    expect(page.locator("#object-count")).to_have_text("1")
    expect(page.locator("#redo")).to_be_enabled()
    page.click("#undo")
    expect(page.locator("#object-count")).to_have_text("")
    expect(page.locator("#undo")).to_be_disabled()

    page.click("#redo")
    page.click("#redo")
    expect(page.locator("#object-count")).to_have_text("2")
    expect(page.locator("#redo")).to_be_disabled()


def test_undo_and_redo_with_the_keyboard(nested):
    page = nested
    click_at(page, 60, 60)
    page.keyboard.press("Delete")
    expect(page.locator("#object-count")).to_have_text("2")
    page.keyboard.press("ControlOrMeta+z")
    expect(page.locator("#object-count")).to_have_text("3")
    page.keyboard.press("ControlOrMeta+Shift+z")
    expect(page.locator("#object-count")).to_have_text("2")
    page.keyboard.press("ControlOrMeta+z")
    page.keyboard.press("ControlOrMeta+y")
    expect(page.locator("#object-count")).to_have_text("2")


def test_a_move_undoes_as_one_step(nested):
    page = nested
    click_at(page, 60, 60)
    drag(page, (50, 55), (100, 100))
    page.keyboard.press("ControlOrMeta+z")
    assert selected(page) == (0, "cabinet", 50, 50, 90, 80)
    expect(page.locator("#undo")).to_be_disabled()


def test_a_new_edit_clears_redo(opened):
    page = opened
    drag(page, (20, 30), (120, 90))
    page.click("#undo")
    drag(page, (150, 30), (250, 90))
    expect(page.locator("#redo")).to_be_disabled()


# --- pages ----------------------------------------------------------------------------------


def test_page_buttons_keys_and_the_select_change_the_page(nested):
    page = nested
    page.click("#next-page")
    expect(page.locator("#page-select")).to_have_value("1")
    expect(page.locator("#prev-page")).to_be_enabled()
    page.keyboard.press("]")
    expect(page.locator("#page-select")).to_have_value("2")
    expect(page.locator("#next-page")).to_be_disabled()
    expect(page.locator("#status-page")).to_have_text("200 × 400 pt")
    page.keyboard.press("[")
    expect(page.locator("#page-select")).to_have_value("1")
    page.select_option("#page-select", "0")
    expect(page.locator("#status-page")).to_have_text("300 × 200 pt")
    expect(page.locator("#prev-page")).to_be_disabled()


def test_only_the_current_pages_boxes_are_drawn(nested):
    page = nested
    expect(boxes_shown(page)).to_have_count(2)
    page.keyboard.press("]")
    expect(boxes_shown(page)).to_have_count(1)
    page.keyboard.press("]")
    expect(boxes_shown(page)).to_have_count(0)


def test_new_boxes_go_on_the_current_page(opened):
    page = opened
    page.keyboard.press("]")
    page.keyboard.press("]")
    expect(page.locator("#status-page")).to_have_text("200 × 400 pt")
    drag(page, (20, 30), (120, 90))
    assert boxes(page)[0][0] == 2
    expect(page.locator("#inspector-id")).to_have_text("cab-001 · page 3")


def test_the_page_select_counts_boxes_per_page(nested):
    expect(nested.locator("#page-select option")).to_have_text(["Page 1 / 3  (2)", "Page 2 / 3  (1)", "Page 3 / 3"])


def test_leaving_a_page_clears_its_selection(nested):
    page = nested
    click_at(page, 60, 60)
    expect(page.locator("#inspector")).to_be_visible()
    page.keyboard.press("]")
    # The page select changes at once, the selection only once the new page has rendered.
    expect(page.locator("#inspector")).to_be_hidden()
    expect(page.locator("#page-select")).to_have_value("1")
    assert selected(page) is None


def test_the_list_shows_all_pages_or_only_the_current_one(nested):
    page = nested
    expect(page.locator("#object-list .page-group-title")).to_have_count(2)
    page.click("[data-scope=page]")
    expect(page.locator("[data-scope=page]")).to_have_class("btn active")
    expect(page.locator("#object-list .object")).to_have_count(2)
    page.keyboard.press("]")
    expect(page.locator("#object-list .object .oid")).to_have_text(["ctp-001"])
    page.keyboard.press("]")
    expect(page.locator("#object-list")).to_have_text("No boxes on this page.")
    page.click("[data-scope=all]")
    expect(page.locator("#object-list .object")).to_have_count(3)


# --- view tools -----------------------------------------------------------------------------


def test_tool_buttons_and_keys_switch_the_tool(opened):
    page = opened
    expect(page.locator("[data-tool=box]")).to_have_class("btn icon active")
    page.click("[data-tool=hand]")
    expect(page.locator("#stage")).to_have_class("stage tool-hand")
    page.keyboard.press("m")
    expect(page.locator("[data-tool=measure]")).to_have_class("btn icon active")
    expect(page.locator("[data-tool=hand]")).not_to_have_class("btn icon active")
    page.keyboard.press("b")
    expect(page.locator("#stage")).to_have_class("stage tool-box")


def test_the_hand_tool_does_not_draw(opened):
    page = opened
    page.keyboard.press("h")
    drag(page, (20, 30), (120, 90))
    assert boxes(page) == []


def test_measuring_shows_the_length_in_inches(opened):
    page = opened
    page.keyboard.press("m")
    drag(page, (10, 10), (82, 10))
    readout = page.locator("#status-measure")
    expect(readout).to_be_visible()
    expect(readout).to_contain_text("1.00 in")
    expect(page.locator("#overlay .measure text")).to_have_text("1.00 in")
    assert boxes(page) == []

    page.keyboard.press("Escape")
    expect(readout).to_be_hidden()
    expect(page.locator("#overlay .measure")).to_have_count(0)


def test_right_drag_measures_with_the_box_tool(opened):
    page = opened
    drag(page, (10, 10), (10, 154), button="right")
    expect(page.locator("#status-measure")).to_contain_text("2.00 in")
    assert boxes(page) == []


def test_the_cursor_position_is_shown_in_points(opened):
    page = opened
    page.mouse.move(*to_screen(page, 100, 50))
    expect(page.locator("#status-cursor")).to_have_text("x 100.0   y 50.0 pt")


def test_boxes_can_be_hidden_and_shown(nested):
    page = nested
    page.click("#toggle-boxes")
    expect(page.locator("#overlay")).to_have_class("overlay boxes-hidden")
    expect(page.locator("#toggle-boxes")).to_have_attribute("title", "Show boxes (O)")
    page.keyboard.press("o")
    expect(page.locator("#overlay")).not_to_have_class("overlay boxes-hidden")
    expect(page.locator("#toggle-boxes")).to_have_attribute("title", "Hide boxes (O)")


def test_zoom_buttons_and_keys(opened):
    page = opened
    fit = page.evaluate("window.annotator.viewer.zoom")
    page.click("#zoom-in")
    expect(page.locator("#zoom-label")).to_have_text(f"{round(fit * 125)}%")
    page.keyboard.press("-")
    page.keyboard.press("-")
    expect(page.locator("#zoom-label")).to_have_text(f"{round(fit / 1.25 * 100)}%")
    page.keyboard.press("0")
    expect(page.locator("#zoom-label")).to_have_text(f"{round(fit * 100)}%")
    page.keyboard.press("=")
    page.click("#zoom-label")
    expect(page.locator("#zoom-label")).to_have_text(f"{round(fit * 100)}%")


def test_boxes_drawn_while_zoomed_in_land_on_the_same_points(opened):
    page = opened
    page.click("#zoom-in")
    page.click("#zoom-in")
    page.evaluate("document.querySelector('#stage').scrollTo(0, 0)")
    drag(page, (20, 30), (60, 50))
    near(boxes(page)[0], (0, "cabinet", 20, 30, 60, 50))


def test_the_help_dialog_opens_and_closes(opened):
    page = opened
    page.keyboard.press("?")
    expect(page.locator("#help-dialog")).to_be_visible()
    page.keyboard.press("h")  # shortcuts are off while the dialog is open
    expect(page.locator("[data-tool=box]")).to_have_class("btn icon active")
    page.click("#help-dialog button[value=close]")
    expect(page.locator("#help-dialog")).to_be_hidden()
    page.click("#help")
    expect(page.locator("#help-dialog")).to_be_visible()


def test_typing_in_the_project_id_does_not_trigger_shortcuts(nested):
    page = nested
    click_at(page, 60, 60)
    page.fill("#project-id", "")
    page.type("#project-id", "kitchen 2 b[")
    expect(page.locator("#project-id")).to_have_value("kitchen 2 b[")
    assert selected(page)[1] == "cabinet"
    expect(page.locator("[data-tool=box]")).to_have_class("btn icon active")
    expect(page.locator("#page-select")).to_have_value("0")
    page.keyboard.press("Backspace")
    expect(page.locator("#object-count")).to_have_text("3")


# --- saving and exporting -------------------------------------------------------------------


def test_save_downloads_the_project_json(nested):
    page = nested
    click_at(page, 60, 60)
    page.keyboard.press("ArrowRight")
    expect(page.locator("#status-saved")).to_have_text("Unsaved changes")

    with page.expect_download() as download:
        page.click("#save")
    assert download.value.suggested_filename == "kitchen-obj-location.json"
    saved = json.loads(open(download.value.path()).read())
    assert saved == project(
        {"id": "elv-001", "category": "elevation", "page": 1, "bbox": {"x": 10, "y": 10, "width": 200, "height": 150}},
        {"id": "cab-001", "category": "cabinet", "page": 1, "bbox": {"x": 51, "y": 50, "width": 40, "height": 30}},
        {"id": "ctp-001", "category": "countertop", "page": 2, "bbox": {"x": 20, "y": 20, "width": 40, "height": 30}},
    )
    expect(toast(page, "Saved 3 objects to kitchen-obj-location.json.")).to_be_visible()
    expect(page.locator("#status-saved")).to_have_text("All changes saved")
    expect(page.locator("#doc-name .dirty")).to_have_count(0)


def test_a_new_project_is_saved_under_its_project_id(opened):
    page = opened
    drag(page, (20, 30), (120, 90))
    page.fill("#project-id", "  galley  ")
    with page.expect_download() as download:
        page.keyboard.press("ControlOrMeta+s")
    assert download.value.suggested_filename == "galley-obj-location.json"
    assert json.loads(open(download.value.path()).read())["project_id"] == "galley"


def test_save_needs_a_project_id(opened):
    page = opened
    drag(page, (20, 30), (120, 90))
    page.fill("#project-id", " ")
    page.click("#save")
    expect(toast(page, "Enter a project ID before saving.")).to_be_visible()
    expect(page.locator("#project-id")).to_be_focused()
    expect(page.locator("#status-saved")).to_have_text("Unsaved changes")


def test_a_saved_file_imports_back_to_the_same_boxes(nested, tmp_path):
    page = nested
    before = boxes(page)
    with page.expect_download() as download:
        page.click("#save")
    saved = tmp_path / "saved-obj-location.json"
    download.value.save_as(saved)
    click_import(page, saved)
    expect(toast(page, "Imported 3 objects from saved-obj-location.json.")).to_be_visible()
    assert boxes(page) == before


def test_export_needs_boxes(opened):
    opened.click("#export-pdf")
    expect(toast(opened, "There are no boxes to export yet.")).to_be_visible()


def test_export_downloads_the_pdf_with_the_boxes_drawn_in(nested):
    page = nested
    with page.expect_download() as download:
        page.keyboard.press("ControlOrMeta+e")
    assert download.value.suggested_filename == "kitchen-annotated.pdf"
    expect(toast(page, "Exported 3 boxes to kitchen-annotated.pdf.")).to_be_visible()
    with pymupdf.open(download.value.path()) as doc:
        assert doc.page_count == len(PAGES)
        assert len(doc[0].get_drawings()) >= 2
        assert "cab-001" in doc[0].get_text() and "ctp-001" in doc[1].get_text()


# --- unsaved work ---------------------------------------------------------------------------


def wait_for_draft(page):
    page.wait_for_function("() => Object.keys(localStorage).some((k) => k.startsWith('annotator:draft:'))")


def test_unsaved_boxes_can_be_restored_after_the_tab_is_closed(opened, context, server, pdf_file):
    drag(opened, (20, 30), (120, 90))
    opened.fill("#project-id", "galley")
    wait_for_draft(opened)
    drawn = boxes(opened)
    opened.close()

    page = new_page(context, server)
    open_pdf(page, pdf_file)
    offer = toast(page, "Unsaved work on this PDF from")
    expect(offer).to_contain_text("1 boxes.")
    offer.get_by_role("button", name="Restore").click()
    assert boxes(page) == drawn
    expect(page.locator("#project-id")).to_have_value("galley")
    expect(page.locator("#status-saved")).to_have_text("Unsaved changes")
    assert not page.errors, page.errors


def test_saving_drops_the_draft(opened, context, server, pdf_file):
    drag(opened, (20, 30), (120, 90))
    wait_for_draft(opened)
    with opened.expect_download():
        opened.click("#save")
    opened.wait_for_function("() => !Object.keys(localStorage).some((k) => k.startsWith('annotator:draft:'))")
    opened.close()

    page = new_page(context, server)
    open_pdf(page, pdf_file)
    page.wait_for_timeout(300)
    expect(toast(page, "Unsaved work")).to_have_count(0)


def test_opening_another_pdf_asks_before_discarding_unsaved_boxes(opened, tmp_path):
    page = opened
    drag(page, (20, 30), (120, 90))
    other = tmp_path / "other.pdf"
    other.write_bytes(make_pdf((300, 200, 0), marker="other"))

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.set_input_files("#pdf-input", other)
    expect(page.locator("#doc-name")).to_contain_text("kitchen.pdf")
    expect(page.locator("#object-count")).to_have_text("1")

    page.once("dialog", lambda dialog: dialog.accept())
    page.set_input_files("#pdf-input", other)
    expect(page.locator("#doc-name")).to_have_text("other.pdf")
    expect(page.locator("#object-count")).to_have_text("")
    expect(page.locator("#project-id")).to_have_value("other")


def test_opening_another_pdf_after_saving_does_not_ask(nested, tmp_path):
    page = nested
    fail_on_dialog(page)
    other = tmp_path / "other.pdf"
    other.write_bytes(make_pdf((300, 200, 0), marker="other"))
    page.set_input_files("#pdf-input", other)
    expect(page.locator("#doc-name")).to_have_text("other.pdf")
