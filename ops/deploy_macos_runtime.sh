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
"$RUNTIME_ROOT/venv/bin/python" -m pip install "${PROJECT_ROOT}[data,ops]"

install -m 644 "$PROJECT_ROOT/data/cache/sector_probe/universe_us.csv" \
  "$RUNTIME_ROOT/config/universe_us.csv"
install -m 644 "$PROJECT_ROOT/data/cache/sector_probe/universe_cn.csv" \
  "$RUNTIME_ROOT/config/universe_cn.csv"

"$RUNTIME_ROOT/venv/bin/quant-workbench" ops-init --root "$RUNTIME_ROOT/data"

# Record what was deployed so health/latest.json can expose build_sha / deployed_at.
BUILD_SHA="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null)" ]]; then
  BUILD_SHA="${BUILD_SHA}-dirty"
fi
printf '{"build_sha": "%s", "deployed_at": "%s"}\n' "$BUILD_SHA" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUNTIME_ROOT/build-info.json"

# Telegram credentials live only in the runtime; never copied from or into the repo.
if [[ ! -f "$RUNTIME_ROOT/config/alerts.env" ]]; then
  echo "note: $RUNTIME_ROOT/config/alerts.env not found; alerts stay disabled (see docs/ALERTING.md)"
fi

for label in pipeline watchdog backup; do
  install -m 644 "$PROJECT_ROOT/ops/launchd/com.quantworkbench.$label.plist" \
    "$AGENT_ROOT/com.quantworkbench.$label.plist"
  launchctl bootstrap "$DOMAIN" "$AGENT_ROOT/com.quantworkbench.$label.plist"
done
