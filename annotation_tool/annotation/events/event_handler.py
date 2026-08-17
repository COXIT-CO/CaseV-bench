#!/usr/bin/env python3
"""
Event Handler for PDF Coordinate Viewer.

This module handles all user input events including mouse movements,
clicks, drags, and keyboard interactions for the PDF viewer application.
"""

import math


class EventHandler:
    """
    Manages all event handling for the PDF Coordinate Viewer application.
    """

    def __init__(self, canvas, pdf_handler, rectangle_manager,
                 dialog_manager, ui_components):
        """
        Initialize the event handler.

        Args:
            canvas: Canvas widget for event binding
            pdf_handler: PDF handler instance
            rectangle_manager: Rectangle manager instance
            dialog_manager: Dialog manager instance
            ui_components: UI components instance
        """
        self.canvas = canvas
        self.pdf_handler = pdf_handler
        self.rectangle_manager = rectangle_manager
        self.dialog_manager = dialog_manager
        self.ui_components = ui_components

        # Mouse state
        self.mouse_x = 0
        self.mouse_y = 0
        self.pdf_x = 0.0
        self.pdf_y = 0.0

        # Pan/drag state
        self.is_panning = False
        self.pan_start_x = 0
        self.pan_start_y = 0
        self.scroll_start_x = 0
        self.scroll_start_y = 0

        # Rectangle drawing state
        self.is_drawing_rectangle = False
        self.rect_start_x = 0
        self.rect_start_y = 0
        self.current_rectangle = None

        # Temporary line state for measurement tool
        self.temp_line = None
        self.temp_line_text = None
        # Track all temporary text elements for cleanup
        self.temp_text_elements = []
        self.line_start_x = 0
        self.line_start_y = 0

        # Callbacks for main application actions
        self.zoom_in_callback = None
        self.zoom_out_callback = None
        self.update_page_callback = None

    def set_callbacks(self, zoom_in_callback, zoom_out_callback,
                      update_page_callback):
        """
        Set callback functions for actions that need to be handled by the main
        application.

        Args:
            zoom_in_callback: Function to call for zooming in
            zoom_out_callback: Function to call for zooming out
            update_page_callback: Function to call for page updates
        """
        self.zoom_in_callback = zoom_in_callback
        self.zoom_out_callback = zoom_out_callback
        self.update_page_callback = update_page_callback

    def bind_events(self):
        """Bind all event handlers to the canvas."""
        # Bind canvas mouse events
        self.canvas.bind('<Motion>', self.on_mouse_motion)
        self.canvas.bind('<Leave>', self.on_mouse_leave)
        self.canvas.bind('<ButtonPress-1>', self.on_left_button_press)
        self.canvas.bind('<ButtonRelease-1>', self.on_left_button_release)
        self.canvas.bind('<B1-Motion>', self.on_left_button_drag)
        # Add double-click binding
        self.canvas.bind('<Double-Button-1>', self.on_left_double_click)
        self.canvas.bind('<ButtonPress-2>', self.on_middle_button_press)
        self.canvas.bind('<ButtonRelease-2>', self.on_middle_button_release)
        self.canvas.bind('<B2-Motion>', self.on_middle_button_drag)

        # Bind mouse wheel events for zooming
        self.canvas.bind('<MouseWheel>', self.on_mouse_wheel)
        self.canvas.bind('<Button-4>', self.on_mouse_wheel)  # Linux scroll up
        self.canvas.bind('<Button-5>', self.on_mouse_wheel)  # Linux scroll down

        # Bind right mouse button events for drawing temporary lines
        self.canvas.bind('<ButtonPress-3>', self.on_right_button_press)
        self.canvas.bind('<B3-Motion>', self.on_right_button_drag)
        self.canvas.bind('<ButtonRelease-3>', self.on_right_button_release)

    def on_mouse_motion(self, event):
        """Handle mouse motion over the canvas."""
        if not self.pdf_handler.is_pdf_loaded():
            return

        # Get canvas coordinates
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)

        # Store mouse coordinates
        self.mouse_x = int(canvas_x)
        self.mouse_y = int(canvas_y)

        # Convert to PDF coordinates using PDF handler
        self.pdf_x, self.pdf_y = (
            self.pdf_handler.convert_canvas_to_pdf_coordinates(
                canvas_x, canvas_y
            )
        )

        # Update coordinates display
        self.ui_components.update_coordinates_display(
            self.mouse_x, self.mouse_y, self.pdf_x, self.pdf_y
        )

    def on_mouse_leave(self, _):
        """Handle mouse leaving the canvas."""
        # Reset coordinates when mouse leaves canvas
        self.mouse_x = 0
        self.mouse_y = 0
        self.pdf_x = 0.0
        self.pdf_y = 0.0
        self.ui_components.update_coordinates_display(
            self.mouse_x, self.mouse_y, self.pdf_x, self.pdf_y
        )

    def on_left_button_press(self, event):
        """
        Handle left mouse button press for rectangle drawing.

        Args:
            event: Mouse event
        """
        if not self.pdf_handler.is_pdf_loaded():
            return

        # Check if clicking on existing rectangle first
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)

        # Use rectangle manager to handle rectangle clicks
        if self.rectangle_manager.handle_rectangle_click(canvas_x, canvas_y):
            return

        # Start drawing new rectangle
        self.is_drawing_rectangle = True
        self.rect_start_x = canvas_x
        self.rect_start_y = canvas_y

        # Create new rectangle item (yellow during drawing)
        self.current_rectangle = self.canvas.create_rectangle(
            self.rect_start_x, self.rect_start_y,
            self.rect_start_x, self.rect_start_y,
            outline='yellow', fill='yellow', stipple='gray25', width=2
        )

    def on_left_button_release(self, event):
        """
        Handle left mouse button release.

        Args:
            event: Mouse event
        """
        if not self.is_drawing_rectangle or not self.current_rectangle:
            return

        # Finish drawing rectangle
        self.is_drawing_rectangle = False

        # Get rectangle coordinates
        end_x = self.canvas.canvasx(event.x)
        end_y = self.canvas.canvasy(event.y)

        # Calculate final rectangle coordinates
        x0 = min(self.rect_start_x, end_x)
        y0 = min(self.rect_start_y, end_y)
        x1 = max(self.rect_start_x, end_x)
        y1 = max(self.rect_start_y, end_y)

        # Check minimum area to prevent accidental rectangle creation
        min_rectangle_area = 400  # pixels (20x20 minimum)
        rect_width = x1 - x0
        rect_height = y1 - y0
        rect_area = rect_width * rect_height

        if rect_area < min_rectangle_area:
            # Rectangle too small - remove it without showing dialog
            self.canvas.delete(self.current_rectangle)
            self.current_rectangle = None
            self.is_drawing_rectangle = False
            return

        # Convert to PDF coordinates
        zoom_factor = self.pdf_handler.get_zoom_factor()
        pdf_x0 = x0 / zoom_factor if zoom_factor > 0 else 0
        pdf_y0 = y0 / zoom_factor if zoom_factor > 0 else 0
        pdf_x1 = x1 / zoom_factor if zoom_factor > 0 else 0
        pdf_y1 = y1 / zoom_factor if zoom_factor > 0 else 0

        # Show form dialog to enter object details
        object_data = self.dialog_manager.show_object_details_dialog(
            pdf_x0, pdf_y0, pdf_x1, pdf_y1
        )

        if object_data is not None:
            # User clicked OK - finalize rectangle
            self.canvas.itemconfig(
                self.current_rectangle,
                outline='red', fill='lightblue', width=2
            )

            # Store rectangle data with object details
            rect_data = {
                'canvas_id': self.current_rectangle,
                'page_number': self.pdf_handler.get_current_page_number(),
                'coordinates': {
                    'x0': int(pdf_x0),
                    'y0': int(pdf_y0),
                    'x1': int(pdf_x1),
                    'y1': int(pdf_y1)
                },
                'object_data': object_data
            }

            # Add rectangle through rectangle manager
            self.rectangle_manager.add_rectangle(rect_data)

            print(f'Created object annotation: {rect_data}')

        else:
            # User clicked Cancel - remove rectangle
            self.canvas.delete(self.current_rectangle)

        self.current_rectangle = None

    def on_left_button_drag(self, event):
        """
        Handle left mouse button drag for rectangle drawing.

        Args:
            event: Mouse event
        """
        if not self.is_drawing_rectangle or not self.current_rectangle:
            return

        # Update rectangle size while dragging
        end_x = self.canvas.canvasx(event.x)
        end_y = self.canvas.canvasy(event.y)

        self.canvas.coords(
            self.current_rectangle,
            self.rect_start_x, self.rect_start_y,
            end_x, end_y
        )

    def on_left_double_click(self, event):
        """
        Handle left mouse button double-click to open edit dialog for
        rectangles.

        Args:
            event: Mouse event
        """
        if not self.pdf_handler.is_pdf_loaded():
            return

        # Get canvas coordinates
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)

        # Use rectangle manager to handle rectangle double-click for editing
        self.rectangle_manager.handle_rectangle_double_click(
            canvas_x, canvas_y
        )

    def on_middle_button_press(self, event):
        """
        Handle middle mouse button press for panning.

        Args:
            event: Mouse event
        """
        self.is_panning = True
        self.pan_start_x = event.x
        self.pan_start_y = event.y

        # Get current scroll position
        self.scroll_start_x = self.canvas.xview()[0]
        self.scroll_start_y = self.canvas.yview()[0]

    def on_middle_button_release(self, _):
        """
        Handle middle mouse button release.
        """
        self.is_panning = False

    def on_middle_button_drag(self, event):
        """
        Handle middle mouse button drag for panning.

        Args:
            event: Mouse event
        """
        if not self.is_panning:
            return

        # Calculate delta movement
        delta_x = event.x - self.pan_start_x
        delta_y = event.y - self.pan_start_y

        # Get canvas size and scroll region
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        scroll_region = self.canvas.cget('scrollregion')

        if scroll_region:
            # Parse scroll region (x1, y1, x2, y2)
            scroll_coords = [float(x) for x in scroll_region.split()]
            scroll_width = scroll_coords[2] - scroll_coords[0]
            scroll_height = scroll_coords[3] - scroll_coords[1]

            # Calculate scroll fractions
            if scroll_width > canvas_width:
                scroll_fraction_x = delta_x / scroll_width
                new_scroll_x = max(
                    0.0, min(1.0, self.scroll_start_x - scroll_fraction_x)
                )
                self.canvas.xview_moveto(new_scroll_x)

            if scroll_height > canvas_height:
                scroll_fraction_y = delta_y / scroll_height
                new_scroll_y = max(
                    0.0, min(1.0, self.scroll_start_y - scroll_fraction_y)
                )
                self.canvas.yview_moveto(new_scroll_y)

    def on_mouse_wheel(self, event):
        """
        Handle mouse wheel events for zooming (only when Ctrl is pressed).

        Args:
            event: Mouse wheel event
        """
        if not self.pdf_handler.is_pdf_loaded():
            return

        # Only zoom if Ctrl key is pressed
        if not event.state & 0x4:  # 0x4 is the mask for Ctrl key
            return

        # Handle different mouse wheel event types
        if event.num == 4 or event.delta > 0:
            # Scroll up - zoom in
            if self.zoom_in_callback:
                self.zoom_in_callback()
        elif event.num == 5 or event.delta < 0:
            # Scroll down - zoom out
            if self.zoom_out_callback:
                self.zoom_out_callback()

    def on_right_button_press(self, event):
        """
        Handle right mouse button press to start drawing a temporary line.

        Args:
            event: Mouse event
        """
        if not self.pdf_handler.is_pdf_loaded():
            return

        # Delete previous temporary line and all temporary text elements
        if self.temp_line is not None:
            self.canvas.delete(self.temp_line)
            self.temp_line = None
        if self.temp_line_text is not None:
            self.canvas.delete(self.temp_line_text)
            self.temp_line_text = None

        # Delete all previous temporary text elements
        for text_element in self.temp_text_elements:
            self.canvas.delete(text_element)
        self.temp_text_elements.clear()

        # Start drawing the line
        self.line_start_x = self.canvas.canvasx(event.x)
        self.line_start_y = self.canvas.canvasy(event.y)

        # Create a temporary line (orange and thick)
        self.temp_line = self.canvas.create_line(
            self.line_start_x, self.line_start_y,
            self.line_start_x, self.line_start_y,
            fill='orange', width=3
        )

        # Create a temporary text for line length (orange and bigger)
        self.temp_line_text = self.canvas.create_text(
            self.line_start_x, self.line_start_y - 10,
            text='', fill='orange', font=('Arial', 30, 'bold')
        )

    def on_right_button_drag(self, event):
        """
        Handle right mouse button drag to update the temporary line.

        Args:
            event: Mouse event
        """
        if not self.temp_line:
            return

        # Update the line's end coordinates
        line_end_x = self.canvas.canvasx(event.x)
        line_end_y = self.canvas.canvasy(event.y)

        self.canvas.coords(
            self.temp_line,
            self.line_start_x, self.line_start_y,
            line_end_x, line_end_y
        )

        # Calculate the line length in inches (PDF DPI = 72)
        # Account for zoom factor - convert canvas pixels to PDF coordinates
        length_pixels = math.sqrt(
            (line_end_x - self.line_start_x) ** 2 +
            (line_end_y - self.line_start_y) ** 2
        )
        zoom_factor = self.pdf_handler.get_zoom_factor()
        length_pdf_points = (
            length_pixels / zoom_factor if zoom_factor > 0 else 0
        )
        length_inches = length_pdf_points / 72

        # Position text in the middle of the line
        if self.temp_line_text is not None:
            # Calculate middle point of the line
            mid_x = (self.line_start_x + line_end_x) / 2
            mid_y = (self.line_start_y + line_end_y) / 2

            # Position text slightly above the middle point
            self.canvas.coords(self.temp_line_text, mid_x, mid_y - 20)
            self.canvas.itemconfig(
                self.temp_line_text, text=f'{length_inches:.2f} in'
            )

    def on_right_button_release(self, _):
        """
        Handle right mouse button release to finalize the temporary line.
        """
        if not self.temp_line:
            return

        # Add the current text element to the tracking list before clearing
        if self.temp_line_text is not None:
            self.temp_text_elements.append(self.temp_line_text)
            self.temp_line_text = None

    def get_mouse_coordinates(self):
        """
        Get current mouse coordinates.

        Returns:
            tuple: (mouse_x, mouse_y, pdf_x, pdf_y)
        """
        return self.mouse_x, self.mouse_y, self.pdf_x, self.pdf_y

    def is_currently_drawing_rectangle(self):
        """
        Check if currently drawing a rectangle.

        Returns:
            bool: True if drawing, False otherwise
        """
        return self.is_drawing_rectangle
