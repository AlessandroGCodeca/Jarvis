#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Installs backend (Python) and frontend (Node) dependencies so the linter,
# type-checker, and build all work inside a fresh web session — the same checks
# CI runs on every PR (ruff + py_compile, tsc + vite build).
#
# Runs synchronously: the session waits until dependencies are ready, which
# avoids race conditions where Claude tries to lint/build before install is done.
set -euo pipefail

# Only needed in the remote (Claude Code on the web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# --- Backend: virtualenv + dependencies + ruff (linter) ---
# A venv keeps installs isolated and is cached with the container. Idempotent:
# re-running just reuses/updates the existing venv.
python3 -m venv "$ROOT/backend/.venv"
"$ROOT/backend/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT/backend/.venv/bin/python" -m pip install --quiet -r "$ROOT/backend/requirements.txt" ruff

# --- Frontend: node modules (npm install is cache-friendly + idempotent) ---
( cd "$ROOT/frontend" && npm install --no-audit --no-fund ) >/dev/null

# Put the backend venv on PATH for the rest of the session so `python`, `ruff`,
# and `uvicorn` resolve to the project's environment.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$ROOT/backend/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

echo "JARVIS session-start hook complete: backend venv + frontend deps ready."
