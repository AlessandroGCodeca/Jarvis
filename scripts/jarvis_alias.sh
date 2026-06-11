# JARVIS launcher — auto-updates, installs new deps, starts backend + frontend,
# then opens Chrome.
#
# Install (once):
#   cp scripts/jarvis_alias.sh ~/.jarvis_alias
#   echo 'source ~/.jarvis_alias' >> ~/.zshrc
#   source ~/.zshrc
# Then just run:  jarvis

jarvis() {
  cd ~/Desktop/Jarvis || { echo "✗ ~/Desktop/Jarvis not found"; return 1; }

  echo "🔄 Checking for updates..."
  local before after
  before="$(git rev-parse HEAD 2>/dev/null)"
  git pull origin main --quiet
  after="$(git rev-parse HEAD 2>/dev/null)"
  if [ "$before" != "$after" ]; then
    echo "✓ JARVIS updated"
  else
    echo "✓ JARVIS up to date"
  fi

  # Install any new backend/frontend dependencies the pull introduced.
  bash backend/check_requirements.sh

  # Backend terminal: venv + uvicorn (0.0.0.0 so the iPhone companion can reach it).
  osascript -e 'tell application "Terminal" to do script "cd ~/Desktop/Jarvis/backend && source venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8000"'

  # Frontend terminal: Vite dev server.
  osascript -e 'tell application "Terminal" to do script "cd ~/Desktop/Jarvis/frontend && npm run dev"'

  # Give the servers a moment, then open the UI.
  sleep 4
  open -a "Google Chrome" "http://localhost:5173"
}
