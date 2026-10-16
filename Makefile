SHELL := /bin/sh

PROJECT := jarvis-os
VENV := .venv
HOST_PYTHON ?= python3

ifeq ($(OS),Windows_NT)
VENV_PYTHON := $(VENV)/Scripts/python.exe
else
VENV_PYTHON := $(VENV)/bin/python
endif

PYTHON ?= $(VENV_PYTHON)
NODE ?= node
NPM ?= npm
DOCKER ?= docker

JS_FILES := \
    frontend/js/app.js \
    frontend/js/config.js \
    frontend/js/audio/player.js \
    frontend/js/audio/recorder.js \
    frontend/js/components/particle-field.js \
    frontend/js/components/signal-orb.js \
    frontend/js/components/speaking-orb.js \
    frontend/js/gateway/client.js

.PHONY: help install test test-all lint format-check typecheck js-check run docker-build docker-run clean

help:
	@printf '%s\n' \
		'install       Create the local environment from the committed lockfiles' \
		'test          Run the offline, headless regression suite' \
		'test-all      Run every test, including browser/audio suites' \
		'lint          Run Ruff lint checks' \
		'format-check  Verify Ruff formatting' \
		'typecheck     Run strict mypy checks' \
		'js-check      Check frontend JavaScript syntax' \
		'run           Start JARVIS through the repository launcher' \
		'docker-build  Build the reproducible runtime image' \
		'docker-run    Run the gateway in the runtime image'

install:
	$(HOST_PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --requirement requirements.lock
	$(VENV_PYTHON) -m pip install --no-deps --no-build-isolation .
	$(NPM) --prefix substrate ci --ignore-scripts

# This target intentionally excludes tests that require a downloaded browser
# or PortAudio hardware. It is deterministic and works without network access
# after install has completed. Those suites remain available through test-all.
test:
	$(PYTHON) -m pytest -m "not audio and not browser"

test-all:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests

format-check:
	$(PYTHON) -m ruff format --check src tests

typecheck:
	$(PYTHON) -m mypy src tests

js-check:
	@for file in $(JS_FILES); do \
		$(NODE) --check "$$file" || exit $$?; \
	done

run:
	./run.sh

docker-build:
	$(DOCKER) build --tag $(PROJECT):local .

docker-run:
	$(DOCKER) run --rm --init --env-file .env -p 8765:8765 $(PROJECT):local

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist src/*.egg-info
