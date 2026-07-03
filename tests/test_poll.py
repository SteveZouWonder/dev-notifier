"""Tests for src/poll.py — Jira/GitHub item collection.

Network (urllib) and the gh CLI (subprocess) are mocked so no real calls occur.

@author SteveZou
"""
import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def poll_mod():
    import poll as poll_mod
    importlib.reload(poll_mod)
    return poll_mod


def _recent_iso(minutes_ago=1):
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

def test_jira_items_disabled_returns_empty(poll_mod):
    assert poll_mod.jira_items({"jira": {"enabled": False}}, 10) == []


def test_jira_items_builds_item(poll_mod, monkeypatch, sample_cfg):
    issues = [{
        "key": "ACME-1",
        "fields": {
            "summary": "Fix the thing",
            "status": {"name": "In Progress"},
            "updated": _recent_iso(),
            "comment": {"comments": []},
        },
    }]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)

    items = poll_mod.jira_items(sample_cfg, 10)

    assert len(items) == 1
    it = items[0]
    assert it["subtitle"] == "ACME-1 · In Progress"
    assert it["message"] == "[updated] Fix the thing"
    assert it["url"] == "https://acme.atlassian.net/browse/ACME-1"
    assert it["fp"].startswith("jira:ACME-1:")


def test_jira_items_detects_comment_mention(poll_mod, monkeypatch, sample_cfg):
    # username is dev@acme.com -> handle "dev" must be matched in a recent comment.
    issues = [{
        "key": "ACME-2",
        "fields": {
            "summary": "Ping",
            "status": {"name": "Open"},
            "updated": _recent_iso(),
            "comment": {"comments": [
                {"created": _recent_iso(), "body": "hey dev please review"},
            ]},
        },
    }]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)

    items = poll_mod.jira_items(sample_cfg, 10)
    assert items[0]["message"] == "[comment mention] Ping"


def test_jira_items_skips_unparseable_comment_date(poll_mod, monkeypatch, sample_cfg):
    # A comment with an invalid `created` timestamp is skipped (ValueError path).
    issues = [{
        "key": "ACME-4",
        "fields": {
            "summary": "Bad date",
            "status": {"name": "Open"},
            "updated": _recent_iso(),
            "comment": {"comments": [
                {"created": "not-a-date", "body": "hey dev"},
            ]},
        },
    }]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)
    items = poll_mod.jira_items(sample_cfg, 10)
    # Unparseable comment -> not treated as a mention.
    assert items[0]["message"] == "[updated] Bad date"


def test_jira_items_mention_with_username_without_at(poll_mod, monkeypatch, sample_cfg):
    # username without '@' -> the whole lowercased username is the handle.
    sample_cfg["jira"]["username"] = "octodev"
    issues = [{
        "key": "ACME-5",
        "fields": {
            "summary": "Ping2",
            "status": {"name": "Open"},
            "updated": _recent_iso(),
            "comment": {"comments": [
                {"created": _recent_iso(), "body": "hey octodev review"},
            ]},
        },
    }]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)
    items = poll_mod.jira_items(sample_cfg, 10)
    assert items[0]["message"] == "[comment mention] Ping2"


def test_jira_items_ignores_old_comment(poll_mod, monkeypatch, sample_cfg):
    issues = [{
        "key": "ACME-3",
        "fields": {
            "summary": "Old",
            "status": {"name": "Open"},
            "updated": _recent_iso(),
            "comment": {"comments": [
                {"created": _recent_iso(minutes_ago=999), "body": "dev mentioned"},
            ]},
        },
    }]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)

    items = poll_mod.jira_items(sample_cfg, 10)
    assert items[0]["message"] == "[updated] Old"  # old comment not counted


def test_jira_search_returns_issues(poll_mod, monkeypatch, sample_cfg):
    payload = json.dumps({"issues": [{"key": "ACME-9"}]}).encode()

    class FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    issues = poll_mod._jira_search(sample_cfg, 10)
    assert issues == [{"key": "ACME-9"}]


def test_jira_search_missing_creds_returns_empty(poll_mod):
    cfg = {"jira": {"base_url": "", "username": "", "api_token": ""}}
    assert poll_mod._jira_search(cfg, 10) == []


