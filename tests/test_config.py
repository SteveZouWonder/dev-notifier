"""Tests for src/config.py — template creation, load fallbacks, and the
``is_configured`` source-readiness logic.

@author SteveZou
"""
import importlib
import json

import pytest


@pytest.fixture
def config_mod(temp_home):
    """Import config fresh so its module-level paths use the temp home."""
    import config as config_mod
    importlib.reload(config_mod)
    return config_mod


def test_ensure_config_writes_template_on_first_run(config_mod, temp_home):
    # No config file exists yet -> the simple template is written and the full
    # runtime defaults are returned.
    assert not config_mod.CONFIG_FILE.exists()

    cfg = config_mod.ensure_config()

    assert config_mod.CONFIG_FILE.exists()
    on_disk = json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["jira"]["base_url"] == config_mod.DEFAULT_CONFIG["jira"]["base_url"]
    # Returned config carries full runtime defaults (theme etc.).
    assert cfg["theme"] == "Orange"


def test_first_run_template_is_simple(config_mod, temp_home):
    # The on-disk template must NOT expose advanced tuning knobs, so a
    # non-technical user only sees the fields they need to fill in.
    config_mod.ensure_config()
    on_disk = json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8"))

    # Common fields are present with plain-language notes.
    assert on_disk["jira"]["api_token"] == ""
    assert "_note" in on_disk["jira"]
    assert "_readme" in on_disk

    # Advanced fields are omitted from the template (defaults come from code).
    assert "event_mode" not in on_disk["jira"]
    assert "event_fields" not in on_disk["jira"]
    assert "poll" not in on_disk
    assert "update" not in on_disk

    # But the app still knows their defaults at runtime.
    assert config_mod.DEFAULT_CONFIG["jira"]["event_mode"] is True
    assert config_mod.DEFAULT_CONFIG["poll"]["interval_seconds"] == 300


def test_ensure_config_reads_existing(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text(
        json.dumps({"theme": "Green", "jira": {"enabled": False}}),
        encoding="utf-8",
    )

    cfg = config_mod.ensure_config()

    assert cfg["theme"] == "Green"
    assert cfg["jira"]["enabled"] is False


def test_ensure_config_falls_back_on_corrupt_json(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text("{ this is not json", encoding="utf-8")

    cfg = config_mod.ensure_config()

    # Corrupt file -> defaults returned (a copy, not the same object).
    assert cfg == config_mod.DEFAULT_CONFIG
    assert cfg is not config_mod.DEFAULT_CONFIG


def test_corrupt_config_is_not_overwritten_and_logs(config_mod):
    # A user's typo must not be silently discarded: the file is left untouched
    # and a note is written to the log so they can fix it.
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    original = '{ "jira": { "api_token": "secret" '  # missing braces
    config_mod.CONFIG_FILE.write_text(original, encoding="utf-8")

    config_mod.ensure_config()

    # File preserved exactly (not replaced with a template).
    assert config_mod.CONFIG_FILE.read_text(encoding="utf-8") == original
    # A helpful note was logged.
    log = config_mod.LOG_FILE.read_text(encoding="utf-8")
    assert "could not read config" in log
    assert "left unchanged" in log


def test_log_problem_swallows_oserror(config_mod, monkeypatch):
    # Logging must never raise even if the log file can't be written.
    from pathlib import Path

    def boom(self, *a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "open", boom)
    config_mod._log_problem("anything")  # no exception


def test_is_configured_true_when_jira_ready(config_mod, sample_cfg):
    sample_cfg["github"]["enabled"] = False
    assert config_mod.is_configured(sample_cfg) is True


def test_is_configured_false_with_placeholder_jira_and_no_github(config_mod):
    cfg = {
        "jira": {
            "enabled": True,
            "base_url": "https://your-domain.atlassian.net",
            "api_token": "tok",
        },
        "github": {"enabled": False},
    }
    assert config_mod.is_configured(cfg) is False


def test_is_configured_true_when_only_github_enabled(config_mod):
    cfg = {"jira": {"enabled": False}, "github": {"enabled": True}}
    assert config_mod.is_configured(cfg) is True


def test_is_configured_true_when_only_pagerduty_ready(config_mod):
    cfg = {"jira": {"enabled": False}, "github": {"enabled": False},
           "pagerduty": {"enabled": True, "api_token": "tok"}}
    assert config_mod.is_configured(cfg) is True


def test_is_configured_false_when_nothing_usable(config_mod):
    cfg = {
        "jira": {"enabled": True, "base_url": "https://your-domain.atlassian.net",
                 "api_token": ""},
        "github": {"enabled": False},
    }
    assert config_mod.is_configured(cfg) is False


def test_config_path_returns_config_file(config_mod):
    assert config_mod.config_path() == config_mod.CONFIG_FILE
