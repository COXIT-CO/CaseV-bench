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
SAVE_SUCCESS = "Page data saved successfully!"

# Validation messages
SUBCLASS_REQUIRED = "Subclass Description is mandatory. Please enter a value."
X1_GREATER_THAN_X0 = "X1 must be greater than X0."
Y1_GREATER_THAN_Y0 = "Y1 must be greater than Y0."
VALID_NUMBERS_REQUIRED = (
    "Please enter valid numbers for dimensions and times referenced."
)
PAGE_NUMBER_REQUIRED = "Please enter a page number."

# Dialog titles
OBJECT_DETAILS_TITLE = "Enter Object Details"
EDIT_RECTANGLE_TITLE = "Edit Rectangle Details"
DELETE_RECTANGLE_TITLE = "Delete Rectangle"
SAVE_PAGE_TITLE = "Save Page Data"

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
SUBCLASS_LABEL = "Subclass Description:"
WIDTH_LABEL = "Width (inches):"
HEIGHT_LABEL = "Height (inches):"
DEPTH_LABEL = "Depth (inches):"
TIMES_REF_LABEL = "Times Referenced:"
ROOMS_LABEL = "Rooms (comma-separated):"
CABINET_ANNOTATIONS_LABEL = "Cabinet Annotations"
OBJECT_DETAILS_LABEL = "Object Details"
REQUIRED_FIELDS_LABEL = "* Required fields"
PAGE_NUMBER_LABEL = "Page Number:"

# JSON structure keys
JSON_PAGE_NUMBER = "page_number"
JSON_CABINETS = "cabinets"
JSON_IDENTIFYING_PROPERTIES = "identifying_properties"
JSON_SUBCLASS_DESCRIPTION = "subclass_description"
JSON_X0 = "x0"
JSON_Y0 = "y0"
JSON_X1 = "x1"
JSON_Y1 = "y1"
JSON_DIMENSIONS = "dimensions_in_inches"
JSON_WIDTH = "width"
JSON_HEIGHT = "height"
JSON_DEPTH = "depth"
JSON_ECALL_DATA = "ecall_data"
JSON_TIMES_REFERENCED = "times_referenced"
JSON_ROOMS = "rooms"

# Mouse button masks
CTRL_MASK = 0x4
