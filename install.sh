#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/bin"
ln -sfn "$HERE/swman" "$HOME/.local/bin/swman"
printf 'Installed command: %s\n' "$HOME/.local/bin/swman"
printf 'Run: swman\n'
