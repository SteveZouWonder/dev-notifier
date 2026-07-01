#!/usr/bin/env bash
# Dev Notifier — dependency doctor.
# Checks the things the app needs and prints fix hints. Safe to run anytime.
set -uo pipefail

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }
info() { printf "    \033[2m%s\033[0m\n" "$1"; }

echo "Dev Notifier — dependency check"
echo "==============================="

# Python (only relevant for running from source)
if command -v python3 >/dev/null 2>&1; then
  ok "python3 present ($(python3 --version 2>&1))"
else
  bad "python3 not found"
fi

# gh CLI
if command -v gh >/dev/null 2>&1; then
  ok "gh CLI installed"
  if gh auth status >/dev/null 2>&1; then
    login=$(gh api user --jq .login 2>/dev/null || echo "?")
    ok "gh authenticated as ${login}"
  else
    bad "gh not logged in"
    info "run: gh auth login"
  fi
else
  bad "gh CLI not installed"
  info "run: brew install gh && gh auth login"
fi

# Config file
CONFIG="$HOME/.config/dev-notifier/config.json"
if [[ -f "$CONFIG" ]]; then
  ok "config file exists ($CONFIG)"
  if grep -q "your-domain" "$CONFIG" 2>/dev/null; then
    bad "Jira base_url still a placeholder"
    info "edit $CONFIG and set base_url / username / api_token"
  fi
  if grep -Eq '"api_token"\s*:\s*""' "$CONFIG" 2>/dev/null; then
    bad "Jira api_token is empty"
    info "create one at https://id.atlassian.com/manage-profile/security/api-tokens"
  fi
else
  info "config not created yet — it appears on first app launch ($CONFIG)"
fi

# Login item
PLIST="$HOME/Library/LaunchAgents/ai.stevezou.devnotifier.plist"
if [[ -f "$PLIST" ]]; then
  ok "start-at-login enabled"
else
  info "start-at-login disabled (toggle it from the app menu)"
fi

echo
echo "Done."
