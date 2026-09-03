#!/usr/bin/env bash
# Dev Notifier — dependency doctor (macOS).
# Checks the things the app needs and prints fix hints. Safe to run anytime.
# Windows users: the in-app Status ▸ submenu shows the same information.
set -uo pipefail

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }
info() { printf "    \033[2m%s\033[0m\n" "$1"; }

echo "Dev Notifier — dependency check"
echo "==============================="

# Installed app + Gatekeeper quarantine (the #1 first-launch problem)
APP="/Applications/DevNotifier.app"
if [[ -d "$APP" ]]; then
  ok "app installed ($APP)"
  if xattr -p com.apple.quarantine "$APP" >/dev/null 2>&1; then
    bad "app is quarantined — macOS will say it \"is damaged\""
    info "run: xattr -dr com.apple.quarantine $APP"
  else
    ok "app is not quarantined"
  fi
  arch=$(lipo -archs "$APP/Contents/MacOS/DevNotifier" 2>/dev/null || echo "?")
  host=$(uname -m)
  if [[ "$arch" != *"$host"* && "$arch" != "?" ]]; then
    bad "app is built for $arch but this Mac is $host"
    info "the prebuilt DMG is Apple Silicon only; build from source on Intel"
  fi
else
  info "app not found in /Applications (running from source or elsewhere?)"
fi

# Python (only relevant for running from source)
if command -v python3 >/dev/null 2>&1; then
  ok "python3 present ($(python3 --version 2>&1)) — only needed to run from source"
else
  info "python3 not found — fine unless you run from source"
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
  bad "gh CLI not installed (only needed if GitHub is enabled)"
  info "run: brew install gh && gh auth login"
fi

# Config file
CONFIG="$HOME/.config/dev-notifier/config.json"
if [[ -f "$CONFIG" ]]; then
  ok "config file exists ($CONFIG)"
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$CONFIG" 2>/dev/null; then
    bad "config file is not valid JSON (missing comma or quote?)"
    info "the app keeps running on defaults and shows 'Config: ⚠' in Status ▸"
  fi
  if grep -q "your-domain" "$CONFIG" 2>/dev/null; then
    bad "Jira base_url still a placeholder"
    info "edit $CONFIG and set base_url / username / api_token"
  fi
  # Look at the Jira section only (PagerDuty's api_token is legitimately empty
  # while PagerDuty is disabled).
  if python3 - "$CONFIG" <<'PY' 2>/dev/null
import json, sys
cfg = json.load(open(sys.argv[1]))
jira = cfg.get("jira", {})
sys.exit(0 if jira.get("enabled", True) and not jira.get("api_token") else 1)
PY
  then
    bad "Jira is enabled but api_token is empty"
    info "create one at https://id.atlassian.com/manage-profile/security/api-tokens"
  fi
else
  info "config not created yet — it appears on first app launch ($CONFIG)"
fi

# Login item
PLIST="$HOME/Library/LaunchAgents/ai.stevezou.devnotifier.plist"
if [[ -f "$PLIST" ]]; then
  target=$(plutil -extract ProgramArguments.1 raw -o - "$PLIST" 2>/dev/null || echo "")
  if [[ -n "$target" && ! -e "$target" ]]; then
    bad "start-at-login points at a missing path: $target"
    info "launch the app once from /Applications (it repairs the entry) or toggle Start at login off/on"
  else
    ok "start-at-login enabled"
  fi
else
  info "start-at-login disabled (toggle it from the app menu)"
fi

# Log
LOG="$HOME/.config/dev-notifier/notifier.log"
if [[ -f "$LOG" ]]; then
  errs=$(grep -c "ERROR" "$LOG" 2>/dev/null || echo 0)
  if [[ "$errs" -gt 0 ]]; then
    info "log has $errs ERROR line(s); last one:"
    info "$(grep "ERROR" "$LOG" | tail -1 | cut -c1-160)"
  fi
fi

echo
echo "Done."
