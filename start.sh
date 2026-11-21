#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or later is required")'
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
exec .venv/bin/python start.py "$@"
