#!/bin/zsh
set -euo pipefail

DOMAIN="gui/501"
for label in pipeline watchdog backup reports fundamentals; do
  launchctl bootout "$DOMAIN/com.quantworkbench.$label" 2>/dev/null || true
done
