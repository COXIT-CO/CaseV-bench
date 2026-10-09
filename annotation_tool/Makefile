VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
PORT ?= 8765

.PHONY: start stop test start-test-data-annotator setup clean

# Serves the tool on http://localhost:$(PORT) and opens it in the browser.
# With uv, PyMuPDF comes from uv's cache and no virtualenv is created; otherwise a local
# .venv is set up once with any python3 >= 3.9.
start:
	@if command -v uv >/dev/null 2>&1; then \
		uv run --quiet --no-project --with-requirements requirements.txt python serve.py --port $(PORT); \
	else \
		$(MAKE) --no-print-directory setup && "$(VENV_PYTHON)" serve.py --port $(PORT); \
	fi

# Stops the server listening on $(PORT), whether it runs in a terminal or in the background.
stop:
	@pids=$$(lsof -ti tcp:$(PORT) -sTCP:LISTEN); \
	if [ -n "$$pids" ]; then kill $$pids && echo "Stopped annotation tool on port $(PORT)."; \
	else echo "Nothing is listening on port $(PORT)."; fi

# Server tests (pytest) and UI logic tests (Node's built-in runner, Node >= 22). Same runner
# choice as `start`: uv when present, otherwise the local .venv.
test:
	@if command -v uv >/dev/null 2>&1; then \
		uv run --quiet --no-project --with-requirements requirements.txt --with pytest pytest tests -q; \
	else \
		$(MAKE) --no-print-directory setup && "$(VENV_PYTHON)" -m pip install --quiet --disable-pip-version-check pytest \
			&& "$(VENV_PYTHON)" -m pytest tests -q; \
	fi
	node --test 'tests/*.test.mjs'

# Old name, kept so existing docs and habits still work.
start-test-data-annotator: start

setup:
	@if [ ! -x "$(VENV_PYTHON)" ]; then python3 -m venv "$(VENV)" || exit 1; fi
	@"$(VENV_PYTHON)" -m pip install --quiet --disable-pip-version-check -r requirements.txt

clean:
	rm -rf "$(VENV)"