def test_jira_search_swallows_network_error(poll_mod, monkeypatch, sample_cfg):
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._jira_search(sample_cfg, 10) == []


# ---------------------------------------------------------------------------
# GitHub notifications + helpers
# ---------------------------------------------------------------------------

def test_html_url_conversion(poll_mod):
    api = "https://api.github.com/repos/acme/app/pulls/42"
    assert poll_mod._html_url(api) == "https://github.com/acme/app/pull/42"


def test_html_url_empty(poll_mod):
    assert poll_mod._html_url("") == ""


def test_gh_notifications_disabled(poll_mod):
    assert poll_mod.gh_notifications({"github": {"enabled": False}}) == []


def test_gh_notifications_filters_irrelevant_reasons(poll_mod, monkeypatch, sample_cfg):
    notifs = [
        {"id": "1", "reason": "subscribed", "subject": {"title": "noise"},
         "repository": {"full_name": "acme/app"}, "updated_at": "t"},
        {"id": "2", "reason": "review_requested",
         "subject": {"title": "Please review",
                     "url": "https://api.github.com/repos/acme/app/pulls/7"},
         "repository": {"full_name": "acme/app"}, "updated_at": "t"},
    ]
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: notifs)

    items = poll_mod.gh_notifications(sample_cfg)
    assert len(items) == 1
    assert items[0]["subtitle"] == "acme/app · review requested"
    assert items[0]["url"] == "https://github.com/acme/app/pull/7"


def test_gh_json_parses_stdout(poll_mod, monkeypatch, fake_proc):
    monkeypatch.setattr(poll_mod.subprocess, "run",
                        lambda *a, **k: fake_proc(stdout='[{"x": 1}]'))
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod._gh_json(["api", "notifications"]) == [{"x": 1}]


def test_gh_json_handles_called_process_error(poll_mod, monkeypatch):
    import subprocess as sp

    def boom(*a, **k):
        raise sp.CalledProcessError(1, "gh", stderr="boom")

    monkeypatch.setattr(poll_mod.subprocess, "run", boom)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod._gh_json(["api", "notifications"]) == []


def test_gh_json_suppresses_no_checks_reported(poll_mod, monkeypatch, caplog):
    import subprocess as sp

    def boom(*a, **k):
        raise sp.CalledProcessError(1, "gh", stderr="no checks reported")

    logged = []
    monkeypatch.setattr(poll_mod, "_log", lambda m: logged.append(m))
    monkeypatch.setattr(poll_mod.subprocess, "run", boom)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod._gh_json(["pr", "checks"]) == []
    # The benign "no checks reported" case must not be logged as an error.
    assert logged == []


def test_gh_json_handles_timeout(poll_mod, monkeypatch):
    import subprocess as sp

    def boom(*a, **k):
        raise sp.TimeoutExpired("gh", 45)

    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.subprocess, "run", boom)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod._gh_json(["api", "notifications"]) == []


def test_gh_json_empty_stdout_returns_list(poll_mod, monkeypatch, fake_proc):
    monkeypatch.setattr(poll_mod.subprocess, "run",
                        lambda *a, **k: fake_proc(stdout="   "))
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod._gh_json(["api", "notifications"]) == []


def test_gh_notifications_uses_repo_url_when_no_subject_url(poll_mod, monkeypatch, sample_cfg):
    notifs = [{
        "id": "3", "reason": "mention",
        "subject": {"title": "no url here"},  # no subject url
        "repository": {"full_name": "acme/app"}, "updated_at": "t",
    }]
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: notifs)
    items = poll_mod.gh_notifications(sample_cfg)
    assert items[0]["url"] == "https://github.com/acme/app/pulls"


def test_gh_notifications_falls_back_to_notifications_url(poll_mod, monkeypatch, sample_cfg):
    notifs = [{
        "id": "4", "reason": "assign",
        "subject": {"title": "orphan"},  # no url, no repo
        "repository": {}, "updated_at": "t",
    }]
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: notifs)
    items = poll_mod.gh_notifications(sample_cfg)
    assert items[0]["url"] == "https://github.com/notifications"


