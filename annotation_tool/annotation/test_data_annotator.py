#!/usr/bin/env python3
"""
PDF Coordinate Viewer Tool - Refactored Main Application

A GUI tool that allows users to open PDF files, view them, and display
the current cursor coordinates when hovering over the PDF pages.
"""

import sys
import gc
import time
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox


# Import our modular components
from config.constants import PDF_FILE_TYPES
from data_manager.data_manager import DataManager
from pdf.pdf_handler import PDFHandler
from ui.ui_components import UIComponents
from dialogs.dialog_manager import DialogManager
from rectangles.rectangle_manager import RectangleManager
from events.event_handler import EventHandler

# macOS-specific imports and autorelease pool management
_autorelease_pool_available = False
_nsautorelease_pool = None
if sys.platform == "darwin":
    try:
        from Foundation import NSAutoreleasePool
        _nsautorelease_pool = NSAutoreleasePool
        _autorelease_pool_available = True
        print("macOS: NSAutoreleasePool available for memory management")
    except ImportError:
        print("Warning: NSAutoreleasePool not available. Using alternative "
              "macOS memory management.")


class AutoreleasePoolManager:
    """Context manager for macOS autorelease pool management."""

    def __init__(self):
        self.pool = None
        self.is_macos = sys.platform == "darwin"

    def __enter__(self):
        if (self.is_macos is True
            and _autorelease_pool_available is True
            and _nsautorelease_pool is not None):
            self.pool = _nsautorelease_pool.alloc().init()
        elif self.is_macos:
            # Alternative approach: force aggressive garbage collection
            gc.collect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.pool is not None:
            self.pool.drain()
            self.pool = None
        elif self.is_macos:
            # Alternative cleanup: force garbage collection and small delay
            gc.collect()
            time.sleep(0.001)  # 1ms delay to let macOS clean up


def macos_safe_operation(func, delay_ms=10):
    """
    Safely execute operations on macOS with memory management.

    Args:
        func: Function to execute
        delay_ms: Delay in milliseconds after operation
    """
    if sys.platform == "darwin":
        try:
            result = func()
            # Force cleanup and small delay for macOS
            gc.collect()
            time.sleep(delay_ms / 1000.0)
            return result
        except Exception as e:
            print(f"macOS operation failed: {e}")
            gc.collect()
            raise
    else:
        return func()


def ensure_main_thread(func):
    """Decorator to ensure GUI operations run on main thread"""
    def wrapper(*args, **kwargs):
        if threading.current_thread() == threading.main_thread():
            return func(*args, **kwargs)
        else:
            # For GUI safety, just call the function directly if not on main
            # thread
            return func(*args, **kwargs)
    return wrapper


