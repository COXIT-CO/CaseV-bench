#!/usr/bin/env python3
"""
Dialog Manager for PDF Coordinate Viewer.

This module handles all dialog windows including object details, editing,
deletion confirmation, and save functionality.
"""

import tkinter as tk
from tkinter import messagebox, filedialog, ttk

from config.constants import (
    OBJECT_DETAILS_TITLE,
    OBJECT_DETAILS_DIALOG_SIZE,
    COORDINATES_FONT,
    EDIT_RECTANGLE_TITLE,
    EDIT_TITLE_FONT,
    DELETE_RECTANGLE_TITLE,
    DELETE_DIALOG_SIZE,
    SECTION_FONT,
    SMALL_FONT,
    DELETE_BUTTON,
    CANCEL_BUTTON,
    SAVE_PROJECT_TITLE,
    SAVE_DIALOG_SIZE,
    PROJECT_ID_LABEL,
    SAVE_SUCCESS,
    PROJECT_ID_REQUIRED,
    SAVE_BUTTON,
    CATEGORY_LABEL,
    CATEGORY_OPTIONS,
    CATEGORY_CABINET,
    OK_BUTTON
)


class DialogManager:
    """
    Manages all dialog windows for the PDF Coordinate Viewer application.
    """

    def __init__(self, root, data_manager, pdf_handler):
        """
        Initialize the dialog manager.

        Args:
            root (tk.Tk): Root window
            data_manager: Data manager instance
            pdf_handler: PDF handler instance
        """
        self.root = root
        self.data_manager = data_manager
        self.pdf_handler = pdf_handler

    def show_object_details_dialog(self, x0, y0, x1, y1):
        """
        Show dialog to enter object details for a new rectangle.

        Args:
            x0, y0, x1, y1 (float): Rectangle coordinates in PDF space

        Returns:
            dict or None: Object data if user clicked OK, None if cancelled
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(OBJECT_DETAILS_TITLE)
        dialog.geometry(OBJECT_DETAILS_DIALOG_SIZE)
        dialog.transient(self.root)
        dialog.grab_set()

        # Center the dialog
        self._center_dialog(dialog)

        # Result variable
        result = {'submitted': False, 'data': None}

        # Create main frame
        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Coordinates display (read-only)
        ttk.Label(main_frame, text='Coordinates:').grid(
            row=0, column=0, sticky='w', pady=(0, 5)
        )
        coords_text = (
            f'x0: {int(x0)}, y0: {int(y0)}, x1: {int(x1)}, y1: {int(y1)}'
        )
        ttk.Label(main_frame, text=coords_text, font=COORDINATES_FONT).grid(
            row=0, column=1, sticky='w', pady=(0, 5)
        )

        # Create form fields
        form_vars = self._create_object_form_fields(main_frame)

        # Create buttons
        self._create_dialog_buttons(
            main_frame,
            lambda: self._on_object_details_ok(result, form_vars, dialog),
            dialog.destroy,
            row=2
        )

        # Focus on first field
        form_vars['category_combo'].focus()

        # Wait for dialog to close
        dialog.wait_window()

        return result['data'] if result['submitted'] else None

    def show_edit_rectangle_dialog(self, rect_data, canvas, update_callback):
        """
        Show dialog to edit rectangle details including coordinates
        with spinboxes.

        Args:
            rect_data (dict): Rectangle data to edit
            canvas: Canvas widget for real-time updates
            update_callback: Callback function for applying updates
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(EDIT_RECTANGLE_TITLE)
        dialog.geometry('600x600')
        dialog.resizable(False, False)
        dialog.transient(self.root)

        # Center and setup dialog
        self._center_dialog(dialog)
        dialog.deiconify()
        dialog.lift()
        dialog.grab_set()

        result = None

        # Create main frame
        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Title
        ttk.Label(
            main_frame,
            text='Edit Rectangle Details',
            font=EDIT_TITLE_FONT
        ).grid(row=0, column=0, columnspan=3, pady=(0, 15))

        # Create coordinate spinboxes with real-time updates
        coord_vars = self._create_coordinate_spinboxes(
            main_frame, rect_data, canvas
        )

        # Create object detail fields
        form_vars = self._create_edit_form_fields(main_frame, rect_data)

        # Create buttons
        self._create_dialog_buttons(
            main_frame,
            lambda: self._on_edit_save(
                result, coord_vars, form_vars, dialog,
                update_callback, rect_data
            ),
            lambda: self._on_edit_cancel(result, dialog),
            row=7,
            columnspan=3
        )

        # Wait for dialog to close
        dialog.wait_window()

    def show_delete_confirmation_dialog(self, rect_data, delete_callback):
        """
        Show confirmation dialog for deleting a rectangle.

        Args:
            rect_data (dict): Rectangle data to delete
            delete_callback: Callback function to execute deletion
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(DELETE_RECTANGLE_TITLE)
        dialog.geometry(DELETE_DIALOG_SIZE)
        dialog.transient(self.root)

        # Center the dialog
        self._center_dialog(dialog)

        # Make dialog visible before grab_set
        dialog.deiconify()
        dialog.lift()
        dialog.grab_set()

        # Main frame
        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Message
        message = 'Are you sure you want to delete this rectangle annotation?'
        ttk.Label(main_frame, text=message, font=SECTION_FONT).pack(
            pady=(0, 20)
        )

        # Show rectangle details
        coords = rect_data['coordinates']
        obj_data = rect_data['object_data']

        details_text = (
            f"Category: {obj_data.get('category', 'N/A')}\n"
        )
        details_text += (
            f"Coordinates: ({coords['x0']}, {coords['y0']}) to "
            f"({coords['x1']}, {coords['y1']})"
        )

        ttk.Label(
            main_frame, text=details_text, font=SMALL_FONT,
            foreground='gray'
        ).pack(pady=(0, 20))

        # Buttons
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(fill=tk.X)

        def on_delete():
            delete_callback(rect_data)
            dialog.destroy()

        ttk.Button(
            buttons_frame, text=DELETE_BUTTON, command=on_delete
        ).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(
            buttons_frame, text=CANCEL_BUTTON, command=dialog.destroy
        ).pack(side=tk.RIGHT)

        # Wait for dialog
        dialog.wait_window()

    def show_save_project_dialog(self, all_rectangles):
        """
        Show dialog to save all annotated objects to a single
        project-level JSON file.

        Args:
            all_rectangles (list): List of rectangles across every page
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(SAVE_PROJECT_TITLE)
        dialog.geometry(SAVE_DIALOG_SIZE)
        dialog.resizable(False, False)
        dialog.transient(self.root)

        # Center and setup dialog
        self._center_dialog(dialog)
        dialog.deiconify()
        dialog.lift()
        dialog.grab_set()

        # Create main frame
        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Title
        title_label = ttk.Label(
            main_frame,
            text='Save Project Data',
            font=SECTION_FONT
        )
        title_label.pack(pady=(0, 15))

        # Project ID input
        ttk.Label(main_frame, text=PROJECT_ID_LABEL).pack(
            anchor='w', pady=(0, 5)
        )
        project_id_var = tk.StringVar(
            value=self.pdf_handler.get_project_id()
        )
        project_id_entry = ttk.Entry(
            main_frame, textvariable=project_id_var, width=30
        )
        project_id_entry.pack(pady=(0, 20))

        # Info text
        page_count = len({
            rect['page_number'] for rect in all_rectangles
        })
        info_label = ttk.Label(
            main_frame,
            text=(
                f'This will save {len(all_rectangles)} object(s) across '
                f'{page_count} page(s) to a JSON file.'
            ),
            font=SMALL_FONT,
            foreground='gray'
        )
        info_label.pack(pady=(0, 20))

        # Create buttons
        self._create_save_dialog_buttons(
            main_frame, project_id_var, all_rectangles,
            project_id_entry, dialog
        )

        # Focus on page number entry
        page_number_entry.focus_set()
        page_number_entry.select_range(0, tk.END)

        # Wait for dialog to close
        dialog.wait_window()

    def _center_dialog(self, dialog):
        """Center a dialog window on the screen."""
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f'+{x}+{y}')

    def _create_object_form_fields(self, parent):
        """Create form fields for object details dialog."""
        form_vars = {}

        # Category (required, defaults to cabinet)
        ttk.Label(parent, text=CATEGORY_LABEL).grid(
            row=1, column=0, sticky='w', pady=5
        )
        form_vars['category_var'] = tk.StringVar(value=CATEGORY_CABINET)
        form_vars['category_combo'] = ttk.Combobox(
            parent, textvariable=form_vars['category_var'],
            values=CATEGORY_OPTIONS, state='readonly', width=28
        )
        form_vars['category_combo'].grid(
            row=1, column=1, sticky='w', pady=5
        )

        return form_vars

    def _create_coordinate_spinboxes(self, parent, rect_data, canvas):
        """Create coordinate spinboxes with real-time updates."""
        # Coordinates section with spinboxes
        coord_frame = ttk.LabelFrame(parent, text='Coordinates')
        coord_frame.grid(
            row=1, column=0, columnspan=3, sticky='ew', pady=(0, 15)
        )
        coord_frame.columnconfigure(1, weight=1)

        coords = rect_data['coordinates']
        coord_vars = {}

        # X0 coordinate
        ttk.Label(coord_frame, text='X0:').grid(
            row=0, column=0, sticky='w', padx=5, pady=5
        )
        coord_vars['x0_var'] = tk.IntVar(value=coords['x0'])
        coord_vars['x0_spinbox'] = ttk.Spinbox(
            coord_frame, from_=-9999, to=9999,
            textvariable=coord_vars['x0_var'], width=10,
            command=lambda: self._update_rectangle_realtime(
                rect_data, coord_vars, canvas
            )
        )
        coord_vars['x0_spinbox'].grid(
            row=0, column=1, sticky='w', padx=5, pady=5
        )

        # Y0 coordinate
        ttk.Label(coord_frame, text='Y0:').grid(
            row=1, column=0, sticky='w', padx=5, pady=5
        )
        coord_vars['y0_var'] = tk.IntVar(value=coords['y0'])
        coord_vars['y0_spinbox'] = ttk.Spinbox(
            coord_frame, from_=-9999, to=9999,
            textvariable=coord_vars['y0_var'], width=10,
            command=lambda: self._update_rectangle_realtime(
                rect_data, coord_vars, canvas
            )
        )
        coord_vars['y0_spinbox'].grid(
            row=1, column=1, sticky='w', padx=5, pady=5
        )

        # X1 coordinate
        ttk.Label(coord_frame, text='X1:').grid(
            row=0, column=2, sticky='w', padx=5, pady=5
        )
        coord_vars['x1_var'] = tk.IntVar(value=coords['x1'])
        coord_vars['x1_spinbox'] = ttk.Spinbox(
            coord_frame, from_=-9999, to=9999,
            textvariable=coord_vars['x1_var'], width=10,
            command=lambda: self._update_rectangle_realtime(
                rect_data, coord_vars, canvas
            )
        )
        coord_vars['x1_spinbox'].grid(
            row=0, column=3, sticky='w', padx=5, pady=5
        )

        # Y1 coordinate
        ttk.Label(coord_frame, text='Y1:').grid(
            row=1, column=2, sticky='w', padx=5, pady=5
        )
        coord_vars['y1_var'] = tk.IntVar(value=coords['y1'])
        coord_vars['y1_spinbox'] = ttk.Spinbox(
            coord_frame, from_=-9999, to=9999,
            textvariable=coord_vars['y1_var'], width=10,
            command=lambda: self._update_rectangle_realtime(
                rect_data, coord_vars, canvas
            )
        )
        coord_vars['y1_spinbox'].grid(
            row=1, column=3, sticky='w', padx=5, pady=5
        )

        return coord_vars

    def _create_edit_form_fields(self, parent, rect_data):
        """Create form fields for edit dialog."""
        obj_data = rect_data['object_data']
        form_vars = {}

        # Category
        ttk.Label(parent, text=CATEGORY_LABEL).grid(
            row=2, column=0, sticky='w', pady=5
        )
        form_vars['category_var'] = tk.StringVar(
            value=obj_data.get('category', CATEGORY_CABINET)
        )
        ttk.Combobox(
            parent, textvariable=form_vars['category_var'],
            values=CATEGORY_OPTIONS, state='readonly', width=38
        ).grid(row=2, column=1, columnspan=2, sticky='w', pady=5)

        return form_vars

    def _create_dialog_buttons(self, parent, ok_command, cancel_command, row,
                               columnspan=2):
        """Create OK/Cancel buttons for dialogs."""
        button_frame = ttk.Frame(parent)
        button_frame.grid(row=row, column=0, columnspan=columnspan, pady=20)

        ttk.Button(
            button_frame, text=OK_BUTTON, command=ok_command
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(
            button_frame, text=CANCEL_BUTTON, command=cancel_command
        ).pack(side=tk.LEFT)

    def _create_save_dialog_buttons(self, parent, project_id_var,
                                    all_rectangles, _, dialog):
        """Create buttons for save dialog."""
        button_frame = ttk.Frame(parent)
        button_frame.pack(pady=(10, 0))

        def on_save():
            project_id = project_id_var.get().strip()
            if not project_id:
                messagebox.showerror('Error', PROJECT_ID_REQUIRED)
                return

            save_directory = filedialog.askdirectory(
                title='Select Directory to Save JSON File',
                initialdir='.'
            )

            if not save_directory:
                return

            try:
                self.data_manager.save_project_to_json(
                    project_id,
                    all_rectangles,
                    save_directory
                )
                messagebox.showinfo('Success', SAVE_SUCCESS)
                dialog.destroy()
            except Exception as e:
                messagebox.showerror(
                    'Error', f'Failed to save project data:\n{str(e)}'
                )

        ttk.Button(
            button_frame, text=SAVE_BUTTON, command=on_save, width=12
        ).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Button(
            button_frame, text=CANCEL_BUTTON, command=dialog.destroy,
            width=12
        ).pack(side=tk.LEFT)

    def _on_object_details_ok(self, result, form_vars, dialog):
        """Handle OK button in object details dialog."""
        result['data'] = {
            'category': form_vars['category_var'].get()
        }
        result['submitted'] = True
        dialog.destroy()

    def _on_edit_save(self, _, coord_vars, form_vars, dialog,
                      update_callback, rect_data):
        """Handle save button in edit dialog."""
        # Validate coordinate logic
        x0_val = coord_vars['x0_var'].get()
        y0_val = coord_vars['y0_var'].get()
        x1_val = coord_vars['x1_var'].get()
        y1_val = coord_vars['y1_var'].get()

        if x0_val >= x1_val:
            messagebox.showerror(
                'Invalid Coordinates', 'X1 must be greater than X0.'
            )
            return

        if y0_val >= y1_val:
            messagebox.showerror(
                'Invalid Coordinates', 'Y1 must be greater than Y0.'
            )
            return

        # Create updated data
        updated_data = {
            'coordinates': {
                'x0': x0_val,
                'y0': y0_val,
                'x1': x1_val,
                'y1': y1_val
            },
            'object_data': {
                'category': form_vars['category_var'].get()
            }
        }

        # Apply the changes through callback
        update_callback(rect_data, updated_data)
        dialog.destroy()

    def _on_edit_cancel(self, _, dialog):
        """Handle cancel button in edit dialog."""
        dialog.destroy()

    def _update_rectangle_realtime(self, rect_data, coord_vars, canvas):
        """Update rectangle on canvas in real-time as coordinates change."""
        # Only update if rectangle is on current page and has canvas ID
        if (rect_data['page_number'] !=
                self.pdf_handler.get_current_page_number() or
                not rect_data.get('canvas_id')):
            return

        try:
            # Get current coordinate values from spinboxes
            x0_val = coord_vars['x0_var'].get()
            y0_val = coord_vars['y0_var'].get()
            x1_val = coord_vars['x1_var'].get()
            y1_val = coord_vars['y1_var'].get()

            # Validate coordinate logic (prevent invalid rectangles)
            if x0_val >= x1_val or y0_val >= y1_val:
                # Invalid coordinates - change rectangle color to show error
                canvas.itemconfig(
                    rect_data['canvas_id'],
                    outline='red', fill='pink', stipple='gray25', width=3
                )
                return

            # Convert PDF coordinates to canvas coordinates
            canvas_x0 = x0_val * self.pdf_handler.get_zoom_factor()
            canvas_y0 = y0_val * self.pdf_handler.get_zoom_factor()
            canvas_x1 = x1_val * self.pdf_handler.get_zoom_factor()
            canvas_y1 = y1_val * self.pdf_handler.get_zoom_factor()

            # Update rectangle position and size on canvas
            canvas.coords(
                rect_data['canvas_id'],
                canvas_x0, canvas_y0, canvas_x1, canvas_y1
            )

            # Reset rectangle appearance to normal editing style
            canvas.itemconfig(
                rect_data['canvas_id'],
                outline='orange', fill='lightyellow',
                stipple='gray25', width=3
            )

        except tk.TclError:
            # Handle potential errors during coordinate updates
            pass
