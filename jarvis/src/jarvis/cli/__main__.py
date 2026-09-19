"""Executable entrypoint allowing 'python -m jarvis.cli' invocation."""

import sys

from jarvis.cli import main

if __name__ == "__main__":
    sys.exit(main())
