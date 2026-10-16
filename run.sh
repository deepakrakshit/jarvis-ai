#!/usr/bin/env bash
set -Eeuo pipefail

# Always launch from the repository root, even when called from another directory.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$SCRIPT_DIR"

# Keep the source tree importable without requiring a global package install.
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

# Prefer the repository virtual environment, then a sibling environment, then
# the first Python interpreter available on PATH.
PYTHON_CMD=""
for candidate in "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/../.venv/bin/python"; do
    if [[ -x "$candidate" ]]; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="$(command -v python)"
    else
        printf 'JARVIS requires Python 3.10 or newer, but no Python interpreter was found.\n' >&2
        exit 127
    fi
fi

printf '%s\n' '================================================================'
printf '%s\n' '            BOOTING JARVIS PERSONAL AI OPERATING SYSTEM'
printf '%s\n' '                          Version 1.0.1'
printf '%s\n\n' '================================================================'
printf '%s\n\n' 'Initializing Neural 3D Interface, Background Gateway, and Nodes...'

if [[ "$#" -eq 0 ]]; then
    exec "$PYTHON_CMD" -m jarvis.cli chat --live --with-daemon
else
    exec "$PYTHON_CMD" -m jarvis.cli "$@"
fi
