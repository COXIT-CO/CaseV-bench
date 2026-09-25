#!/usr/bin/env python3
"""
Dialogs module for PDF Coordinate Viewer.

This module contains all dialog-related functionality including:
- Object details dialog for creating new rectangles
- Edit rectangle dialog with real-time updates
- Delete confirmation dialog
- Save page dialog for JSON export
"""

from .dialog_manager import DialogManager

__all__ = ['DialogManager']
