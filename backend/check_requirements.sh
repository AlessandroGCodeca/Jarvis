#!/usr/bin/env bash
#
# Run right after `git pull` (the `jarvis` alias calls this). If the pull
# changed backend/requirements.txt, backend/requirements-dev.txt or
# frontend/package.json, install the new dependencies; otherwise just report
# that things are up to date.

set -uo pipefail

# Resolve the repo root from this script's location (backend/..).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# What did the most recent pull change? (HEAD@{1} is the pre-pull commit.)
if git rev-parse 'HEAD@{1}' >/dev/null 2>&1; then
  CHANGED="$(git diff --name-only 'HEAD@{1}' HEAD 2>/dev/null || true)"
else
  CHANGED=""
fi

# --- Backend Python deps ---
# `venv` is the documented name (setup.sh, the README, the jarvis alias);
# `.venv` is tolerated for older checkouts.
VENV_PY=""
for d in backend/venv backend/.venv; do
  if [ -x "$d/bin/python" ]; then
    VENV_PY="$ROOT/$d/bin/python"
    break
  fi
done

# -x matches a whole line: git diff --name-only prints one path per line, and
# an unanchored pattern would also fire on e.g. backend/requirements.txt.bak.
runtime_changed=false
dev_changed=false
echo "$CHANGED" | grep -qx 'backend/requirements\.txt' && runtime_changed=true
echo "$CHANGED" | grep -qx 'backend/requirements-dev\.txt' && dev_changed=true

# requirements-dev.txt begins with `-r requirements.txt`, so installing it
# covers both files. Dev tooling is only refreshed for someone who already has
# it — a runtime-only setup shouldn't sprout pytest because of a git pull.
has_pytest=false
if [ -n "$VENV_PY" ] && "$VENV_PY" -c "import pytest" >/dev/null 2>&1; then
  has_pytest=true
fi

REQ=""
if [ "$has_pytest" = true ] && { [ "$runtime_changed" = true ] || [ "$dev_changed" = true ]; }; then
  REQ="requirements-dev.txt"
elif [ "$runtime_changed" = true ]; then
  REQ="requirements.txt"
fi

if [ -n "$REQ" ]; then
  echo "📦 New backend dependencies detected — installing from $REQ..."
  if [ -n "$VENV_PY" ]; then
    "$VENV_PY" -m pip install -r "$ROOT/backend/$REQ" -q
  else
    (cd backend && pip install -r "$REQ" -q)
  fi && echo "✅ Backend dependencies up to date"
else
  echo "✅ Backend dependencies up to date"
fi

# --- Frontend npm deps ---
if echo "$CHANGED" | grep -qx 'frontend/package\.json'; then
  echo "📦 New frontend dependencies detected — installing..."
  (cd frontend && npm install --silent) && echo "✅ Frontend dependencies up to date"
else
  echo "✅ Frontend dependencies up to date"
fi