def test_gh_login_autodetect_swallows_error(poll_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("gh missing")

    monkeypatch.setattr(poll_mod.subprocess, "run", boom)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod.gh_login({"github": {"login": ""}}) == ""


# ---------------------------------------------------------------------------
# CI rollup
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("buckets,expected", [
    (["pass", "fail"], "fail"),
    (["pass", "pending"], "pending"),
    (["pass", "pass"], "pass"),
])
def test_ci_rollup_for_pr(poll_mod, monkeypatch, buckets, expected):
    pr = {"url": "https://github.com/acme/app/pull/5", "title": "My PR"}
    checks = [{"bucket": b} for b in buckets]
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: checks)

    result = poll_mod._ci_rollup_for_pr(pr)
    assert result["ci_rollup"] == expected
    assert result["ci_only"] is True
    assert result["fp"] == f"gh-ci:acme/app#5:{expected}"


def test_ci_rollup_no_checks_returns_none(poll_mod, monkeypatch):
    pr = {"url": "https://github.com/acme/app/pull/5", "title": "My PR"}
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: [])
    assert poll_mod._ci_rollup_for_pr(pr) is None


def test_ci_rollup_bad_url_returns_none(poll_mod):
    assert poll_mod._ci_rollup_for_pr({"url": "not-a-pr-url"}) is None


# ---------------------------------------------------------------------------
# gh_login + collect_all
# ---------------------------------------------------------------------------

def test_gh_login_uses_configured_login(poll_mod, sample_cfg):
    assert poll_mod.gh_login(sample_cfg) == "octocat"


def test_gh_login_autodetects(poll_mod, monkeypatch, fake_proc):
    cfg = {"github": {"login": ""}}
    monkeypatch.setattr(poll_mod.subprocess, "run",
                        lambda *a, **k: fake_proc(stdout="autodetected\n"))
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod.gh_login(cfg) == "autodetected"


def test_collect_all_returns_phases(poll_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: ["j"])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg: ["g"])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: ["c"])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")

    phases = poll_mod.collect_all(sample_cfg)
    assert phases == [("jira", ["j"]), ("github", ["g"]), ("ci", ["c"])]


def test_collect_all_wires_custom_logger(poll_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: [])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")

    captured = []
    poll_mod.collect_all(sample_cfg, log=captured.append)
    # The passed-in logger replaces the module-level _log.
    poll_mod._log("hello")
    assert "hello" in captured


# ---------------------------------------------------------------------------
# gh_ci_fallback
# ---------------------------------------------------------------------------

def test_gh_ci_fallback_disabled_or_no_login(poll_mod):
    assert poll_mod.gh_ci_fallback({"github": {"enabled": True}}, "") == []
    assert poll_mod.gh_ci_fallback({"github": {"enabled": False}}, "octocat") == []


def test_gh_ci_fallback_no_prs(poll_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: [])
    assert poll_mod.gh_ci_fallback(sample_cfg, "octocat") == []


def test_gh_ci_fallback_collects_rollups(poll_mod, monkeypatch, sample_cfg):
    prs = [
        {"url": "https://github.com/acme/app/pull/1", "title": "A"},
        {"url": "https://github.com/acme/app/pull/2", "title": "B"},
    ]
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: prs)
    # _ci_rollup_for_pr is called via a thread pool; return a rollup for #1
    # and None for #2 to exercise the filtering.
    def fake_rollup(pr):
        if pr["url"].endswith("/1"):
            return {"fp": "gh-ci:acme/app#1:fail", "ci_only": True}
        return None

    monkeypatch.setattr(poll_mod, "_ci_rollup_for_pr", fake_rollup)
    items = poll_mod.gh_ci_fallback(sample_cfg, "octocat")
    assert len(items) == 1
    assert items[0]["fp"] == "gh-ci:acme/app#1:fail"


def test_default_log_prints(poll_mod, capsys):
    # The module-level default _log just prints; exercise it directly.
    import importlib
    importlib.reload(poll_mod)
    poll_mod._log("plain message")
    assert "plain message" in capsys.readouterr().out
