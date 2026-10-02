VENV := .venv_quality_tests_annotation
VENV_PYTHON := $(VENV)/bin/python
REQUIREMENTS := annotation/requirements.txt

.PHONY: start-test-data-annotator setup-test-data-annotator clean-test-data-annotator

start-test-data-annotator: setup-test-data-annotator
	@echo "Starting test data annotator..."
	@cd annotation && ../$(VENV_PYTHON) test_data_annotator.py

# Builds the venv on a Python 3.12 that has tkinter. With uv, a uv-managed
# Python is used (it ships with Tk); otherwise falls back to system python3.12.
# A venv whose interpreter lacks tkinter is rebuilt.
setup-test-data-annotator:
	@echo "Setting up test data annotator environment..."
	@if [ -x "$(VENV_PYTHON)" ] && ! "$(VENV_PYTHON)" -c "import tkinter" 2>/dev/null; then \
		echo "Existing virtual environment has no tkinter, recreating it..."; \
		rm -rf "$(VENV)"; \
	fi
	@if command -v uv >/dev/null 2>&1; then \
		if [ ! -x "$(VENV_PYTHON)" ]; then \
			echo "Creating virtual environment with uv-managed Python 3.12..."; \
			uv venv --python 3.12 --managed-python --clear "$(VENV)" || exit 1; \
		fi; \
		uv pip install --quiet --python "$(VENV_PYTHON)" -r $(REQUIREMENTS) || exit 1; \
	else \
		python3.12 -c "import tkinter" 2>/dev/null || { \
			echo "❌ Error: tkinter is not available for Python 3.12"; \
			echo "Easiest fix: install uv (https://docs.astral.sh/uv/) and re-run this command."; \
			echo "Or install tkinter for your Python 3.12:"; \
			echo "  Ubuntu/Debian: sudo apt-get install python3.12-tk"; \
			echo "  RHEL/Fedora:   sudo dnf install python3-tkinter"; \
			echo "  Arch:          sudo pacman -S tk"; \
			echo "  MacOS:         brew install python-tk@3.12"; \
			exit 1; \
		}; \
		if [ ! -x "$(VENV_PYTHON)" ]; then \
			echo "Creating virtual environment for test data annotator..."; \
			python3.12 -m venv "$(VENV)" || exit 1; \
		fi; \
		"$(VENV_PYTHON)" -m pip install --quiet --upgrade pip && \
		"$(VENV_PYTHON)" -m pip install --quiet -r $(REQUIREMENTS) || exit 1; \
	fi
	@echo "✅ Environment ready (tkinter available)"

clean-test-data-annotator:
	rm -rf "$(VENV)"
