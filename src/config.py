"""Configuration loading for the notifier.

All user-specific values (Jira URL, credentials, poll settings) live in a local
config file — never in the repo:

- macOS:   ~/.config/dev-notifier/config.json
- Windows: %APPDATA%/dev-notifier/config.json

On first run a **simple** template with just the common settings is written so
non-technical users only see what they need to fill in. Every advanced option
still has a built-in default (read via ``.get(...)`` throughout the code), so
leaving it out of the file changes nothing about how the app runs — power users
can add any advanced key by hand.

@author SteveZou
"""
import json
from pathlib import Path

import paths as _paths

CONFIG_DIR = _paths.config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"
LOG_FILE = CONFIG_DIR / "notifier.log"

# Full runtime defaults. This is the source of truth the app falls back to (and
# what a corrupt/missing file resolves to). It intentionally includes every
# advanced option; the on-disk *template* below is a trimmed subset.
DEFAULT_CONFIG = {
    "jira": {
        "enabled": True,
        "base_url": "https://your-domain.atlassian.net",
        "username": "you@example.com",
        "api_token": "",
        "event_mode": True,
        "event_fields": ["status", "assignee"],
    },
    "github": {
        "enabled": True,
        "login": "",
    },
    "pagerduty": {
        "enabled": False,
        "api_token": "",
        "user_id": "",
        "team_ids": [],
    },
    "poll": {
        "interval_seconds": 300,
        "window_minutes": 1440,
        "max_window_minutes": 10080,
    },
    "update": {
        "enabled": True,
        "check_interval_hours": 24,
        "skipped_version": "",
    },
    "theme": "Orange",
}

# Simple first-run template: only the settings a typical user fills in, with
# short plain-language notes (``_note`` keys are ignored by the app). Advanced
# options are omitted on purpose — the app supplies their defaults at runtime.
_TEMPLATE = {
    "_readme": "Fill in the fields below, save this file, then click "
               "'Check dependencies' in the app menu. Only Jira needs details "
               "here; GitHub uses the 'gh' command-line tool (run 'gh auth "
               "login' once). See the TUTORIAL for step-by-step help.",
    "jira": {
        "enabled": True,
        "base_url": "https://your-domain.atlassian.net",
        "username": "you@example.com",
        "api_token": "",
        "_note": "Get an API token at "
                 "https://id.atlassian.com/manage-profile/security/api-tokens. "
                 "Set enabled to false to turn Jira off.",
    },
    "github": {
        "enabled": True,
        "login": "",
        "_note": "No token needed — uses the 'gh' CLI. Leave login blank to "
                 "auto-detect. Set enabled to false if you don't use GitHub.",
    },
    "pagerduty": {
        "enabled": False,
        "api_token": "",
        "_note": "Optional. Set enabled to true and paste a PagerDuty User API "
                 "token to get incident notifications.",
    },
    "theme": "Orange",
    "_theme_options": "Orange | Green | Purple | Rainbow | Yellow "
                      "(also switchable from the menu).",
}


def _log_problem(msg: str) -> None:
    """Best-effort note to the log file; never raises."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def ensure_config() -> dict:
    """Load config, writing the simple template on first run.

    On a corrupt file the app must still start, so runtime defaults are
    returned — but the user's file is **left untouched** (not overwritten) and a
    note is logged, so a typo can be fixed without losing their edits.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(_TEMPLATE, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return dict(DEFAULT_CONFIG)
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log_problem(
            f"WARN: could not read config ({e}); using defaults for this run. "
            f"Your file was left unchanged at {CONFIG_FILE} — fix the error and "
            f"restart. (Common cause: a missing comma or quote in the JSON.)"
        )
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