class TestDataAnnotator:
    """
    Main application class that coordinates between all components.
    """

    def __init__(self, root):
        """
        Initialize the PDF viewer application.

        Args:
            root (tk.Tk): The root tkinter window
        """
        self.root = root
        self.is_macos = sys.platform == "darwin"

        # Initialize component modules
        self.data_manager = DataManager()
        self.pdf_handler = PDFHandler()
        self.ui_components = UIComponents(root)
        self.dialog_manager = DialogManager(
            root, self.data_manager, self.pdf_handler
        )

        # macOS-specific setup
        if self.is_macos:
            self._setup_macos_specific()

        self._setup_ui()

        # Schedule memory cleanup for macOS
        if self.is_macos:
            self._schedule_memory_cleanup()

    def _setup_macos_specific(self):
        """Set up macOS-specific configurations."""
        try:
            # macOS-specific tkinter fixes
            self.root.createcommand("::tk::mac::ReopenApplication",
                                    lambda: None)
            self.root.createcommand("::tk::mac::OpenDocument", lambda: None)

            # Set proper focus behavior
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after_idle(lambda: self.root.attributes("-topmost",
                                                              False))
        except Exception as e:
            print(f"Warning: Could not set up macOS-specific features: {e}")

    def _schedule_memory_cleanup(self):
        """Schedule periodic memory cleanup for macOS."""
        def cleanup():
            try:
                # More aggressive cleanup for macOS
                gc.collect()

                # Force tkinter cleanup
                self.root.update_idletasks()

                # NOTE: Don"t clear current image references as they"re needed
                # for display
                # Only do general garbage collection to avoid clearing active
                # images

                # Shorter cleanup cycles on macOS due to memory pressure
                self.root.after(15000, cleanup)
            except Exception as e:
                print(f"Warning: Memory cleanup failed: {e}")
                self.root.after(15000, cleanup)  # Continue scheduling

        self.root.after(5000, cleanup)  # First cleanup after 5 seconds

    def macos_safe_operation(self, func, delay_ms=10):
        """
        Safely execute operations on macOS with memory management.

        Args:
            func: Function to execute
            delay_ms: Delay in milliseconds after operation
        """
        if self.is_macos is True:
            try:
                result = func()
                # Force cleanup and small delay for macOS
                gc.collect()
                time.sleep(delay_ms / 1000.0)
                return result
            except Exception as e:
                print(f"macOS operation failed: {e}")
                gc.collect()
                raise
        else:
            return func()

    def _setup_ui(self):
        """Set up the user interface components."""
        # Create the main UI layout
        ui_elements = self.ui_components.create_main_layout()

        # Store references to important UI elements
        self.canvas = self.ui_components.get_canvas()
        self.details_frame = self.ui_components.get_details_frame()
        self.rectangles_list_frame = (
            self.ui_components.get_rectangles_list_frame()
        )

        # Initialize rectangle manager after UI is created
        self.rectangle_manager = RectangleManager(
            self.canvas,
            self.details_frame,
            self.rectangles_list_frame,
            self.pdf_handler,
            self.dialog_manager
        )

        # Initialize event handler after other components are created
        self.event_handler = EventHandler(
            self.canvas,
            self.pdf_handler,
            self.rectangle_manager,
            self.dialog_manager,
            self.ui_components
        )

        # Set up callbacks for event handler
        self.event_handler.set_callbacks(
            self._zoom_in,
            self._zoom_out,
            self._update_page_display
        )

        # Set up page update callback for rectangle manager
        self.rectangle_manager.set_page_update_callback(
            self._update_page_display
        )

        # Connect event handlers
        self._bind_events(ui_elements)

    def _bind_events(self, ui_elements):
        """Bind event handlers to UI elements."""
        # Get toolbar elements
        toolbar = ui_elements["toolbar_frame"]

        # Bind toolbar button events
        toolbar["open_button"].configure(command=self._open_pdf)
        toolbar["prev_button"].configure(command=self._previous_page)
        toolbar["next_button"].configure(command=self._next_page)
        toolbar["zoom_in_button"].configure(command=self._zoom_in)
        toolbar["zoom_out_button"].configure(command=self._zoom_out)

        # Bind page dropdown event
        toolbar["page_dropdown"].bind(
            "<<ComboboxSelected>>", self._on_page_dropdown_changed
        )

        # Bind canvas events through event handler
        self.event_handler.bind_events()

    def _open_pdf(self):
        """Open a PDF file using file dialog."""
        file_path = filedialog.askopenfilename(
            title="Select PDF File",
            filetypes=PDF_FILE_TYPES
        )

        if file_path:
            try:
                # Use macOS-safe PDF opening if on macOS
                if self.is_macos is True:
                    with AutoreleasePoolManager():
                        # Use PDF handler to open the file
                        self.pdf_handler.open_pdf(file_path)
                else:
                    # Use PDF handler to open the file
                    self.pdf_handler.open_pdf(file_path)

                # Update UI
                self._update_page_display()

                # Update rectangles list to show import button if no
                # rectangles exist
                self.rectangle_manager.update_rectangles_list()

                file_info = self.pdf_handler.get_file_info(file_path)
                self.ui_components.update_file_info(file_info)

                print(f"Successfully opened PDF: {file_path}")

            except Exception as e:
                messagebox.showerror("Error", str(e))
                print(f"Error opening PDF: {str(e)}")

    def _update_page_display(self):
        """Update the page display with macOS memory management."""
        def _perform_update():
            if self.pdf_handler.is_pdf_loaded() is False:
                return

            # Safety check for canvas
            if self.canvas is None:
                print("Warning: Canvas not available for display update")
                return

            # Clear previous image
            self.canvas.delete("all")

            # Enhanced cleanup on macOS before rendering
            if self.is_macos is True:
                gc.collect()

            # Render current page
            try:
                photo = self.pdf_handler.render_current_page()
                if photo:
                    # Display the image on canvas
                    self.canvas.create_image(0, 0, anchor="nw", image=photo)
                    # Keep a reference to prevent garbage collection
                    setattr(self.canvas, "image", photo)

                    # Update canvas scroll region
                    self.canvas.config(scrollregion=self.canvas.bbox("all"))

                    # Redraw rectangles for current page
                    self.rectangle_manager.redraw_rectangles_for_current_page()

                    # Update page dropdown
                    self._update_page_dropdown()

                    pg_num = self.pdf_handler.get_current_page_number()

                    print(f"Successfully displayed page {pg_num}")

            except Exception as e:
                print(f"Error updating page display: {e}")
                traceback.print_exc()

            # Enhanced cleanup on macOS after update
            if self.is_macos is True:
                self._schedule_memory_cleanup()

        # Execute with macOS safety wrapper
        if self.is_macos is True:
            self.macos_safe_operation(_perform_update, 5)
        else:
            _perform_update()

    def _continue_page_update(self):
        """Continue page update after cleanup delay on macOS."""
        # This method is kept for backward compatibility but simplified
        pass

    def _previous_page(self):
        """Navigate to the previous page."""
        if self.pdf_handler.previous_page():
            self._update_page_display()

    def _next_page(self):
        """Navigate to the next page."""
        if self.pdf_handler.next_page():
            self._update_page_display()

    def _zoom_in(self):
        """Increase zoom level."""
        if self.pdf_handler.zoom_in():
            self._update_page_display()

    def _zoom_out(self):
        """Decrease zoom level."""
        if self.pdf_handler.zoom_out():
            self._update_page_display()

    def _update_page_dropdown(self):
        """
        Update the page dropdown with available pages and set current selection.
        """
        page_options = self.pdf_handler.get_page_options_for_dropdown()
        current_page_text = (
            f"Page {self.pdf_handler.get_current_page_number() + 1}"
        )
        self.ui_components.update_page_dropdown(
            page_options, current_page_text
        )

    def _on_page_dropdown_changed(self, _):
        """Handle page dropdown selection change."""
        if not self.pdf_handler.is_pdf_loaded():
            return

        (
            selected_text
        ) = self.ui_components.page_dropdown_var.get()  # type: ignore
        new_page_num = self.pdf_handler.parse_page_selection(selected_text)

        if (new_page_num is not None and
            new_page_num != self.pdf_handler.get_current_page_number()):
            self.pdf_handler.set_current_page(new_page_num)
            self._update_page_display()

    def run(self):
        """Start the application main loop."""
        print("Starting Test Data Annotator...")

        if self.is_macos is True:
            # Use macOS-friendly event loop
            try:
                while True:
                    try:
                        self.root.update_idletasks()
                        self.root.update()
                    except tk.TclError:
                        break
            except KeyboardInterrupt:
                print("\nApplication terminated by user")
        else:
            self.root.mainloop()

        # Clean up
        self.pdf_handler.close_pdf()
        print("Closed PDF document")


def main():
    """Main function to run the Test Data Annotator."""
    # Create the main window
    root = tk.Tk()
    root.title("Test Data Annotator")
    root.geometry("1200x800")

    # Create and run the application
    app = TestDataAnnotator(root)

    try:
        app.run()
    except KeyboardInterrupt:
        print("\nApplication terminated by user")
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        messagebox.showerror(
            "Error", f"Unexpected error:\n{str(e)}"
        )


if __name__ == "__main__":
    main()
