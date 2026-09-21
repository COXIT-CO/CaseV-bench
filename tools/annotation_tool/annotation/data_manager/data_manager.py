#!/usr/bin/env python3
"""
Data management module for the PDF Coordinate Viewer application.
Handles saving and loading object location data to/from JSON files.
"""

import json
import os
from config.constants import (
    JSON_PROJECT_ID,
    JSON_OBJECTS,
    JSON_ID,
    JSON_CATEGORY,
    JSON_PAGE,
    JSON_BBOX,
    JSON_BBOX_X,
    JSON_BBOX_Y,
    JSON_BBOX_WIDTH,
    JSON_BBOX_HEIGHT,
    CATEGORY_CABINET,
    CATEGORY_ID_PREFIXES,
)


class DataManager:
    """Handles data operations for object location annotations."""

    def __init__(self):
        """Initialize the data manager."""
        pass

    def save_project_to_json(self, project_id, all_rectangles, save_directory):
        """
        Save all annotated objects (across every page) to a single
        project-level JSON file.

        Args:
            project_id (str): Project identifier, used for the output
                filename and JSON content
            all_rectangles (list): List of rectangle data across all pages
            save_directory (str): Directory where the JSON file will be saved

        Returns:
            str: Path to the saved file

        Raises:
            Exception: If saving fails
        """
        sorted_rectangles = sorted(
            all_rectangles,
            key=lambda r: (
                r['page_number'],
                r['coordinates']['y0'],
                r['coordinates']['x0'],
            )
        )

        prefix_counters = {}
        objects = []

        for rect_data in sorted_rectangles:
            objects.append(
                self._convert_rectangle_to_object_data(
                    rect_data, prefix_counters
                )
            )

        output_data = {
            JSON_PROJECT_ID: project_id,
            JSON_OBJECTS: objects
        }

        filename = f'{project_id}-obj-location.json'
        file_path = os.path.join(save_directory, filename)

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            return file_path
        except Exception as e:
            raise Exception(f'Failed to write JSON file: {str(e)}') from e

    def _convert_rectangle_to_object_data(self, rect_data, prefix_counters):
        """
        Convert rectangle data to object JSON format, assigning a
        sequential id per category.

        Args:
            rect_data (dict): Rectangle data from the application
            prefix_counters (dict): Running count of assigned ids per
                category prefix, mutated in place

        Returns:
            dict: Object data in JSON format
        """
        coords = rect_data['coordinates']
        category = rect_data['object_data'].get('category', CATEGORY_CABINET)
        prefix = CATEGORY_ID_PREFIXES.get(category, 'obj')

        prefix_counters[prefix] = prefix_counters.get(prefix, 0) + 1
        object_id = f'{prefix}-{prefix_counters[prefix]:03d}'

        return {
            JSON_ID: object_id,
            JSON_CATEGORY: category,
            JSON_PAGE: rect_data['page_number'] + 1,
            JSON_BBOX: {
                JSON_BBOX_X: coords['x0'],
                JSON_BBOX_Y: coords['y0'],
                JSON_BBOX_WIDTH: coords['x1'] - coords['x0'],
                JSON_BBOX_HEIGHT: coords['y1'] - coords['y0']
            }
        }

    def load_project_from_json(self, file_path):
        """
        Load project object-location data from a JSON file.

        Args:
            file_path (str): Path to the JSON file

        Returns:
            dict: Parsed JSON data

        Raises:
            Exception: If loading fails
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            raise Exception(f'Failed to load JSON file: {str(e)}') from e
