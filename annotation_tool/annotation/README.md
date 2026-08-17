# Test Data Annotator

This tool provides a graphical interface for annotating PDF documents with bounding boxes, subclass description, and other info needed to verify if a processing result CSV file is correct. It is designed to help create test data for quality assurance of the Autoscan/Archiscan object detection system.

## Prerequisites

Before running the annotation tool, ensure you have the following installed:

- **Python 3.12** - This specific version is required for the annotation tool to work properly

## Usage

To start the annotation tool, use the dedicated make target from the project root directory:

```bash
make start-test-data-annotator
```

This target will start the annotation tool.

**The below videos describe how to use the tool:**
1. [How to save data for the quality tests](https://drive.google.com/file/d/1O1QV-XPN8v2ypBrj-z-TWdH0nEb-bEnY/view?usp=drive_link).
2. [How to count `referenced times` value](https://drive.google.com/file/d/1RdTlwjkYxTsxuvyUkwDxqIhxiY73hU17/view?usp=drive_link).
3. [How to calculate a cabinet depth](https://drive.google.com/file/d/1kVT1GmCV4WYktXS2jCL2m9HXw606nCTj/view?usp=drive_link).
4. [How to annotate room numbers](https://drive.google.com/file/d/1uVEj3vEVP0bHA1WaCk-0PEX7T7KlOdaQ/view?usp=drive_link).

**More details on the tool:**
* The per-page cabinets info must be saved only to the dirs in the following format:
`<repo-dir>/tests/quality_tests/test_data/<dir-with-name-of-the-PDF-file>/expected_per_page_data/`.
* A single JSON file with cabinets is created per a page of a PDF.
* If there is no created yet cabinet records in an opened PDF - it is possible to open a directory with previously created JSON files - hence edit them.
