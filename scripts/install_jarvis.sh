#!/usr/bin/env bash
#
# One-shot installer for the `jarvis` launcher (macOS). Safe to re-run: it
# backs up ~/.zshrc, removes any old `alias jarvis=...` lines and any copy of
# the launcher that was pasted straight into ~/.zshrc (which breaks zsh with
# "defining function based on alias"), then wires up ~/.jarvis_alias.
#
# Usage:  bash scripts/install_jarvis.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZSHRC="$HOME/.zshrc"

[ -f "$ZSHRC" ] || touch "$ZSHRC"
cp "$ZSHRC" "$ZSHRC.jarvis-backup"
echo "🗂  Backed up $ZSHRC to $ZSHRC.jarvis-backup"

# Old-style alias definitions shadow the function and break it at parse time.
sed -i '' '/^alias jarvis=/d' "$ZSHRC"

# A launcher that was appended directly into .zshrc spans from its header
# comment down to the function's closing brace.
sed -i '' '/^# JARVIS launcher/,/^}/d' "$ZSHRC"

cp "$ROOT/scripts/jarvis_alias.sh" "$HOME/.jarvis_alias"

if ! grep -q 'source ~/.jarvis_alias' "$ZSHRC"; then
  printf '\nsource ~/.jarvis_alias\n' >> "$ZSHRC"
fi

echo "✅ Installed."
echo "   Open a new terminal and run:  jarvis"
echo "   (or in this one:  unalias jarvis 2>/dev/null; source ~/.zshrc)"
