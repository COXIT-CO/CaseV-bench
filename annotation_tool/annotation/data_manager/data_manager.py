#!/usr/bin/env python3
"""
Data management module for the PDF Coordinate Viewer application.
Handles saving and loading rectangle data to/from JSON files.
"""

import json
import os
from config.constants import (
    JSON_PAGE_NUMBER,
    JSON_CABINETS,
    JSON_IDENTIFYING_PROPERTIES,
    JSON_SUBCLASS_DESCRIPTION,
    JSON_X0,
    JSON_Y0,
    JSON_X1,
    JSON_Y1,
    JSON_DIMENSIONS,
    JSON_WIDTH,
    JSON_HEIGHT,
    JSON_DEPTH,
    JSON_ECALL_DATA,
    JSON_TIMES_REFERENCED,
    JSON_ROOMS,
    SUBCLASS_REQUIRED,
    X1_GREATER_THAN_X0,
    Y1_GREATER_THAN_Y0
)


class DataManager:
    """Handles data operations for rectangle annotations."""

    def __init__(self):
        """Initialize the data manager."""
        pass

    def save_page_to_json(self,
                          page_number_text,
                          page_rectangles,
                          save_directory,
                          page_number):
        """
        Save page rectangles data to JSON file in the specified format.

        Args:
            page_number_text (str): Page number text entered by user
                (for JSON content)
            page_rectangles (list): List of rectangle data for this page
            save_directory (str): Directory where the JSON file will be saved

        Returns:
            str: Path to the saved file

        Raises:
            Exception: If saving fails
        """
        # Create output data structure
        output_data = {
            JSON_PAGE_NUMBER: page_number_text,
            JSON_CABINETS: []
        }

        # Convert rectangle data to the required format
        for rect_data in page_rectangles:
            cabinet_data = self._convert_rectangle_to_cabinet_data(rect_data)
            output_data[JSON_CABINETS].append(cabinet_data)

        # Generate filename using actual page number
        filename = f'{int(page_number)+1}.json'

        # Save to selected directory
        file_path = os.path.join(save_directory, filename)

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=4, ensure_ascii=False)
            return file_path
        except Exception as e:
            raise Exception(f'Failed to write JSON file: {str(e)}') from e

    def _convert_rectangle_to_cabinet_data(self, rect_data):
        """
        Convert rectangle data to cabinet JSON format.

        Args:
            rect_data (dict): Rectangle data from the application

        Returns:
            dict: Cabinet data in JSON format
        """
        coords = rect_data['coordinates']
        obj_data = rect_data['object_data']

        return {
            JSON_IDENTIFYING_PROPERTIES: {
                JSON_SUBCLASS_DESCRIPTION: obj_data.get(
                    'subclass_description', ''
                ),
                JSON_X0: coords['x0'],
                JSON_Y0: coords['y0'],
                JSON_X1: coords['x1'],
                JSON_Y1: coords['y1']
            },
            JSON_DIMENSIONS: {
                JSON_WIDTH: obj_data.get('width', 0),
                JSON_HEIGHT: obj_data.get('height', 0),
                JSON_DEPTH: obj_data.get('depth', 0)
            },
            JSON_ECALL_DATA: {
                JSON_TIMES_REFERENCED: obj_data.get('times_referenced', 0),
                JSON_ROOMS: obj_data.get('rooms', [])
            }
        }

    def load_page_from_json(self, file_path):
        """
        Load page data from JSON file.

        Args:
            file_path (str): Path to the JSON file

        Returns:
            dict: Loaded page data

        Raises:
            Exception: If loading fails
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return self._convert_json_to_rectangle_data(data)
        except Exception as e:
            raise Exception(f'Failed to load JSON file: {str(e)}') from e

    def _convert_json_to_rectangle_data(self, json_data):
        """
        Convert JSON data back to rectangle format.

        Args:
            json_data (dict): JSON data from file

        Returns:
            dict: Rectangle data in application format
        """
        rectangles = []

        for cabinet in json_data.get(JSON_CABINETS, []):
            identifying = cabinet.get(JSON_IDENTIFYING_PROPERTIES, {})
            dimensions = cabinet.get(JSON_DIMENSIONS, {})
            ecall = cabinet.get(JSON_ECALL_DATA, {})

            rect_data = {
                'coordinates': {
                    'x0': identifying.get(JSON_X0, 0),
                    'y0': identifying.get(JSON_Y0, 0),
                    'x1': identifying.get(JSON_X1, 0),
                    'y1': identifying.get(JSON_Y1, 0)
                },
                'object_data': {
                    'subclass_description': identifying.get(
                        JSON_SUBCLASS_DESCRIPTION, ''
                    ),
                    'width': dimensions.get(JSON_WIDTH, 0),
                    'height': dimensions.get(JSON_HEIGHT, 0),
                    'depth': dimensions.get(JSON_DEPTH, 0),
                    'times_referenced': ecall.get(JSON_TIMES_REFERENCED, 0),
                    'rooms': ecall.get(JSON_ROOMS, [])
                }
            }
            rectangles.append(rect_data)

        return {
            'page_number': json_data.get(JSON_PAGE_NUMBER, ''),
            'rectangles': rectangles
        }

    def validate_rectangle_data(self, rect_data):
        """
        Validate rectangle data before saving.

        Args:
            rect_data (dict): Rectangle data to validate

        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            # Check required fields
            obj_data = rect_data.get('object_data', {})
            coords = rect_data.get('coordinates', {})

            # Validate mandatory fields
            if not obj_data.get('subclass_description', '').strip():
                return False, SUBCLASS_REQUIRED

            # Validate coordinates
            x0 = coords.get('x0', 0)
            y0 = coords.get('y0', 0)
            x1 = coords.get('x1', 0)
            y1 = coords.get('y1', 0)

            if x0 >= x1:
                return False, X1_GREATER_THAN_X0

            if y0 >= y1:
                return False, Y1_GREATER_THAN_Y0

            return True, ''

        except Exception as e:
            return False, f'Validation error: {str(e)}'
