#!/usr/bin/env python3
"""
UI components module for the PDF Coordinate Viewer application.
Handles all user interface creation and layout.
"""

import tkinter as tk
from tkinter import ttk
from config.constants import (
    APP_TITLE,
    APP_GEOMETRY,
    NO_PDF_LOADED,
    OPEN_PDF_BUTTON,
    PREVIOUS_BUTTON,
    NEXT_BUTTON,
    ZOOM_IN_BUTTON,
    ZOOM_OUT_BUTTON,
    CANVAS_BG,
    CABINET_ANNOTATIONS_LABEL,
    TITLE_FONT,
    COORDINATES_LABEL,
    COORDINATES_FONT,
    DETAILS_PANEL_DEFAULT,
    EMPTY_RECTANGLES_MESSAGE,
    SMALL_FONT,
    TOOLBAR_PADDING,
    CONTENT_PADDING,
    STATUS_PADDING,
    LEFT_PANEL_WEIGHT,
    MIDDLE_PANEL_WEIGHT,
    PDF_PANEL_WEIGHT,
    DETAILS_PANEL_WEIGHT
)


class UIComponents:
    """Handles UI component creation and layout."""

    def __init__(self, root):
        """
        Initialize the UI components manager.

        Args:
            root (tk.Tk): The root tkinter window
        """
        self.root = root
        self.setup_main_window()

        # UI element references
        self.canvas = None
        self.page_dropdown = None
        self.page_dropdown_var = None
        self.page_info_var = None
        self.zoom_var = None
        self.coordinates_var = None
        self.file_info_var = None
        self.details_frame = None
        self.rectangles_frame = None
        self.rectangles_list_frame = None
        self.rectangles_canvas = None
        self.rectangles_scrollbar = None

    def setup_main_window(self):
        """Configure the main application window."""
        self.root.title(APP_TITLE)
        self.root.geometry(APP_GEOMETRY)

    def create_main_layout(self):
        """
        Create the main application layout.

        Returns:
            dict: Dictionary containing main UI elements
        """
        # Create main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Create toolbar
        toolbar_frame = self.create_toolbar(main_frame)

        # Create content area
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=CONTENT_PADDING)

        # Create PDF display area with panels
        canvas_elements = self.create_pdf_display_area(content_frame)

        # Create status bar
        status_elements = self.create_status_bar(main_frame)

        return {
            'main_frame': main_frame,
            'toolbar_frame': toolbar_frame,
            'content_frame': content_frame,
            'status_elements': status_elements,
            **canvas_elements
        }

    def create_toolbar(self, parent):
        """
        Create the toolbar with buttons and controls.

        Args:
            parent: Parent widget

        Returns:
            ttk.Frame: The toolbar frame
        """
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=TOOLBAR_PADDING)

        # Open PDF button
        open_button = ttk.Button(toolbar, text=OPEN_PDF_BUTTON)
        open_button.pack(side=tk.LEFT, padx=(0, 5))

        # Separator
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=5
        )

        # Navigation controls
        prev_button = ttk.Button(toolbar, text=PREVIOUS_BUTTON)
        prev_button.pack(side=tk.LEFT, padx=(0, 2))

        # Page dropdown
        self.page_dropdown_var = tk.StringVar()
        self.page_dropdown = ttk.Combobox(
            toolbar,
            textvariable=self.page_dropdown_var,
            width=8,
            state='readonly'
        )
        self.page_dropdown.pack(side=tk.LEFT, padx=2)

        # Page info
        self.page_info_var = tk.StringVar(value=NO_PDF_LOADED)
        page_info_label = ttk.Label(toolbar, textvariable=self.page_info_var)
        page_info_label.pack(side=tk.LEFT, padx=5)

        next_button = ttk.Button(toolbar, text=NEXT_BUTTON)
        next_button.pack(side=tk.LEFT, padx=(2, 5))

        # Separator
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=5
        )

        # Zoom controls
        zoom_in_button = ttk.Button(toolbar, text=ZOOM_IN_BUTTON)
        zoom_in_button.pack(side=tk.LEFT, padx=(0, 2))

        self.zoom_var = tk.StringVar(value='100%')
        zoom_label = ttk.Label(toolbar, textvariable=self.zoom_var)
        zoom_label.pack(side=tk.LEFT, padx=5)

        zoom_out_button = ttk.Button(toolbar, text=ZOOM_OUT_BUTTON)
        zoom_out_button.pack(side=tk.LEFT, padx=(2, 0))

        return {
            'toolbar': toolbar,
            'open_button': open_button,
            'prev_button': prev_button,
            'next_button': next_button,
            'zoom_in_button': zoom_in_button,
            'zoom_out_button': zoom_out_button,
            'page_dropdown': self.page_dropdown,
            'page_dropdown_var': self.page_dropdown_var,
            'page_info_var': self.page_info_var,
            'zoom_var': self.zoom_var
        }

    def create_pdf_display_area(self, parent):
        """
        Create the PDF display area with panels.

        Args:
            parent: Parent widget

        Returns:
            dict: Dictionary containing canvas and panel elements
        """
        # Create horizontal paned window for left panel, PDF, and details
        main_paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # Create left panel for rectangle list
        rectangles_elements = self.create_rectangles_panel(main_paned)

        # Create middle paned window for PDF and details
        pdf_details_paned = ttk.PanedWindow(main_paned, orient=tk.HORIZONTAL)
        main_paned.add(pdf_details_paned, weight=MIDDLE_PANEL_WEIGHT)

        # Create canvas area
        canvas_elements = self.create_canvas_area(pdf_details_paned)

        # Create details panel
        details_elements = self.create_details_panel(pdf_details_paned)

        return {
            'main_paned': main_paned,
            'pdf_details_paned': pdf_details_paned,
            'canvas': self.canvas,
            **rectangles_elements,
            **canvas_elements,
            **details_elements
        }

    def create_canvas_area(self, parent):
        """
        Create the canvas area with scrollbars.

        Args:
            parent: Parent paned window

        Returns:
            dict: Canvas-related elements
        """
        # Create frame for canvas and scrollbars
        canvas_frame = ttk.Frame(parent)
        parent.add(canvas_frame, weight=PDF_PANEL_WEIGHT)

        # Create canvas
        self.canvas = tk.Canvas(
            canvas_frame,
            bg=CANVAS_BG,
            highlightthickness=0
        )

        # Create scrollbars
        v_scrollbar = ttk.Scrollbar(
            canvas_frame,
            orient=tk.VERTICAL,
            command=self.canvas.yview
        )
        h_scrollbar = ttk.Scrollbar(
            canvas_frame,
            orient=tk.HORIZONTAL,
            command=self.canvas.xview
        )

        # Configure canvas scrollbars
        self.canvas.configure(
            yscrollcommand=v_scrollbar.set,
            xscrollcommand=h_scrollbar.set
        )

        # Pack scrollbars and canvas
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Make canvas focusable for mouse wheel events
        self.canvas.focus_set()

        return {
            'canvas_frame': canvas_frame,
            'v_scrollbar': v_scrollbar,
            'h_scrollbar': h_scrollbar
        }

    def create_rectangles_panel(self, parent):
        """
        Create the left panel for displaying rectangles list.

        Args:
            parent: Parent paned window

        Returns:
            dict: Rectangle panel elements
        """
        # Create frame for rectangle list
        self.rectangles_frame = ttk.Frame(parent)
        parent.add(self.rectangles_frame, weight=LEFT_PANEL_WEIGHT)

        # Title label
        title_label = ttk.Label(
            self.rectangles_frame,
            text=CABINET_ANNOTATIONS_LABEL,
            font=TITLE_FONT
        )
        title_label.pack(anchor='w', padx=10, pady=(10, 5))

        # Canvas for rectangle list (scrollable)
        self.rectangles_canvas = tk.Canvas(self.rectangles_frame)
        self.rectangles_scrollbar = ttk.Scrollbar(
            self.rectangles_frame,
            orient='vertical',
            command=self.rectangles_canvas.yview
        )

        self.rectangles_canvas.configure(
            yscrollcommand=self.rectangles_scrollbar.set
        )

        # Pack canvas and scrollbar
        self.rectangles_canvas.pack(side='left', fill='both', expand=True)
        self.rectangles_scrollbar.pack(side='right', fill='y')

        # Create a frame inside the canvas for the rectangle list
        self.rectangles_list_frame = ttk.Frame(self.rectangles_canvas)
        self.rectangles_canvas.create_window(
            (0, 0),
            window=self.rectangles_list_frame,
            anchor='nw'
        )

        # Bind configure event to update scroll region
        self.rectangles_list_frame.bind(
            '<Configure>',
            lambda e: self.rectangles_canvas.configure(  # type: ignore
                scrollregion=self.rectangles_canvas.bbox('all')  # type: ignore
            )
        )

        return {
            'rectangles_frame': self.rectangles_frame,
            'rectangles_canvas': self.rectangles_canvas,
            'rectangles_scrollbar': self.rectangles_scrollbar,
            'rectangles_list_frame': self.rectangles_list_frame,
            'title_label': title_label
        }

    def create_details_panel(self, parent):
        """
        Create the details panel for displaying information.

        Args:
            parent: Parent paned window

        Returns:
            dict: Details panel elements
        """
        # Create details frame and add to paned window
        self.details_frame = ttk.Frame(parent)
        parent.add(self.details_frame, weight=DETAILS_PANEL_WEIGHT)

        # Add initial content
        initial_label = ttk.Label(
            self.details_frame,
            text=DETAILS_PANEL_DEFAULT
        )
        initial_label.pack(padx=10, pady=10)

        return {
            'details_frame': self.details_frame,
            'initial_label': initial_label
        }

    def create_status_bar(self, parent):
        """
        Create the status bar with coordinate and file information.

        Args:
            parent: Parent widget

        Returns:
            dict: Status bar elements
        """
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill=tk.X, pady=STATUS_PADDING)

        # Coordinates display
        coords_label = ttk.Label(status_frame, text=COORDINATES_LABEL)
        coords_label.pack(side=tk.LEFT)

        self.coordinates_var = tk.StringVar(
            value='Mouse: (0, 0) | PDF: (0.0, 0.0)'
        )
        coords_value_label = ttk.Label(
            status_frame,
            textvariable=self.coordinates_var,
            font=COORDINATES_FONT
        )
        coords_value_label.pack(side=tk.LEFT, padx=(10, 0))

        # File info
        self.file_info_var = tk.StringVar(value='')
        file_info_label = ttk.Label(
            status_frame,
            textvariable=self.file_info_var
        )
        file_info_label.pack(side=tk.RIGHT)

        return {
            'status_frame': status_frame,
            'coords_label': coords_label,
            'coords_value_label': coords_value_label,
            'file_info_label': file_info_label,
            'coordinates_var': self.coordinates_var,
            'file_info_var': self.file_info_var
        }

    def get_canvas(self):
        """Get the main canvas widget."""
        return self.canvas

    def get_details_frame(self):
        """Get the details panel frame."""
        return self.details_frame

    def get_rectangles_list_frame(self):
        """Get the rectangles list frame."""
        return self.rectangles_list_frame

    def update_coordinates_display(self, mouse_x, mouse_y, pdf_x, pdf_y):
        """
        Update the coordinates display in the status bar.

        Args:
            mouse_x, mouse_y (int): Mouse coordinates
            pdf_x, pdf_y (float): PDF coordinates
        """
        coords_text = (
            f'Mouse: ({mouse_x}, {mouse_y}) | '
            f'PDF: ({pdf_x:.2f}, {pdf_y:.2f})'
        )
        self.coordinates_var.set(coords_text)  # type: ignore

    def update_file_info(self, file_info):
        """
        Update file information display.

        Args:
            file_info (dict): File information dictionary
        """
        info_text = (
            f"File: {file_info['filename']} | "
            f"Pages: {file_info['page_count']}"
        )
        self.file_info_var.set(info_text)  # type: ignore

    def update_page_info(self, page_info_text):
        """
        Update page information display.

        Args:
            page_info_text (str): Page information text
        """
        self.page_info_var.set(page_info_text)  # type: ignore

    def update_zoom_info(self, zoom_info_text):
        """
        Update zoom information display.

        Args:
            zoom_info_text (str): Zoom information text
        """
        self.zoom_var.set(zoom_info_text)  # type: ignore

    def update_page_dropdown(self, page_options, current_page_text):
        """
        Update the page dropdown with available pages.

        Args:
            page_options (list): List of page option strings
            current_page_text (str): Current page selection text
        """
        self.page_dropdown['values'] = page_options  # type: ignore
        self.page_dropdown_var.set(current_page_text)  # type: ignore

    def clear_rectangles_list(self):
        """Clear all widgets from the rectangles list frame."""
        for widget in self.rectangles_list_frame.winfo_children():# type: ignore
            widget.destroy()

    def clear_details_panel(self):
        """Clear all widgets from the details panel."""
        for widget in self.details_frame.winfo_children():  # type: ignore
            widget.destroy()

    def show_empty_rectangles_message(self):
        """Show message when no rectangles exist."""
        ttk.Label(
            self.rectangles_list_frame,
            text=EMPTY_RECTANGLES_MESSAGE,
            font=SMALL_FONT,
            foreground='gray'
        ).pack(padx=10, pady=20)

    def restore_default_details_panel(self):
        """Restore the default details panel content."""
        self.clear_details_panel()
        ttk.Label(
            self.details_frame,
            text=DETAILS_PANEL_DEFAULT
        ).pack(padx=10, pady=10)
