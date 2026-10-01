#!/usr/bin/env python3
"""
Rectangle Manager for PDF Coordinate Viewer.

This module handles all rectangle-related operations including selection,
details display, list management, navigation, and canvas updates.
"""

import json
import gc
import sys
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config.constants import (
    COORDINATES_FONT,
    COORDINATES_LABEL,
    RECTANGLE_EDITING_COLOR,
    RECTANGLE_EDITING_FILL,
    SECTION_FONT,
    SMALL_FONT,
    CATEGORY_CABINET
)


class RectangleManager:
    """
    Manages all rectangle operations for the PDF Coordinate Viewer application.
    """

    def __init__(self, canvas, details_frame, rectangles_list_frame,
                 pdf_handler, dialog_manager):
        """
        Initialize the rectangle manager.

        Args:
            canvas: Canvas widget for drawing rectangles
            details_frame: Frame for displaying rectangle details
            rectangles_list_frame: Frame for displaying rectangles list
            pdf_handler: PDF handler instance
            dialog_manager: Dialog manager instance
        """
        self.canvas = canvas
        self.details_frame = details_frame
        self.rectangles_list_frame = rectangles_list_frame
        self.pdf_handler = pdf_handler
        self.dialog_manager = dialog_manager
        self.is_macos = sys.platform == 'darwin'

        # Rectangle state
        self.rectangles = []  # List to store all drawn rectangles with data
        self.selected_rectangle = None  # Currently selected rectangle
        self.delete_icon = None  # X-mark icon for deletion

    def handle_macos_gui_operation(self,
                                   operation, operation_name='GUI operation'):
        """
        Safely handle GUI operations on macOS.

        Args:
            operation: Function to execute
            operation_name: Name of the operation for error reporting

        Returns:
            Result of the operation or None if failed
        """
        if self.is_macos is True:
            try:
                return operation()
            except Exception as e:
                print(f'macOS {operation_name} failed: {e}')
                traceback.print_exc()
                return None
        else:
            return operation()

    def add_rectangle(self, rect_data):
        """
        Add a new rectangle to the manager.

        Args:
            rect_data (dict): Rectangle data to add
        """
        self.rectangles.append(rect_data)
        self.update_rectangles_list()

    def remove_rectangle(self, rect_data):
        """
        Remove a rectangle from the manager.

        Args:
            rect_data (dict): Rectangle data to remove
        """
        if rect_data in self.rectangles:
            self.rectangles.remove(rect_data)

        # Remove from canvas
        if rect_data.get('canvas_id'):
            self.canvas.delete(rect_data['canvas_id'])

        # Clear selection if this was the selected rectangle
        if self.selected_rectangle == rect_data:
            self.clear_selection()

        # Update rectangles list display
        self.update_rectangles_list()

    def clear_selection(self):
        """Clear the current rectangle selection and remove delete icon."""
        if self.delete_icon is not None:
            self.canvas.delete(self.delete_icon)
            self.delete_icon = None
        self.selected_rectangle = None

    def show_rectangle_details(self, rect_data):
        """
        Show details of a selected rectangle and provide edit/delete options.

        Args:
            rect_data (dict): Rectangle data to display
        """
        # Clear previous selection highlighting
        self.clear_selection()

        # Highlight selected rectangle
        self.selected_rectangle = rect_data
        if rect_data.get('canvas_id'):
            self.canvas.itemconfig(
                rect_data['canvas_id'],
                outline=RECTANGLE_EDITING_COLOR,
                fill=RECTANGLE_EDITING_FILL
            )

            # Add delete icon
            self._add_delete_icon(rect_data)

        # Update details panel
        self._update_details_panel(rect_data)

    def redraw_rectangles_for_current_page(self):
        """
        Redraw all rectangles for the current page after page changes or zoom.
        """
        if not self.pdf_handler.is_pdf_loaded():
            return

        # macOS-specific safe redraw operation
        def _perform_redraw():
            # Remove existing rectangle canvas items
            for rect_data in self.rectangles:
                if rect_data.get('canvas_id'):
                    self.canvas.delete(rect_data['canvas_id'])
                    rect_data['canvas_id'] = None

            # Redraw rectangles for current page
            current_page = self.pdf_handler.get_current_page_number()
            rectangles_drawn = 0

            for rect_data in self.rectangles:
                if rect_data['page_number'] == current_page:
                    try:
                        coords = rect_data['coordinates']

                        # Convert PDF coordinates to canvas coordinates
                        zoom_factor = self.pdf_handler.get_zoom_factor()
                        canvas_x0 = coords['x0'] * zoom_factor
                        canvas_y0 = coords['y0'] * zoom_factor
                        canvas_x1 = coords['x1'] * zoom_factor
                        canvas_y1 = coords['y1'] * zoom_factor

                        # Create rectangle on canvas
                        rect_id = self.canvas.create_rectangle(
                            canvas_x0, canvas_y0, canvas_x1, canvas_y1,
                            outline='red', fill='lightblue', stipple='gray25',
                            width=2
                        )
                        rect_data['canvas_id'] = rect_id
                        rectangles_drawn += 1

                        # On macOS, add small delays between operations to
                        # prevent memory pressure
                        if self.is_macos is True and rectangles_drawn % 10 == 0:
                            time.sleep(0.01)  # 10ms delay every 10 rectangles

                    except Exception as e:
                        print(f'Error drawing rectangle: {e}')
                        continue

            return rectangles_drawn

        # Execute with macOS safety if needed
        if self.is_macos is True:
            try:
                result = _perform_redraw()
                # Force cleanup after redraw on macOS
                gc.collect()
                return result
            except Exception as e:
                print(f'macOS rectangle redraw failed: {e}')
                traceback.print_exc()
        else:
            return _perform_redraw()

    def update_rectangles_list(self):
        """
        Update the rectangles list panel with current rectangles
        organized by pages.
        """
        # Clear existing content
        for widget in self.rectangles_list_frame.winfo_children():
            widget.destroy()

        if not self.rectangles:
            # Check if PDF is loaded to show import option
            if self.pdf_handler.is_pdf_loaded():
                self.show_import_button()
            else:
                # Show empty state when no PDF is loaded
                empty_label = ttk.Label(
                    self.rectangles_list_frame,
                    text='Open a PDF file to get started',
                    font=SMALL_FONT,
                    foreground='gray'
                )
                empty_label.pack(padx=10, pady=20)
            return

        # Group rectangles by page
        rectangles_by_page = {}
        for rect_data in self.rectangles:
            page_num = rect_data['page_number']
            if page_num not in rectangles_by_page:
                rectangles_by_page[page_num] = []
            rectangles_by_page[page_num].append(rect_data)

        # Sort pages and create sections
        for page_num in sorted(rectangles_by_page.keys()):
            self._create_page_section(page_num, rectangles_by_page[page_num])

    def navigate_to_rectangle(self, rect_data):
        """
        Navigate to the page containing the rectangle and center it on screen.

        Args:
            rect_data (dict): Rectangle data containing page and coordinates
        """
        target_page = rect_data['page_number']

        # Navigate to the target page if not already there
        if self.pdf_handler.get_current_page_number() != target_page:
            self.pdf_handler.set_current_page(target_page)
            # Trigger page update through callback
            if hasattr(self, 'page_update_callback'):
                self.page_update_callback()

        # Center the rectangle on screen
        self._center_rectangle_on_screen(rect_data)

        # Select the rectangle and show its details
        self.show_rectangle_details(rect_data)

    def apply_rectangle_edit(self, original_rect_data, updated_data):
        """
        Apply the edited rectangle data and update the UI.

        Args:
            original_rect_data (dict): Original rectangle data
            updated_data (dict): Updated rectangle data from edit dialog
        """
        # Update the rectangle data
        original_rect_data['coordinates'] = updated_data['coordinates']
        original_rect_data['object_data'] = updated_data['object_data']

        # If this rectangle is currently on the displayed page, update canvas
        current_page = self.pdf_handler.get_current_page_number()
        if original_rect_data['page_number'] == current_page:
            # Update the rectangle on canvas if it exists
            if original_rect_data.get('canvas_id'):
                coords = original_rect_data['coordinates']
                zoom_factor = self.pdf_handler.get_zoom_factor()
                canvas_x0 = coords['x0'] * zoom_factor
                canvas_y0 = coords['y0'] * zoom_factor
                canvas_x1 = coords['x1'] * zoom_factor
                canvas_y1 = coords['y1'] * zoom_factor

                self.canvas.coords(
                    original_rect_data['canvas_id'],
                    canvas_x0, canvas_y0, canvas_x1, canvas_y1
                )

        # Update the rectangles list in the left panel
        self.update_rectangles_list()

        # If this rectangle is currently selected, update the details panel
        if self.selected_rectangle == original_rect_data:
            self.show_rectangle_details(original_rect_data)

    def get_rectangles_for_current_page(self):
        """
        Get all rectangles for the current page.

        Returns:
            list: List of rectangle data for current page
        """
        current_page = self.pdf_handler.get_current_page_number()
        return [
            rect for rect in self.rectangles
            if rect['page_number'] == current_page
        ]

    def get_all_rectangles(self):
        """
        Get all rectangles.

        Returns:
            list: List of all rectangle data
        """
        return self.rectangles

    def set_page_update_callback(self, callback):
        """
        Set the callback function for page updates.

        Args:
            callback: Function to call when page update is needed
        """
        self.page_update_callback = callback

    def handle_rectangle_click(self, canvas_x, canvas_y):
        """
        Handle click on a rectangle to select it.

        Args:
            canvas_x (float): Canvas X coordinate
            canvas_y (float): Canvas Y coordinate

        Returns:
            bool: True if a rectangle was clicked, False otherwise
        """
        # Find if clicked inside existing rectangle
        current_page = self.pdf_handler.get_current_page_number()
        for rect_data in self.rectangles:
            if (rect_data['page_number'] == current_page and
                    rect_data.get('canvas_id')):
                coords = rect_data['coordinates']

                # Convert PDF coordinates to canvas coordinates
                zoom_factor = self.pdf_handler.get_zoom_factor()
                # Note: canvas_x0 and canvas_y1 are used for bounds checking
                canvas_x1 = coords['x1'] * zoom_factor

                # Check if click is inside rectangle bounds
                if (coords['x0'] * zoom_factor <= canvas_x <= canvas_x1 and
                        coords['y0'] * zoom_factor <= canvas_y <=
                        coords['y1'] * zoom_factor):
                    self.show_rectangle_details(rect_data)
                    return True
        return False

    def handle_rectangle_double_click(self, canvas_x, canvas_y):
        """
        Handle double-click on a rectangle to open edit dialog.

        Args:
            canvas_x (float): Canvas X coordinate
            canvas_y (float): Canvas Y coordinate

        Returns:
            bool: True if a rectangle was double-clicked, False otherwise
        """
        # Find if double-clicked inside existing rectangle
        current_page = self.pdf_handler.get_current_page_number()
        for rect_data in self.rectangles:
            if (rect_data['page_number'] == current_page and
                    rect_data.get('canvas_id')):
                coords = rect_data['coordinates']

                # Convert PDF coordinates to canvas coordinates
                zoom_factor = self.pdf_handler.get_zoom_factor()
                canvas_x0 = coords['x0'] * zoom_factor
                canvas_y0 = coords['y0'] * zoom_factor
                canvas_x1 = coords['x1'] * zoom_factor
                canvas_y1 = coords['y1'] * zoom_factor

                # Check if double-click is inside rectangle bounds
                if (canvas_x0 <= canvas_x <= canvas_x1 and
                        canvas_y0 <= canvas_y <= canvas_y1):
                    # Open edit dialog for the rectangle
                    self.dialog_manager.show_edit_rectangle_dialog(
                        rect_data, self.canvas, self.apply_rectangle_edit
                    )
                    return True
        return False

    def handle_delete_icon_click(self):
        """Handle click on the delete icon."""
        if self.selected_rectangle is None:
            return

        # Show delete confirmation dialog
        self.dialog_manager.show_delete_confirmation_dialog(
            self.selected_rectangle, self._delete_rectangle_callback
        )

    def _delete_rectangle_callback(self, rect_data):
        """Callback function to handle rectangle deletion."""
        self.remove_rectangle(rect_data)
        print(f'Deleted rectangle: {rect_data}')

    def _add_delete_icon(self, rect_data):
        """Add a delete icon to the selected rectangle."""
        coords = rect_data['coordinates']

        # Convert PDF coordinates to canvas coordinates
        zoom_factor = self.pdf_handler.get_zoom_factor()
        canvas_y0 = coords['y0'] * zoom_factor
        canvas_x1 = coords['x1'] * zoom_factor

        # Create delete icon (X-mark) in the top-right corner of rectangle
        icon_size = 16
        icon_x = canvas_x1 - icon_size // 2
        icon_y = canvas_y0 + icon_size // 2

        # Create a red circle background for the X
        self.delete_icon = self.canvas.create_oval(
            icon_x - icon_size // 2, icon_y - icon_size // 2,
            icon_x + icon_size // 2, icon_y + icon_size // 2,
            fill='red', outline='darkred', width=2
        )

        # Create the X mark
        x_offset = 4
        self.canvas.create_line(
            icon_x - x_offset, icon_y - x_offset,
            icon_x + x_offset, icon_y + x_offset,
            fill='white', width=2, tags=f'delete_x_{self.delete_icon}'
        )
        self.canvas.create_line(
            icon_x - x_offset, icon_y + x_offset,
            icon_x + x_offset, icon_y - x_offset,
            fill='white', width=2, tags=f'delete_x_{self.delete_icon}'
        )

        # Bind click event to the delete icon
        self.canvas.tag_bind(
            self.delete_icon, '<Button-1>',
            lambda e: self.handle_delete_icon_click()
        )
        self.canvas.tag_bind(
            f'delete_x_{self.delete_icon}', '<Button-1>',
            lambda e: self.handle_delete_icon_click()
        )

    def _update_details_panel(self, rect_data):
        """Update the details panel with rectangle information."""
        # Clear existing details
        for widget in self.details_frame.winfo_children():
            widget.destroy()

        # Title
        title_label = ttk.Label(
            self.details_frame,
            text='Object Details',
            font=SECTION_FONT
        )
        title_label.pack(anchor='w', padx=10, pady=(10, 5))

        # Create scrollable frame for details
        canvas = tk.Canvas(self.details_frame)
        scrollbar = ttk.Scrollbar(
            self.details_frame, orient='vertical', command=canvas.yview
        )
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(scrollregion=canvas.bbox('all'))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(
            side='left', fill='both', expand=True, padx=(10, 0), pady=5
        )
        scrollbar.pack(side='right', fill='y', pady=5)

        # Display coordinates
        coord_frame = ttk.LabelFrame(scrollable_frame, text=COORDINATES_LABEL)
        coord_frame.pack(fill='x', padx=5, pady=5)

        coords = rect_data['coordinates']
        coord_text = (
            f"x0: {coords['x0']}, y0: {coords['y0']}\n"
            f"x1: {coords['x1']}, y1: {coords['y1']}"
        )
        coord_label = ttk.Label(
            coord_frame, text=coord_text, font=COORDINATES_FONT
        )
        coord_label.pack(anchor='w', padx=5, pady=5)

        # Display object data
        obj_data = rect_data['object_data']

        details_frame = ttk.LabelFrame(
            scrollable_frame, text='Object Information'
        )
        details_frame.pack(fill='x', padx=5, pady=5)

        # Create labels for each field
        fields = [
            ('Category:', obj_data.get('category', CATEGORY_CABINET)),
        ]

        for label_text, value in fields:
            field_frame = ttk.Frame(details_frame)
            field_frame.pack(fill='x', padx=5, pady=2)

            label = ttk.Label(field_frame, text=label_text, width=18)
            label.pack(side='left')
            value_label = ttk.Label(field_frame, text=value, font=SMALL_FONT)
            value_label.pack(side='left', fill='x')

    def _create_page_section(self, page_num, page_rectangles):
        """
        Create a section for rectangles on a specific page.

        Args:
            page_num (int): Page number (0-based)
            page_rectangles (list): List of rectangle data for this page
        """
        # Create page header frame with save button
        header_frame = ttk.Frame(self.rectangles_list_frame)
        header_frame.pack(fill='x', padx=5, pady=(5, 0))

        # Save button (left side)
        save_button = ttk.Button(
            header_frame,
            text='Save',
            width=6,
            command=self._show_save_project_dialog
        )
        save_button.pack(side='left', padx=(0, 5))

        # Page label (right side of save button)
        page_label = ttk.Label(
            header_frame,
            text=f'Page {page_num + 1}',
            font=SECTION_FONT
        )
        page_label.pack(side='left')

        # Create page content frame
        page_frame = ttk.Frame(self.rectangles_list_frame)
        page_frame.pack(fill='x', padx=5, pady=(0, 5))

        # Add border to content frame
        content_frame = ttk.Frame(page_frame, relief='solid', borderwidth=1)
        content_frame.pack(fill='x', padx=5, pady=2)

        # Sort rectangles by x1 and y1 coordinates
        def sort_key(rect):
            x1 = rect['coordinates']['x1']
            y1 = rect['coordinates']['y1']
            return (x1, y1)

        sorted_rectangles = sorted(page_rectangles, key=sort_key)

        # Create rectangle entries
        for i, rect_data in enumerate(sorted_rectangles):
            self._create_rectangle_entry(content_frame, rect_data, i + 1)

    def _create_rectangle_entry(self, parent, rect_data, rect_number):
        """
        Create an entry for a single rectangle in the list.

        Args:
            parent: Parent widget
            rect_data (dict): Rectangle data
            rect_number (int): Sequential number within the page
        """
        # Create main frame for the rectangle entry
        entry_frame = ttk.Frame(parent)
        entry_frame.pack(fill='x', padx=2, pady=2)

        # Build display name from the category
        category = rect_data['object_data'].get('category', '').strip()
        display_text = category if category else f'Rectangle {rect_number}'

        # Create clickable button for the rectangle
        rect_button = ttk.Button(
            entry_frame,
            text=display_text,
            command=lambda rd=rect_data: self.navigate_to_rectangle(rd),
            width=20
        )
        rect_button.pack(side='left', fill='x', expand=True)

        # Edit button
        edit_button = ttk.Button(
            entry_frame,
            text='Edit',
            command=lambda rd=rect_data: (
                self.dialog_manager.show_edit_rectangle_dialog(
                    rd, self.canvas, self.apply_rectangle_edit
                )
            ),
            width=6
        )
        edit_button.pack(side='left', padx=(2, 0))

        # Add coordinates as tooltip-like text
        coords = rect_data['coordinates']
        coord_text = f"({coords['x0']}, {coords['y0']})"
        coord_label = ttk.Label(
            entry_frame,
            text=coord_text,
            font=('Courier', 8),
            foreground='gray'
        )
        coord_label.pack(side='right', padx=(5, 0))

    def _center_rectangle_on_screen(self, rect_data):
        """
        Center the specified rectangle on the screen.

        Args:
            rect_data (dict): Rectangle data containing coordinates
        """
        coords = rect_data['coordinates']

        # Calculate rectangle center in PDF coordinates
        center_x = (coords['x0'] + coords['x1']) / 2
        center_y = (coords['y0'] + coords['y1']) / 2

        # Convert to canvas coordinates
        zoom_factor = self.pdf_handler.get_zoom_factor()
        canvas_center_x = center_x * zoom_factor
        canvas_center_y = center_y * zoom_factor

        # Get canvas and scroll region dimensions
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        scroll_region = self.canvas.cget('scrollregion')

        if scroll_region:
            # Parse scroll region
            scroll_coords = [float(x) for x in scroll_region.split()]
            scroll_width = scroll_coords[2] - scroll_coords[0]
            scroll_height = scroll_coords[3] - scroll_coords[1]

            # Calculate scroll position to center the rectangle
            if scroll_width > canvas_width:
                target_scroll_x = (
                    (canvas_center_x - canvas_width / 2) / scroll_width
                )
                target_scroll_x = max(0.0, min(1.0, target_scroll_x))
                self.canvas.xview_moveto(target_scroll_x)

            if scroll_height > canvas_height:
                target_scroll_y = (
                    (canvas_center_y - canvas_height / 2) / scroll_height
                )
                target_scroll_y = max(0.0, min(1.0, target_scroll_y))
                self.canvas.yview_moveto(target_scroll_y)

    def _show_save_project_dialog(self):
        """Show dialog to save all annotated objects to a project JSON file."""
        # Use dialog manager to show save dialog with every rectangle,
        # across all pages, since the project file bundles them together
        self.dialog_manager.show_save_project_dialog(self.get_all_rectangles())

    def has_rectangles(self):
        """
        Check if there are any rectangles in the manager.

        Returns:
            bool: True if rectangles exist, False otherwise
        """
        return len(self.rectangles) > 0

    def show_import_button(self):
        """Show import button in rectangles list when no rectangles exist."""
        # Clear existing content
        for widget in self.rectangles_list_frame.winfo_children():
            widget.destroy()

        # Create import section
        import_frame = ttk.Frame(self.rectangles_list_frame)
        import_frame.pack(fill='x', padx=10, pady=20)

        # Title
        title_label = ttk.Label(
            import_frame,
            text='No Rectangles Found',
            font=SECTION_FONT
        )
        title_label.pack(pady=(0, 10))

        # Description
        desc_label = ttk.Label(
            import_frame,
            text=('You can import rectangle data\nfrom a previously saved JSON'
                  ' file.'),
            font=SMALL_FONT,
            foreground='gray',
            justify='center'
        )
        desc_label.pack(pady=(0, 15))

        # Import button
        import_button = ttk.Button(
            import_frame,
            text='Import from JSON',
            command=self._show_import_dialog,
            width=20
        )
        import_button.pack()

    def _show_import_dialog(self):
        """
        Show file dialog to select a project-level JSON file for import.
        """
        file_path = filedialog.askopenfilename(
            title='Select Project JSON File',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')]
        )

        if not file_path:
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                json_data = json.load(file)

            loaded_count, skipped_count = (
                self.load_rectangles_from_project_json(json_data)
            )

            if loaded_count > 0:
                self.redraw_rectangles_for_current_page()

                success_msg = f'Successfully imported {loaded_count} object(s).'
                if skipped_count:
                    success_msg += (
                        f'\n\nSkipped {skipped_count} object(s) referencing '
                        f'pages outside the PDF ({self.pdf_handler.get_page_count()} '
                        f'page(s)).'
                    )
                messagebox.showinfo('Import Successful', success_msg)
            else:
                messagebox.showwarning(
                    'Import Failed', 'No objects were imported.'
                )

        except json.JSONDecodeError as e:
            messagebox.showerror('Import Error', f'Invalid JSON: {str(e)}')
        except Exception as e:
            messagebox.showerror(
                'Import Error', f'Error reading file:\n{str(e)}'
            )

    def load_rectangles_from_project_json(self, json_data):
        """
        Load rectangles from project-level JSON data
        (project_id + objects[]) and add them to the manager.

        Args:
            json_data (dict): Project JSON data

        Returns:
            tuple: (loaded_count, skipped_count)
        """
        loaded_count = 0
        skipped_count = 0
        page_count = self.pdf_handler.get_page_count()

        for obj in json_data.get('objects', []):
            try:
                page_number = int(obj.get('page', 1)) - 1  # Convert to 0-based

                if page_number < 0 or page_number >= page_count:
                    skipped_count += 1
                    continue

                bbox = obj.get('bbox', {})
                x0 = bbox.get('x', 0)
                y0 = bbox.get('y', 0)

                rect_data = {
                    'canvas_id': None,  # Will be set when drawing
                    'page_number': page_number,
                    'coordinates': {
                        'x0': x0,
                        'y0': y0,
                        'x1': x0 + bbox.get('width', 0),
                        'y1': y0 + bbox.get('height', 0)
                    },
                    'object_data': {
                        'category': obj.get('category', CATEGORY_CABINET)
                    }
                }

                self.add_rectangle(rect_data)
                loaded_count += 1

            except Exception as e:
                print(f'Error loading object from JSON: {e}')
                continue

        return loaded_count, skipped_count
