#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${QW_PROJECT_ROOT:-/Users/lucky/Documents/ChatGPT/Quant}"
RUNTIME_ROOT="/Users/lucky/Library/Application Support/QuantWorkbench"
AGENT_ROOT="/Users/lucky/Library/LaunchAgents"
DOMAIN="gui/501"

for label in pipeline watchdog backup reports fundamentals events; do
  launchctl bootout "$DOMAIN/com.quantworkbench.$label" 2>/dev/null || true
done

mkdir -p "$RUNTIME_ROOT/config" "$AGENT_ROOT"
/opt/homebrew/bin/python3.14 -m venv "$RUNTIME_ROOT/venv"
"$RUNTIME_ROOT/venv/bin/python" -m pip install "${PROJECT_ROOT}[data,ops]"
# The version number rarely changes, so pip would otherwise keep the stale copy.
"$RUNTIME_ROOT/venv/bin/python" -m pip install --no-deps --force-reinstall --no-cache-dir \
  "${PROJECT_ROOT}"

# Refuse to deploy if the installed package differs from the source tree.
"$RUNTIME_ROOT/venv/bin/python" - "$PROJECT_ROOT/src/quant_workbench" <<'PY'
import hashlib, pathlib, sys
import quant_workbench
source = pathlib.Path(sys.argv[1])
installed = pathlib.Path(quant_workbench.__file__).parent
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
drift = [
    str(path.relative_to(source))
    for path in sorted(source.rglob("*.py"))
    if not (installed / path.relative_to(source)).is_file()
    or digest(path) != digest(installed / path.relative_to(source))
]
if drift:
    sys.exit("deploy aborted: installed package differs from source: " + ", ".join(drift[:10]))
print(f"installed package matches source ({installed})")
PY

install -m 644 "$PROJECT_ROOT/data/cache/sector_probe/universe_us.csv" \
  "$RUNTIME_ROOT/config/universe_us.csv"
install -m 644 "$PROJECT_ROOT/data/cache/sector_probe/universe_cn.csv" \
  "$RUNTIME_ROOT/config/universe_cn.csv"

# Daily committee prompts embed and hash every project skill.  Keep a runtime copy
# because LaunchAgent deliberately runs outside the Documents checkout.
mkdir -p "$RUNTIME_ROOT/skills"
rsync -a --delete "$PROJECT_ROOT/.claude/skills/" "$RUNTIME_ROOT/skills/"

"$RUNTIME_ROOT/venv/bin/quant-workbench" ops-init --root "$RUNTIME_ROOT/data"

# Record what was deployed so health/latest.json can expose build_sha / deployed_at.
BUILD_SHA="${QW_BUILD_SHA:-$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)}"
if [[ -z "${QW_BUILD_SHA:-}" && -n "$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null)" ]]; then
  BUILD_SHA="${BUILD_SHA}-dirty"
fi
printf '{"build_sha": "%s", "deployed_at": "%s"}\n' "$BUILD_SHA" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUNTIME_ROOT/build-info.json"

# Telegram credentials live only in the runtime; never copied from or into the repo.
if [[ ! -f "$RUNTIME_ROOT/config/alerts.env" ]]; then
  echo "note: $RUNTIME_ROOT/config/alerts.env not found; alerts stay disabled (see docs/ALERTING.md)"
fi

# GPT provider selection contains no credential.  Codex itself owns and refreshes its
# ChatGPT login; this file only opts the report job into that already-authorized CLI.
if [[ ! -f "$RUNTIME_ROOT/config/openai.env" ]]; then
  install -m 600 "$PROJECT_ROOT/config/openai.env.example" \
    "$RUNTIME_ROOT/config/openai.env"
fi

for label in pipeline watchdog backup reports fundamentals events; do
  install -m 644 "$PROJECT_ROOT/ops/launchd/com.quantworkbench.$label.plist" \
    "$AGENT_ROOT/com.quantworkbench.$label.plist"
  launchctl bootstrap "$DOMAIN" "$AGENT_ROOT/com.quantworkbench.$label.plist"
done
