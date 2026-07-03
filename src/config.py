"""Configuration loading for the notifier.

All user-specific values (Jira URL, credentials, poll settings) live in a local
config file at ``~/.config/dev-notifier/config.json`` — never in the repo. A
template is written on first run so the user can fill in their details.

@author SteveZou
"""
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "dev-notifier"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"
LOG_FILE = CONFIG_DIR / "notifier.log"

DEFAULT_CONFIG = {
    "jira": {
        "enabled": True,
        "base_url": "https://your-domain.atlassian.net",
        "username": "you@example.com",
        "api_token": "",
        "_comment": "Create a token at https://id.atlassian.com/manage-profile/security/api-tokens",
    },
    "github": {
        "enabled": True,
        "login": "",
        "_comment": "Leave login blank to auto-detect via the gh CLI (gh api user).",
    },
    "pagerduty": {
        "enabled": False,
        "api_token": "",
        "user_id": "",
        "team_ids": [],
        "_comment": "User API token: My Profile > User Settings > API Access > "
                    "Create API User Token. Leave user_id / team_ids blank to "
                    "auto-detect the current user and their teams via /users/me.",
    },
    "poll": {
        "interval_seconds": 300,
        "window_minutes": 10,
        "max_window_minutes": 10080,
        "_comment": "window_minutes is the fallback lookback on first run (no "
                    "prior poll). Normally the window spans from the previous "
                    "poll to now, capped at max_window_minutes (default 7 days) "
                    "so a long sleep/shutdown doesn't fetch an unbounded backlog.",
    },
    "update": {
        "enabled": True,
        "check_interval_hours": 24,
        "skipped_version": "",
        "_comment": "Auto-check GitHub Releases for a newer version. skipped_version hides a version you dismissed.",
    },
    "theme": "Orange",
    "_theme_options": "Orange | Green | Purple | Rainbow | Yellow (also switchable from the menu)",
}


def ensure_config() -> dict:
    """Load config, creating a template on first run. Returns the parsed dict."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return dict(DEFAULT_CONFIG)
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def is_configured(cfg: dict) -> bool:
    """True when at least one source has usable credentials."""
    jira = cfg.get("jira", {})
    jira_ok = (
        jira.get("enabled")
        and jira.get("api_token")
        and "your-domain" not in jira.get("base_url", "")
    )
    github_ok = cfg.get("github", {}).get("enabled")
    pd = cfg.get("pagerduty", {})
    pagerduty_ok = pd.get("enabled") and pd.get("api_token")
    return bool(jira_ok or github_ok or pagerduty_ok)


def config_path() -> Path:
    return CONFIG_FILE
