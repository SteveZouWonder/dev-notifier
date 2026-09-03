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
import copy
import json
import os
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
        "suppress_self": True,
        "suppress_automation": True,
    },
    "github": {
        "enabled": True,
        "login": "",
        "suppress_self": True,
        "ci_notify": ["fail"],
        "emails": [],
    },
    "pagerduty": {
        "enabled": False,
        "api_token": "",
        "user_id": "",
        "team_ids": [],
        "suppress_self": True,
        "notify_team_incidents": True,
        "low_urgency_sound": False,
        "oncall_reminders": True,
        "oncall_remind_before_minutes": [1440, 60],
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
                 "Set enabled to false to turn Jira off. Add "
                 "\"suppress_self\": false to also be notified of your own "
                 "changes/comments (on by default = your own actions are "
                 "hidden).",
    },
    "github": {
        "enabled": True,
        "login": "",
        "_note": "No token needed — uses the 'gh' CLI. Leave login blank to "
                 "auto-detect. Set enabled to false if you don't use GitHub. "
                 "Add \"suppress_self\": false to also be notified of activity "
                 "you triggered yourself (on by default = hidden).",
    },
    "pagerduty": {
        "enabled": False,
        "api_token": "",
        "_note": "Optional. Set enabled to true and paste a PagerDuty User API "
                 "token to get incident + on-call shift notifications. Add "
                 "\"suppress_self\": false to also be notified of actions you "
                 "took yourself (on by default = hidden); "
                 "\"notify_team_incidents\": false to only hear about "
                 "incidents assigned to you; \"oncall_reminders\": false to "
                 "turn off shift reminders. See the TUTORIAL for more.",
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


# Problems found by the most recent ``ensure_config()`` call (parse errors,
# unknown keys, wrong value types). The app shows the first one in its Status
# menu so a typo in config.json is visible instead of silently ignored.
_last_problems = []


def last_problems() -> list:
    """Human-readable config problems from the last ``ensure_config()``."""
    return list(_last_problems)


def _write_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` atomically (temp file + ``os.replace``).

    The file holds API tokens, so it is created user-readable only (0600) on
    POSIX; ``chmod`` failures (e.g. exotic filesystems) are ignored.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def validate_config(cfg: dict) -> list:
    """Return a list of human-readable problems with ``cfg`` (empty = fine).

    Checks each known section for unknown keys (typos such as
    ``supress_self``) and for values whose type differs from the default
    (e.g. ``"interval_seconds": "300"``). Keys starting with ``_`` are template
    notes and ignored. Unknown *sections* are reported too. This never rejects
    the config — it only produces warnings for the UI/log.
    """
    problems = []
    if not isinstance(cfg, dict):
        return ["config.json must contain a JSON object"]
    for section, value in cfg.items():
        if section.startswith("_"):
            continue
        default = DEFAULT_CONFIG.get(section)
        if default is None:
            problems.append(f"unknown setting '{section}'")
            continue
        if isinstance(default, dict):
            if not isinstance(value, dict):
                problems.append(f"'{section}' must be an object")
                continue
            for key, val in value.items():
                if key.startswith("_"):
                    continue
                if key not in default:
                    problems.append(f"unknown setting '{section}.{key}'")
                elif not _same_type(val, default[key]):
                    problems.append(
                        f"'{section}.{key}' should be "
                        f"{_type_name(default[key])}")
        elif not _same_type(value, default):
            problems.append(f"'{section}' should be {_type_name(default)}")
    return problems


def _same_type(val, default) -> bool:
    if isinstance(default, bool):
        return isinstance(val, bool)
    if isinstance(default, int):
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if isinstance(default, str):
        return isinstance(val, str)
    if isinstance(default, list):
        return isinstance(val, list)
    return True


def _type_name(default) -> str:
    if isinstance(default, bool):
        return "true or false"
    if isinstance(default, int):
        return "a number"
    if isinstance(default, str):
        return "text in quotes"
    if isinstance(default, list):
        return "a list [...]"
    return "a value"


def ensure_config() -> dict:
    """Load config, writing the simple template on first run.

    On a corrupt file the app must still start, so runtime defaults are
    returned — but the user's file is **left untouched** (not overwritten) and a
    note is logged, so a typo can be fixed without losing their edits. The
    problem is also exposed via ``last_problems()`` for the UI.

    The returned dict is always a *deep* copy: callers mutate it (theme
    switch, skipped version) and must never leak into ``DEFAULT_CONFIG``.
    """
    global _last_problems
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        _write_json(CONFIG_FILE, _TEMPLATE)
        _last_problems = []
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log_problem(
            f"WARN: could not read config ({e}); using defaults for this run. "
            f"Your file was left unchanged at {CONFIG_FILE} — fix the error and "
            f"restart. (Common cause: a missing comma or quote in the JSON.)"
        )
        _last_problems = [f"config.json could not be parsed ({e})"]
        return copy.deepcopy(DEFAULT_CONFIG)
    problems = validate_config(cfg)
    if not isinstance(cfg, dict):
        _last_problems = problems
        return copy.deepcopy(DEFAULT_CONFIG)
    if problems != _last_problems:
        for p in problems:
            _log_problem(f"WARN: config: {p}")
    _last_problems = problems
    return cfg


def save_config_patch(patch: dict) -> bool:
    """Merge ``patch`` into the on-disk config file and write it atomically.

    Only the keys in ``patch`` change; everything else the user wrote (their
    credentials, comments-as-``_note`` keys, advanced options) is preserved
    exactly. Nested dicts are merged one level deep (``{"update":
    {"skipped_version": "1.2.3"}}`` touches just that key).

    Returns ``False`` without writing when the file cannot be parsed — the
    in-memory config is then only the defaults and writing it back would
    silently replace the user's real settings with placeholders (the bug this
    replaces). Also ``False`` on I/O errors.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                current = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                _log_problem(f"WARN: not saving settings: config.json could not "
                             f"be parsed ({e}); fix the file first.")
                return False
            if not isinstance(current, dict):
                _log_problem("WARN: not saving settings: config.json is not a "
                             "JSON object.")
                return False
        else:
            current = copy.deepcopy(_TEMPLATE)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key].update(value)
            else:
                current[key] = value
        _write_json(CONFIG_FILE, current)
        return True
    except OSError as e:
        _log_problem(f"ERROR writing config: {e}")
        return False


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
