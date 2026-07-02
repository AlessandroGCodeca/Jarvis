#!/usr/bin/env bash
# (Re)create backend/venv on a well-supported Python, precompile bytecode, and
# warm up the imports — so the first `uvicorn main:app` binds in seconds
# instead of sitting silent for minutes on a cold import.
#
# Usage:  cd backend && ./setup.sh
# Pick a specific interpreter with:  PYTHON=python3.11 ./setup.sh
set -euo pipefail

cd "$(dirname "$0")"

# Prefer 3.12 (the most battle-tested wheel target), then 3.13 / 3.11.
# Bleeding-edge Pythons work too, but ecosystem wheels and tooling lag them.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in python3.12 python3.13 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "error: no python3.12 / 3.13 / 3.11 on PATH." >&2
    echo "Install one (e.g. 'brew install python@3.12') or set PYTHON=..." >&2
    exit 1
fi
echo "Using $("$PYTHON" --version) at $(command -v "$PYTHON")"

rm -rf venv
"$PYTHON" -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# Compile every .py to .pyc now so the first boot doesn't pay that cost.
echo "Precompiling bytecode..."
venv/bin/python -m compileall -q -j 0 venv/lib . || true

# One throwaway import of the app. This absorbs the remaining one-time cost
# (on macOS, Gatekeeper's first-run scan of freshly installed files) here,
# where you can see it, instead of inside a silent-looking uvicorn start.
echo "Warming up imports (the first run after an install can take a while)..."
time PYTHONPATH="$PWD" venv/bin/python -c "import main; print('imports ok')"

echo
echo "Setup complete. Start the backend with:"
echo "  cd backend && venv/bin/uvicorn main:app --port 8000"
