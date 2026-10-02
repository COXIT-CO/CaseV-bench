VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
PORT ?= 8765

.PHONY: start start-test-data-annotator setup clean

# Serves the tool on http://localhost:$(PORT) and opens it in the browser.
# With uv, PyMuPDF comes from uv's cache and no virtualenv is created; otherwise a local
# .venv is set up once with any python3 >= 3.9.
start:
	@if command -v uv >/dev/null 2>&1; then \
		uv run --quiet --no-project --with-requirements requirements.txt python serve.py --port $(PORT); \
	else \
		$(MAKE) --no-print-directory setup && "$(VENV_PYTHON)" serve.py --port $(PORT); \
	fi

# Old name, kept so existing docs and habits still work.
start-test-data-annotator: start

setup:
	@if [ ! -x "$(VENV_PYTHON)" ]; then python3 -m venv "$(VENV)" || exit 1; fi
	@"$(VENV_PYTHON)" -m pip install --quiet --disable-pip-version-check -r requirements.txt

clean:
	rm -rf "$(VENV)"
