#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")" && pwd)"
launcher_source="$repo_root/bin/codex-switch"
launcher_target="$HOME/.local/bin/codex-switch"

mkdir -p "$HOME/.local/bin"
chmod +x "$launcher_source"
ln -sf "$launcher_source" "$launcher_target"

echo "Installed codex-switch to $launcher_target"
echo "Run: codex-switch --help"
