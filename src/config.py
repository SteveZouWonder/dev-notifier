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
    "poll": {
        "interval_seconds": 300,
        "window_minutes": 10,
        "max_auto_open": 3,
    },
    "theme": "Orange",
    "_theme_options": "Orange | Green | Purple | Rainbow | Bell (also switchable from the menu)",
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
    return bool(jira_ok or github_ok)


def config_path() -> Path:
    return CONFIG_FILE
