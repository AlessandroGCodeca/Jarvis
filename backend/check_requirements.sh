#!/usr/bin/env bash
#
# Run right after `git pull` (the `jarvis` alias calls this). If the pull
# changed backend/requirements.txt or frontend/package.json, install the new
# dependencies; otherwise just report that things are up to date.

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
if echo "$CHANGED" | grep -q 'backend/requirements.txt'; then
  echo "📦 New backend dependencies detected — installing..."
  (
    cd backend || exit 1
    if [ -d venv ]; then
      # shellcheck disable=SC1091
      source venv/bin/activate
    elif [ -d .venv ]; then
      # shellcheck disable=SC1091
      source .venv/bin/activate
    fi
    pip install -r requirements.txt -q
  ) && echo "✅ Backend dependencies up to date"
else
  echo "✅ Backend dependencies up to date"
fi

# --- Frontend npm deps ---
if echo "$CHANGED" | grep -q 'frontend/package.json'; then
  echo "📦 New frontend dependencies detected — installing..."
  (cd frontend && npm install --silent) && echo "✅ Frontend dependencies up to date"
else
  echo "✅ Frontend dependencies up to date"
fi
