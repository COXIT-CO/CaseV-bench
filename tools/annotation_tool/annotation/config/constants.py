#!/usr/bin/env python3
"""
Configuration constants for the PDF Coordinate Viewer application.
"""

# Application configuration
APP_TITLE = "PDF Coordinate Viewer"
APP_GEOMETRY = "1200x800"

# Zoom settings
MIN_ZOOM_FACTOR = 0.25
MAX_ZOOM_FACTOR = 5.0
ZOOM_STEP = 1.25

# Rectangle drawing
MIN_RECTANGLE_AREA = 400  # pixels (20x20 minimum)

# Canvas colors
CANVAS_BG = "white"
RECTANGLE_DRAWING_COLOR = "yellow"
RECTANGLE_FINAL_COLOR = "red"
RECTANGLE_FILL_COLOR = "lightblue"
RECTANGLE_EDITING_COLOR = "orange"
RECTANGLE_EDITING_FILL = "lightyellow"
RECTANGLE_ERROR_COLOR = "red"
RECTANGLE_ERROR_FILL = "pink"

# Temporary line settings
TEMP_LINE_COLOR = "orange"
TEMP_LINE_WIDTH = 3
TEMP_TEXT_COLOR = "orange"
TEMP_TEXT_FONT = ("Arial", 30, "bold")

# Delete icon settings
DELETE_ICON_SIZE = 16
DELETE_ICON_COLOR = "red"
DELETE_ICON_OUTLINE = "darkred"
DELETE_ICON_X_COLOR = "white"
DELETE_ICON_X_WIDTH = 2
DELETE_ICON_X_OFFSET = 4

# PDF settings
PDF_DPI = 72  # Standard PDF DPI

# UI styling
TOOLBAR_PADDING = (0, 5)
CONTENT_PADDING = (5, 0)
STATUS_PADDING = (5, 0)

# Dialog settings
OBJECT_DETAILS_DIALOG_SIZE = "500x500"
EDIT_DIALOG_SIZE = "600x600"
DELETE_DIALOG_SIZE = "400x250"
SAVE_DIALOG_SIZE = "400x250"

# Font settings
TITLE_FONT = ("Arial", 12, "bold")
SECTION_FONT = ("Arial", 10, "bold")
EDIT_TITLE_FONT = ("Arial", 14, "bold")
COORDINATES_FONT = ("Courier", 10)
SMALL_FONT = ("Arial", 9)
TINY_FONT = ("Courier", 8)
ERROR_FONT = ("Arial", 9)

# Panel weights
LEFT_PANEL_WEIGHT = 1
MIDDLE_PANEL_WEIGHT = 24
PDF_PANEL_WEIGHT = 3
DETAILS_PANEL_WEIGHT = 1

# Status messages
NO_PDF_LOADED = "No PDF loaded"
EMPTY_RECTANGLES_MESSAGE = "No rectangles created yet"
DETAILS_PANEL_DEFAULT = "Cabinet Details Panel"

# File types
PDF_FILE_TYPES = [("PDF files", "*.pdf"), ("All files", "*.*")]

# Error messages
REQUIRED_FIELD_ERROR = "Required Field Missing"
INVALID_INPUT_ERROR = "Invalid Input"
COORDINATE_ERROR = "Invalid Coordinates"

# Success messages
SAVE_SUCCESS = "Project data saved successfully!"

# Validation messages
X1_GREATER_THAN_X0 = "X1 must be greater than X0."
Y1_GREATER_THAN_Y0 = "Y1 must be greater than Y0."
PROJECT_ID_REQUIRED = "Please enter a project ID."

# Dialog titles
OBJECT_DETAILS_TITLE = "Enter Object Details"
EDIT_RECTANGLE_TITLE = "Edit Rectangle Details"
DELETE_RECTANGLE_TITLE = "Delete Rectangle"
SAVE_PROJECT_TITLE = "Save Project Data"

# Button labels
OPEN_PDF_BUTTON = "Open PDF"
PREVIOUS_BUTTON = "Previous"
NEXT_BUTTON = "Next"
ZOOM_IN_BUTTON = "Zoom In"
ZOOM_OUT_BUTTON = "Zoom Out"
EDIT_BUTTON = "Edit"
DELETE_BUTTON = "Delete"
SAVE_BUTTON = "Save"
OK_BUTTON = "OK"
CANCEL_BUTTON = "Cancel"

# Label texts
COORDINATES_LABEL = "Coordinates:"
CATEGORY_LABEL = "Category:"
CABINET_ANNOTATIONS_LABEL = "Cabinet Annotations"
OBJECT_DETAILS_LABEL = "Object Details"
PROJECT_ID_LABEL = "Project ID:"

# Category options
CATEGORY_CABINET = "cabinet"
CATEGORY_COUNTERTOP = "countertop"
CATEGORY_ELEVATION = "elevation"
CATEGORY_CALLOUT = "callout"
CATEGORY_FLOOR_PLAN = "floor plan"
CATEGORY_OPTIONS = [
    CATEGORY_CABINET,
    CATEGORY_COUNTERTOP,
    CATEGORY_ELEVATION,
    CATEGORY_CALLOUT,
    CATEGORY_FLOOR_PLAN,
]
CATEGORY_ID_PREFIXES = {
    CATEGORY_CABINET: "cab",
    CATEGORY_COUNTERTOP: "ctp",
    CATEGORY_ELEVATION: "elv",
    CATEGORY_CALLOUT: "cal",
    CATEGORY_FLOOR_PLAN: "flp",
}

# JSON structure keys
JSON_PROJECT_ID = "project_id"
JSON_OBJECTS = "objects"
JSON_ID = "id"
JSON_CATEGORY = "category"
JSON_PAGE = "page"
JSON_BBOX = "bbox"
JSON_BBOX_X = "x"
JSON_BBOX_Y = "y"
JSON_BBOX_WIDTH = "width"
JSON_BBOX_HEIGHT = "height"

# Mouse button masks
CTRL_MASK = 0x4
