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


def test_optional_sources_are_off_by_default(config_mod, temp_home):
    # Only Jira is on out of the box; GitHub and PagerDuty must be opted into,
    # both in the runtime defaults and in the first-run template.
    cfg = config_mod.ensure_config()
    on_disk = json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8"))

    for source in ("github", "pagerduty"):
        assert config_mod.DEFAULT_CONFIG[source]["enabled"] is False
        assert on_disk[source]["enabled"] is False
        assert cfg[source]["enabled"] is False
    assert config_mod.DEFAULT_CONFIG["jira"]["enabled"] is True
    assert on_disk["jira"]["enabled"] is True


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


# ---------------------------------------------------------------------------
# Validation warnings (typos / wrong types are no longer silently ignored)
# ---------------------------------------------------------------------------

def test_validate_config_clean_config_has_no_problems(config_mod, sample_cfg):
    assert config_mod.validate_config(sample_cfg) == []
    assert config_mod.validate_config(config_mod.DEFAULT_CONFIG) == []
    # The first-run template (with its _note keys) is clean too.
    assert config_mod.validate_config(config_mod._TEMPLATE) == []


def test_validate_config_reports_typos_and_types(config_mod):
    cfg = {
        "jira": {"enabled": True, "supress_self": False,       # typo
                 "event_fields": "status"},                    # should be a list
        "github": {"enabled": "yes"},                          # should be bool
        "poll": {"interval_seconds": "300"},                   # should be number
        "theme": 3,                                            # should be text
        "pagerdooty": {},                                      # unknown section
        "update": "on",                                        # not an object
    }
    problems = config_mod.validate_config(cfg)
    assert "unknown setting 'jira.supress_self'" in problems
    assert "'jira.event_fields' should be a list [...]" in problems
    assert "'github.enabled' should be true or false" in problems
    assert "'poll.interval_seconds' should be a number" in problems
    assert "'theme' should be text in quotes" in problems
    assert "unknown setting 'pagerdooty'" in problems
    assert "'update' must be an object" in problems


def test_validate_config_accepts_floats_for_numbers_and_rejects_bools(config_mod):
    assert config_mod.validate_config({"poll": {"interval_seconds": 30.5}}) == []
    assert config_mod.validate_config({"poll": {"interval_seconds": True}}) == [
        "'poll.interval_seconds' should be a number"]


def test_validate_config_non_dict(config_mod):
    assert config_mod.validate_config(["not", "a", "dict"]) == [
        "config.json must contain a JSON object"]


def test_type_name_fallback(config_mod):
    # Defaults are only bool/int/str/list today; the fallback label is kept for
    # future value kinds and must not crash.
    assert config_mod._type_name(None) == "a value"
    assert config_mod._same_type("anything", None) is True


