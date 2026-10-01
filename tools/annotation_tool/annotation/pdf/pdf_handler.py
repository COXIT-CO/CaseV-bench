#!/usr/bin/env python3
"""
PDF handling module for the PDF Coordinate Viewer application.
Manages PDF document operations, page rendering, and navigation.
"""

import sys
import gc
import time
import fitz  # PyMuPDF
from PIL import Image, ImageTk # type: ignore
import io
import math
import os
from config.constants import (
    MAX_ZOOM_FACTOR,
    MIN_ZOOM_FACTOR,
    ZOOM_STEP,
    PDF_DPI,
    NO_PDF_LOADED
)

# macOS-specific imports
_nsautorelease_pool = None
if sys.platform == 'darwin':
    try:
        from Foundation import NSAutoreleasePool
        _nsautorelease_pool = NSAutoreleasePool
    except ImportError:
        pass


class AutoreleasePoolManager:
    """Context manager for macOS autorelease pool management."""

    def __init__(self):
        self.pool = None
        self.is_macos = sys.platform == 'darwin'

    def __enter__(self):
        if self.is_macos and _nsautorelease_pool:
            self.pool = _nsautorelease_pool.alloc().init()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.pool is not None:
            self.pool.drain()
            self.pool = None


class PDFHandler:
    """Handles all PDF-related operations."""

    def __init__(self):
        """Initialize the PDF handler."""
        self.pdf_document = None
        self.current_page_num = 0
        self.zoom_factor = 1.0
        self.page_image = None
        self.photo_image = None
        self.file_path = None
        self.is_macos = sys.platform == 'darwin'

    def open_pdf(self, file_path):
        """
        Open a PDF file.

        Args:
            file_path (str): Path to the PDF file

        Returns:
            bool: True if successful, False otherwise

        Raises:
            Exception: If opening fails
        """
        try:
            # Close existing document if any
            if self.pdf_document is not None:
                self.pdf_document.close()

            # Open new PDF with macOS-specific memory management
            if self.is_macos is True:
                with AutoreleasePoolManager():
                    self.pdf_document = fitz.open(file_path)
                    # Force immediate memory cleanup on macOS
                    gc.collect()
            else:
                self.pdf_document = fitz.open(file_path)

            self.current_page_num = 0
            self.zoom_factor = 1.0
            self.file_path = file_path
            return True

        except Exception as e:
            error_msg = f'Failed to open PDF file: {str(e)}'
            if self.is_macos is True:
                error_msg += ' (macOS memory management applied)'
            raise Exception(error_msg) from e

    def close_pdf(self):
        """Close the current PDF document."""
        if self.pdf_document is not None:
            self.pdf_document.close()
            self.pdf_document = None

        # Clean up images
        self.page_image = None
        self.photo_image = None

        # Reset state
        self.current_page_num = 0
        self.zoom_factor = 1.0

        # Force cleanup on macOS
        if self.is_macos is True:
            gc.collect()

    def cleanup_images(self):
        """Clean up image references for memory management."""
        if self.is_macos is True:
            # More aggressive cleanup on macOS
            if self.photo_image is not None:
                self.photo_image = None
            if self.page_image is not None:
                self.page_image = None
            gc.collect()

    def get_page_count(self):
        """
        Get the total number of pages in the PDF.

        Returns:
            int: Number of pages, 0 if no PDF loaded
        """
        return len(self.pdf_document) if self.pdf_document else 0

    def get_project_id(self):
        """
        Derive a project id from the open PDF's filename by stripping
        its extension(s).

        Returns:
            str: Project id, empty string if no PDF is loaded
        """
        if not self.file_path:
            return ''

        stem = os.path.basename(self.file_path)
        while True:
            new_stem, ext = os.path.splitext(stem)
            if ext.lower() == '.pdf':
                stem = new_stem
            else:
                break
        return stem

    def get_current_page_number(self):
        """
        Get the current page number (0-based).

        Returns:
            int: Current page number
        """
        return self.current_page_num

    def set_current_page(self, page_num):
        """
        Set the current page number.

        Args:
            page_num (int): Page number (0-based)

        Returns:
            bool: True if page number is valid and set
        """
        if self.pdf_document and 0 <= page_num < len(self.pdf_document):
            self.current_page_num = page_num
            return True
        return False

    def next_page(self):
        """
        Navigate to the next page.

        Returns:
            bool: True if navigation was successful
        """
        if (self.pdf_document and
            self.current_page_num < len(self.pdf_document) - 1):
            self.current_page_num += 1
            return True
        return False

    def previous_page(self):
        """
        Navigate to the previous page.

        Returns:
            bool: True if navigation was successful
        """
        if self.pdf_document and self.current_page_num > 0:
            self.current_page_num -= 1
            return True
        return False

    def zoom_in(self):
        """
        Increase zoom level.

        Returns:
            bool: True if zoom was increased
        """
        if self.pdf_document:
            if self.zoom_factor >= MAX_ZOOM_FACTOR:
                return False
            self.zoom_factor = min(
                self.zoom_factor * ZOOM_STEP, MAX_ZOOM_FACTOR
            )
            return True
        return False

    def zoom_out(self):
        """
        Decrease zoom level.

        Returns:
            bool: True if zoom was decreased
        """
        if self.pdf_document:
            self.zoom_factor = max(
                self.zoom_factor / ZOOM_STEP, MIN_ZOOM_FACTOR
            )
            return True
        return False

    def get_zoom_factor(self):
        """
        Get the current zoom factor.

        Returns:
            float: Current zoom factor
        """
        return self.zoom_factor

    def render_current_page(self):
        """
        Render the current page as an image.

        Returns:
            ImageTk.PhotoImage: Rendered page image for tkinter

        Raises:
            Exception: If rendering fails
        """
        if not self.pdf_document:
            raise Exception('No PDF document loaded')

        try:
            if self.is_macos is True:
                # macOS-specific rendering with multiple safety measures

                # Step 1: Clear previous images to free memory
                if self.photo_image is not None:
                    self.photo_image = None
                if self.page_image is not None:
                    self.page_image = None

                # Force garbage collection before rendering
                gc.collect()

                # Step 2: Render with autorelease pool if available
                with AutoreleasePoolManager():
                    page = self.pdf_document[self.current_page_num]
                    mat = fitz.Matrix(self.zoom_factor, self.zoom_factor)
                    pix = page.get_pixmap(matrix=mat)  # type: ignore

                    # Convert to PIL Image
                    img_data = pix.tobytes('ppm')
                    self.page_image = Image.open(io.BytesIO(img_data))

                    # Create PhotoImage with explicit reference
                    self.photo_image = ImageTk.PhotoImage(self.page_image)

                    # Clean up pixmap immediately
                    pix = None
                    img_data = None

                    # Force cleanup
                    gc.collect()

                    # Small delay to let macOS process memory cleanup
                    time.sleep(0.01)  # 10ms delay

            else:
                # Standard rendering for non-macOS systems
                page = self.pdf_document[self.current_page_num]
                mat = fitz.Matrix(self.zoom_factor, self.zoom_factor)
                pix = page.get_pixmap(matrix=mat)  # type: ignore
                img_data = pix.tobytes('ppm')
                self.page_image = Image.open(io.BytesIO(img_data))
                self.photo_image = ImageTk.PhotoImage(self.page_image)

            return self.photo_image

        except Exception as e:
            error_msg = f'Failed to render page: {str(e)}'
            if self.is_macos is True:
                error_msg += ' (macOS enhanced rendering applied)'
                # Cleanup on error
                gc.collect()
            raise Exception(error_msg) from e

    def get_page_image(self):
        """
        Get the current page image.

        Returns:
            ImageTk.PhotoImage: Current page image
        """
        return self.photo_image

    def convert_canvas_to_pdf_coordinates(self, canvas_x, canvas_y):
        """
        Convert canvas coordinates to PDF coordinates.

        Args:
            canvas_x (float): X coordinate on canvas
            canvas_y (float): Y coordinate on canvas

        Returns:
            tuple: (pdf_x, pdf_y) in PDF coordinate space
        """
        if self.zoom_factor > 0:
            pdf_x = canvas_x / self.zoom_factor
            pdf_y = canvas_y / self.zoom_factor
        else:
            pdf_x = 0.0
            pdf_y = 0.0
        return pdf_x, pdf_y

    def convert_pdf_to_canvas_coordinates(self, pdf_x, pdf_y):
        """
        Convert PDF coordinates to canvas coordinates.

        Args:
            pdf_x (float): X coordinate in PDF space
            pdf_y (float): Y coordinate in PDF space

        Returns:
            tuple: (canvas_x, canvas_y) in canvas coordinate space
        """
        canvas_x = pdf_x * self.zoom_factor
        canvas_y = pdf_y * self.zoom_factor
        return canvas_x, canvas_y

    def calculate_line_length_inches(self, start_x, start_y, end_x, end_y):
        """
        Calculate line length in inches for measurement tool.

        Args:
            start_x, start_y (float): Start coordinates in canvas space
            end_x, end_y (float): End coordinates in canvas space

        Returns:
            float: Length in inches
        """

        # Calculate pixel length
        length_pixels = math.sqrt(
            (end_x - start_x) ** 2 + (end_y - start_y) ** 2
        )

        # Convert to PDF points, then to inches
        length_pdf_points = (
            length_pixels / self.zoom_factor if self.zoom_factor > 0 else 0
        )
        length_inches = length_pdf_points / PDF_DPI

        return length_inches

    def get_file_info(self, file_path):
        """
        Get file information for display.

        Args:
            file_path (str): Path to the PDF file

        Returns:
            dict: File information
        """
        filename = os.path.basename(file_path)
        page_count = self.get_page_count()

        return {
            'filename': filename,
            'page_count': page_count,
            'current_page': self.current_page_num + 1,  # 1-based for display
            'zoom_percentage': int(self.zoom_factor * 100)
        }

    def get_page_info_text(self):
        """
        Get formatted page information text.

        Returns:
            str: Formatted page info
        """
        if not self.pdf_document:
            return NO_PDF_LOADED

        total_pages = len(self.pdf_document)
        return f'Page {self.current_page_num + 1} of {total_pages}'

    def get_zoom_info_text(self):
        """
        Get formatted zoom information text.

        Returns:
            str: Formatted zoom info
        """
        return f'{int(self.zoom_factor * 100)}%'

    def get_page_options_for_dropdown(self):
        """
        Get page options for dropdown menu.

        Returns:
            list: List of page option strings
        """
        if not self.pdf_document:
            return []

        total_pages = len(self.pdf_document)
        return [f'Page {i + 1}' for i in range(total_pages)]

    def parse_page_selection(self, selection_text):
        """
        Parse page selection from dropdown.

        Args:
            selection_text (str): Selected text from dropdown

        Returns:
            int or None: Page number (0-based) or None if invalid
        """
        if selection_text.startswith('Page '):
            try:
                display_page_num = int(selection_text.split(' ')[1])
                # Convert to 0-based index
                page_num = display_page_num - 1

                # Validate page number
                if 0 <= page_num < len(self.pdf_document):  # type: ignore
                    return page_num
            except (ValueError, IndexError):
                pass
        return None

    def is_pdf_loaded(self):
        """
        Check if a PDF document is currently loaded.

        Returns:
            bool: True if PDF is loaded
        """
        return self.pdf_document is not None
