start-test-data-annotator:
	@echo "Setting up test data annotator environment..."
	@echo "Checking for tkinter availability..."
	@python3.12 -c "import tkinter" 2>/dev/null || { \
		echo "❌ Error: tkinter is not available for Python 3.12"; \
		echo "To fix this issue, install tkinter with:"; \
		echo "  Ubuntu/Debian: sudo apt-get install python3.12-tk"; \
		echo "  RHEL/Fedora:   sudo dnf install python3-tkinter"; \
		echo "  Arch:          sudo pacman -S tk"; \
		echo "  MacOS:         brew install python-tk@3.12"; \
		exit 1; \
	}
	@echo "✅ tkinter is available"
	@if [ ! -d ".venv_quality_tests_annotation" ]; then \
		echo "Creating virtual environment for test data annotator..."; \
		python3.12 -m venv .venv_quality_tests_annotation; \
	fi
	@echo "Activating virtual environment and installing requirements..."
	@. .venv_quality_tests_annotation/bin/activate && \
		pip install --upgrade pip && \
		pip install -r annotation/requirements.txt
	@echo "Starting test data annotator..."
	@. .venv_quality_tests_annotation/bin/activate && \
		cd annotation && \
		python test_data_annotator.py