def test_ensure_config_exposes_problems_and_logs_once(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text(
        json.dumps({"jira": {"enabled": True, "supress_self": False}}),
        encoding="utf-8")
    config_mod.ensure_config()
    assert config_mod.last_problems() == ["unknown setting 'jira.supress_self'"]
    log = config_mod.LOG_FILE.read_text(encoding="utf-8")
    assert log.count("supress_self") == 1
    # Same problems on the next load -> not logged again (no log spam per poll).
    config_mod.ensure_config()
    log = config_mod.LOG_FILE.read_text(encoding="utf-8")
    assert log.count("supress_self") == 1


def test_ensure_config_corrupt_file_reports_problem(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text("{ nope", encoding="utf-8")
    config_mod.ensure_config()
    assert config_mod.last_problems()[0].startswith("config.json could not be parsed")


def test_ensure_config_non_object_file_falls_back_to_defaults(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text("[1, 2, 3]", encoding="utf-8")
    cfg = config_mod.ensure_config()
    assert cfg == config_mod.DEFAULT_CONFIG
    assert config_mod.last_problems() == ["config.json must contain a JSON object"]


def test_ensure_config_first_run_has_no_problems(config_mod):
    config_mod.ensure_config()
    assert config_mod.last_problems() == []


def test_ensure_config_returns_deep_copy_of_defaults(config_mod):
    # Mutating the returned dict (as the app does for theme / skipped version)
    # must never leak into DEFAULT_CONFIG.
    cfg = config_mod.ensure_config()  # first run -> defaults
    cfg["update"]["skipped_version"] = "9.9.9"
    cfg["jira"]["event_fields"].append("Sprint")
    assert config_mod.DEFAULT_CONFIG["update"]["skipped_version"] == ""
    assert config_mod.DEFAULT_CONFIG["jira"]["event_fields"] == ["status", "assignee"]


# ---------------------------------------------------------------------------
# save_config_patch (never clobber the user's file)
# ---------------------------------------------------------------------------

def test_save_config_patch_preserves_user_content(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text(json.dumps({
        "_readme": "keep me",
        "jira": {"enabled": True, "api_token": "real-secret", "_note": "n"},
        "update": {"enabled": True},
        "theme": "Green",
    }), encoding="utf-8")
    assert config_mod.save_config_patch({"theme": "Purple"}) is True
    assert config_mod.save_config_patch(
        {"update": {"skipped_version": "1.2.3"}}) is True
    on_disk = json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["theme"] == "Purple"
    assert on_disk["update"] == {"enabled": True, "skipped_version": "1.2.3"}
    # Everything else is untouched.
    assert on_disk["_readme"] == "keep me"
    assert on_disk["jira"] == {"enabled": True, "api_token": "real-secret",
                               "_note": "n"}


def test_save_config_patch_refuses_to_overwrite_corrupt_file(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    original = '{ "jira": { "api_token": "secret" '  # broken JSON
    config_mod.CONFIG_FILE.write_text(original, encoding="utf-8")
    assert config_mod.save_config_patch({"theme": "Purple"}) is False
    assert config_mod.CONFIG_FILE.read_text(encoding="utf-8") == original
    assert "not saving settings" in config_mod.LOG_FILE.read_text(encoding="utf-8")


def test_save_config_patch_refuses_non_object_file(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text("[]", encoding="utf-8")
    assert config_mod.save_config_patch({"theme": "Purple"}) is False
    assert config_mod.CONFIG_FILE.read_text(encoding="utf-8") == "[]"


def test_save_config_patch_creates_template_when_missing(config_mod):
    assert not config_mod.CONFIG_FILE.exists()
    assert config_mod.save_config_patch({"theme": "Yellow"}) is True
    on_disk = json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["theme"] == "Yellow"
    assert "_readme" in on_disk  # friendly template, not bare defaults


def test_save_config_patch_replaces_non_dict_section(config_mod):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_FILE.write_text(json.dumps({"update": "broken"}),
                                      encoding="utf-8")
    assert config_mod.save_config_patch({"update": {"skipped_version": "1"}})
    on_disk = json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["update"] == {"skipped_version": "1"}


def test_save_config_patch_swallows_oserror(config_mod, monkeypatch):
    from pathlib import Path

    def boom(self, *a, **k):
        raise OSError("read-only")

    config_mod.ensure_config()
    monkeypatch.setattr(Path, "write_text", boom)
    assert config_mod.save_config_patch({"theme": "Purple"}) is False


def test_write_json_is_atomic_and_private(config_mod, monkeypatch):
    config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_mod._write_json(config_mod.CONFIG_FILE, {"a": 1})
    assert json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8")) == {"a": 1}
    assert not config_mod.CONFIG_FILE.with_name("config.json.tmp").exists()
    import os
    import sys
    if sys.platform != "win32":
        assert oct(os.stat(config_mod.CONFIG_FILE).st_mode & 0o777) == "0o600"
    # chmod failures (exotic filesystems) are ignored.

    def boom(*a, **k):
        raise OSError("no chmod")

    monkeypatch.setattr(config_mod.os, "chmod", boom)
    config_mod._write_json(config_mod.CONFIG_FILE, {"a": 2})
    assert json.loads(config_mod.CONFIG_FILE.read_text(encoding="utf-8")) == {"a": 2}
