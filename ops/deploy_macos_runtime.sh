#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Users/lucky/Documents/ChatGPT/Quant"
RUNTIME_ROOT="/Users/lucky/Library/Application Support/QuantWorkbench"
AGENT_ROOT="/Users/lucky/Library/LaunchAgents"
DOMAIN="gui/501"

for label in pipeline watchdog backup; do
  launchctl bootout "$DOMAIN/com.quantworkbench.$label" 2>/dev/null || true
done

mkdir -p "$RUNTIME_ROOT/config" "$AGENT_ROOT"
/opt/homebrew/bin/python3.14 -m venv "$RUNTIME_ROOT/venv"
"$RUNTIME_ROOT/venv/bin/python" -m pip install "$PROJECT_ROOT[data,ops]"

install -m 644 "$PROJECT_ROOT/data/cache/sector_probe/universe_us.csv" \
  "$RUNTIME_ROOT/config/universe_us.csv"
install -m 644 "$PROJECT_ROOT/data/cache/sector_probe/universe_cn.csv" \
  "$RUNTIME_ROOT/config/universe_cn.csv"

"$RUNTIME_ROOT/venv/bin/quant-workbench" ops-init --root "$RUNTIME_ROOT/data"

for label in pipeline watchdog backup; do
  install -m 644 "$PROJECT_ROOT/ops/launchd/com.quantworkbench.$label.plist" \
    "$AGENT_ROOT/com.quantworkbench.$label.plist"
  launchctl bootstrap "$DOMAIN" "$AGENT_ROOT/com.quantworkbench.$label.plist"
done
