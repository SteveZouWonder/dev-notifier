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
    sample_cfg["jira"]["event_mode"] = False
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
    sample_cfg["jira"]["event_mode"] = False
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
    sample_cfg["jira"]["event_mode"] = False
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
    sample_cfg["jira"]["event_mode"] = False
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
    sample_cfg["jira"]["event_mode"] = False
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


# ---------------------------------------------------------------------------
# Jira — timestamp parsing (_parse_jira_dt)
# ---------------------------------------------------------------------------

def test_parse_jira_dt_offset_without_colon(poll_mod):
    dt = poll_mod._parse_jira_dt("2026-07-05T20:57:15.858-0400")
    assert dt is not None and dt.tzinfo is not None
    # -04:00 -> UTC is +4h -> 00:57 the next day.
    assert dt.year == 2026 and dt.month == 7 and dt.day == 6
    assert dt.hour == 0 and dt.minute == 57


def test_parse_jira_dt_offset_with_colon(poll_mod):
    dt = poll_mod._parse_jira_dt("2026-07-05T20:57:15.858-04:00")
    assert dt.hour == 0 and dt.day == 6


def test_parse_jira_dt_z_suffix(poll_mod):
    dt = poll_mod._parse_jira_dt("2026-07-05T20:57:15.858Z")
    assert dt.hour == 20 and dt.day == 5


def test_parse_jira_dt_naive_treated_as_utc(poll_mod):
    dt = poll_mod._parse_jira_dt("2026-07-05T20:57:15")
    assert dt.tzinfo is not None and dt.hour == 20


def test_parse_jira_dt_empty_and_garbage_return_none(poll_mod):
    assert poll_mod._parse_jira_dt("") is None
    assert poll_mod._parse_jira_dt(None) is None
    assert poll_mod._parse_jira_dt("not-a-date") is None


# ---------------------------------------------------------------------------
# Jira — event mode (changelog + comments)
# ---------------------------------------------------------------------------

def _issue_with_changelog(histories, comments=None, key="BLUE-1",
                          summary="Do the thing", status="Done", total=None):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "updated": _recent_iso(),
            "comment": {"comments": comments or []},
        },
        "changelog": {
            "total": total if total is not None else len(histories),
            "histories": histories,
        },
    }


def test_jira_items_event_mode_status_change(poll_mod, monkeypatch, sample_cfg):
    hist = [{
        "id": "5713733",
        "created": _recent_iso(),
        "author": {"displayName": "Ann Lin"},
        "items": [{"field": "status", "fromString": "In QA", "toString": "Done"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    assert len(items) == 1
    it = items[0]
    assert it["fp"] == "jira:BLUE-1:cl:5713733:status"
    assert it["subtitle"] == "BLUE-1 · Done"
    assert "[status]" in it["message"]
    assert "In QA → Done" in it["message"]
    assert "by Ann Lin" in it["message"]
    assert it["url"] == "https://acme.atlassian.net/browse/BLUE-1"


def test_jira_items_event_mode_whitelist_filters_noise(poll_mod, monkeypatch,
                                                       sample_cfg):
    # description / Attachment are not in the default whitelist -> dropped.
    hist = [{
        "id": "1",
        "created": _recent_iso(),
        "author": {"displayName": "Ann Lin"},
        "items": [
            {"field": "description", "fromString": None, "toString": "x"},
            {"field": "Attachment", "fromString": None, "toString": "a.png"},
            {"field": "assignee", "fromString": None, "toString": "Steve Zou"},
        ],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    assert len(items) == 1
    assert items[0]["fp"] == "jira:BLUE-1:cl:1:assignee"


def test_jira_items_event_mode_multiple_fields_one_history(poll_mod, monkeypatch,
                                                           sample_cfg):
    # Both whitelisted fields in one history -> two distinct events/fps.
    hist = [{
        "id": "7",
        "created": _recent_iso(),
        "author": {"displayName": "Ann Lin"},
        "items": [
            {"field": "status", "fromString": "A", "toString": "B"},
            {"field": "assignee", "fromString": None, "toString": "Steve"},
        ],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    fps = {it["fp"] for it in items}
    assert fps == {"jira:BLUE-1:cl:7:status", "jira:BLUE-1:cl:7:assignee"}


def test_jira_items_event_mode_out_of_window_history_dropped(poll_mod,
                                                             monkeypatch,
                                                             sample_cfg):
    hist = [{
        "id": "9",
        "created": _recent_iso(minutes_ago=999),
        "author": {"displayName": "Ann Lin"},
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_items_event_mode_unparseable_history_date_dropped(poll_mod,
                                                                monkeypatch,
                                                                sample_cfg):
    hist = [{
        "id": "9",
        "created": "garbage",
        "author": {"displayName": "Ann Lin"},
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_items_event_mode_comment_event(poll_mod, monkeypatch, sample_cfg):
    comments = [{
        "id": "807014",
        "created": _recent_iso(),
        "author": {"displayName": "Ann Lin"},
        "body": "looks good",
    }]
    issue = _issue_with_changelog([], comments=comments)
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: [issue])
    items = poll_mod.jira_items(sample_cfg, 10)
    assert len(items) == 1
    assert items[0]["fp"] == "jira:BLUE-1:comment:807014"
    assert "[comment]" in items[0]["message"]
    assert "commented" in items[0]["message"]


def test_jira_items_event_mode_comment_mention(poll_mod, monkeypatch, sample_cfg):
    # username dev@acme.com -> handle "dev" appears in the comment body.
    comments = [{
        "id": "1",
        "created": _recent_iso(),
        "author": {"displayName": "Ann Lin"},
        "body": "hey dev please look",
    }]
    issue = _issue_with_changelog([], comments=comments)
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: [issue])
    items = poll_mod.jira_items(sample_cfg, 10)
    assert "mentioned you" in items[0]["message"]


def test_jira_items_event_mode_old_comment_dropped(poll_mod, monkeypatch,
                                                   sample_cfg):
    comments = [{
        "id": "1",
        "created": _recent_iso(minutes_ago=999),
        "author": {"displayName": "Ann Lin"},
        "body": "old",
    }]
    issue = _issue_with_changelog([], comments=comments)
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: [issue])
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_items_event_mode_sorted_oldest_first(poll_mod, monkeypatch,
                                                   sample_cfg):
    hist = [
        {"id": "2", "created": _recent_iso(minutes_ago=1),
         "author": {"displayName": "A"},
         "items": [{"field": "status", "fromString": "A", "toString": "B"}]},
        {"id": "1", "created": _recent_iso(minutes_ago=5),
         "author": {"displayName": "A"},
         "items": [{"field": "status", "fromString": "X", "toString": "Y"}]},
    ]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    # Oldest (id=1, 5 min ago) first.
    assert [it["fp"] for it in items] == [
        "jira:BLUE-1:cl:1:status", "jira:BLUE-1:cl:2:status",
    ]


def test_jira_items_event_mode_fetches_full_changelog_when_truncated(
        poll_mod, monkeypatch, sample_cfg):
    # Inline changelog reports total > len(histories) -> full log is fetched.
    issue = _issue_with_changelog([], total=5)  # 0 inline, total 5 -> truncated
    full = [{
        "id": "42",
        "created": _recent_iso(),
        "author": {"displayName": "Ann Lin"},
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: [issue])
    monkeypatch.setattr(poll_mod, "_jira_changelog", lambda cfg, key: full)
    items = poll_mod.jira_items(sample_cfg, 10)
    assert items[0]["fp"] == "jira:BLUE-1:cl:42:status"


def test_jira_items_event_mode_format_change_empty_sides(poll_mod, monkeypatch,
                                                         sample_cfg):
    hist = [{
        "id": "1",
        "created": _recent_iso(),
        "author": {"displayName": "A"},
        "items": [{"field": "status", "fromString": None, "toString": None}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    assert "∅ → ∅" in items[0]["message"]


def test_jira_items_event_mode_no_author(poll_mod, monkeypatch, sample_cfg):
    hist = [{
        "id": "1",
        "created": _recent_iso(),
        "author": {},
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    # No author -> no " by ..." suffix.
    assert "by" not in items[0]["message"].split("—")[0]


def test_jira_changelog_fetches_values(poll_mod, monkeypatch, sample_cfg):
    payload = json.dumps({"values": [{"id": "1"}]}).encode()

    class Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen",
                        lambda *a, **k: Resp())
    assert poll_mod._jira_changelog(sample_cfg, "BLUE-1") == [{"id": "1"}]


def test_jira_changelog_missing_creds_returns_empty(poll_mod):
    assert poll_mod._jira_changelog({"jira": {}}, "BLUE-1") == []


def test_jira_changelog_swallows_network_error(poll_mod, monkeypatch, sample_cfg):
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._jira_changelog(sample_cfg, "BLUE-1") == []


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


def test_ssl_context_uses_certifi_bundle(poll_mod, monkeypatch):
    import ssl as _ssl

    captured = {}

    def fake_create(*a, **k):
        captured["cafile"] = k.get("cafile")
        return "CTX"

    monkeypatch.setattr(poll_mod.ssl, "create_default_context", fake_create)
    # certifi is available in the test env; the bundle path should be passed.
    assert poll_mod._ssl_context() == "CTX"
    assert captured["cafile"] and captured["cafile"].endswith("cacert.pem")


def test_ssl_context_falls_back_without_certifi(poll_mod, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_certifi(name, *a, **k):
        if name == "certifi":
            raise ImportError("no certifi")
        return real_import(name, *a, **k)

    calls = []

    def fake_create(*a, **k):
        calls.append(k)
        return "DEFAULT_CTX"

    monkeypatch.setattr(builtins, "__import__", no_certifi)
    monkeypatch.setattr(poll_mod.ssl, "create_default_context", fake_create)
    assert poll_mod._ssl_context() == "DEFAULT_CTX"
    # Fallback path: called with no cafile kwarg.
    assert calls == [{}]


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
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w: ["p"])

    phases = poll_mod.collect_all(sample_cfg)
    assert phases == [("jira", ["j"]), ("github", ["g"]), ("ci", ["c"]),
                      ("pagerduty", ["p"])]


def test_collect_all_wires_custom_logger(poll_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: [])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w: [])

    captured = []
    poll_mod.collect_all(sample_cfg, log=captured.append)
    # The passed-in logger replaces the module-level _log.
    poll_mod._log("hello")
    assert "hello" in captured


def test_collect_all_passes_dynamic_window(poll_mod, monkeypatch, sample_cfg):
    # since_ts is turned into a lookback window and handed to the sources.
    seen = {}
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: seen.setdefault("jira", w) or [])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w: seen.setdefault("pd", w) or [])

    import time
    poll_mod.collect_all(sample_cfg, since_ts=time.time() - 25 * 60)
    # ~25 minutes since the last poll -> both sources get the same window.
    assert seen["jira"] == 25
    assert seen["pd"] == 25


# ---------------------------------------------------------------------------
# resolve_window_minutes (dynamic poll window)
# ---------------------------------------------------------------------------

def test_resolve_window_falls_back_without_since(poll_mod):
    cfg = {"poll": {"window_minutes": 10, "max_window_minutes": 10080}}
    assert poll_mod.resolve_window_minutes(cfg, since_ts=None) == 10


def test_resolve_window_spans_last_poll_to_now(poll_mod):
    cfg = {"poll": {"window_minutes": 10, "max_window_minutes": 10080}}
    now = 1_000_000.0
    # 42 minutes ago.
    assert poll_mod.resolve_window_minutes(
        cfg, since_ts=now - 42 * 60, now_ts=now) == 42


def test_resolve_window_capped_at_max(poll_mod):
    cfg = {"poll": {"window_minutes": 10, "max_window_minutes": 60}}
    now = 1_000_000.0
    # 5 hours ago, but capped at 60 minutes.
    assert poll_mod.resolve_window_minutes(
        cfg, since_ts=now - 5 * 3600, now_ts=now) == 60


def test_resolve_window_floor_of_one_minute(poll_mod):
    cfg = {"poll": {"window_minutes": 10, "max_window_minutes": 10080}}
    now = 1_000_000.0
    # A poll just seconds ago (or slight clock skew) still looks back >= 1 min.
    assert poll_mod.resolve_window_minutes(
        cfg, since_ts=now - 5, now_ts=now) == 1


def test_resolve_window_uses_defaults_when_poll_cfg_absent(poll_mod):
    # No poll section at all: defaults (1440 fallback = 24h, 10080 cap) apply.
    assert poll_mod.resolve_window_minutes({}, since_ts=None) == 1440


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


# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------

def _pd_incident(iid="PINC1", num=42, status="triggered",
                 changed="2026-07-01T00:00:00Z", title="Disk full",
                 service="prod-api", html_url="https://acme.pagerduty.com/incidents/PINC1"):
    return {
        "id": iid,
        "incident_number": num,
        "status": status,
        "last_status_change_at": changed,
        "service": {"summary": service},
        "title": title,
        "html_url": html_url,
    }


def test_pagerduty_items_disabled_returns_empty(poll_mod):
    assert poll_mod.pagerduty_items({"pagerduty": {"enabled": False}}, 10) == []


def test_pagerduty_items_no_token_returns_empty(poll_mod):
    cfg = {"pagerduty": {"enabled": True, "api_token": ""}}
    assert poll_mod.pagerduty_items(cfg, 10) == []


def test_pagerduty_items_builds_assigned_and_team(poll_mod, monkeypatch, sample_cfg):
    calls = []

    def fake_get(token, path, params=None):
        calls.append(params)
        # First call = assigned-to-me (user_ids[]); second = team.
        if any(k == "user_ids[]" for k, _ in (params or [])):
            return {"incidents": [_pd_incident(iid="PINC1", num=1,
                                               status="triggered")]}
        return {"incidents": [
            _pd_incident(iid="PINC1", num=1, status="triggered"),  # dup, skipped
            _pd_incident(iid="PINC2", num=2, status="acknowledged",
                         title="CPU high"),
        ]}

    monkeypatch.setattr(poll_mod, "_pd_get", fake_get)
    items = poll_mod.pagerduty_items(sample_cfg, 10)

    assert len(items) == 2  # PINC1 assigned + PINC2 team (PINC1 dup skipped)
    assigned = items[0]
    assert assigned["title"] == "PagerDuty"
    assert assigned["subtitle"] == "#1 · triggered · prod-api"
    assert assigned["message"] == "[assigned to you] Disk full"
    assert assigned["fp"] == "pd:PINC1:2026-07-01T00:00:00Z"
    assert assigned["url"] == "https://acme.pagerduty.com/incidents/PINC1"
    assert items[1]["message"] == "[team incident] CPU high"


def test_pagerduty_items_no_service_summary(poll_mod, monkeypatch, sample_cfg):
    inc = _pd_incident(service="")
    monkeypatch.setattr(poll_mod, "_pd_get",
                        lambda t, p, params=None: {"incidents": [inc]}
                        if any(k == "user_ids[]" for k, _ in (params or []))
                        else {"incidents": []})
    items = poll_mod.pagerduty_items(sample_cfg, 10)
    assert items[0]["subtitle"] == "#42 · triggered"  # no trailing service


def test_pagerduty_items_only_teams_when_no_user_id(poll_mod, monkeypatch):
    cfg = {"pagerduty": {"enabled": True, "api_token": "tok",
                         "user_id": "", "team_ids": ["PTEAM1"]}}
    seen = {"user": False}

    def fake_get(token, path, params=None):
        if any(k == "user_ids[]" for k, _ in (params or [])):
            seen["user"] = True
            return {"incidents": []}
        return {"incidents": [_pd_incident(iid="PT1", num=9,
                                           status="triggered")]}

    monkeypatch.setattr(poll_mod, "_pd_get", fake_get)
    items = poll_mod.pagerduty_items(cfg, 10)
    # user_id empty -> no assigned query issued; only the team item remains.
    assert seen["user"] is False
    assert len(items) == 1
    assert items[0]["message"].startswith("[team incident]")


def test_pd_identity_uses_configured_values(poll_mod, sample_cfg):
    # Both user_id and team_ids present -> no network call.
    assert poll_mod.pd_identity(sample_cfg) == ("PUSER1", ["PTEAM1"])


def test_pd_identity_no_token_returns_configured(poll_mod):
    cfg = {"pagerduty": {"enabled": True, "api_token": "",
                         "user_id": "", "team_ids": []}}
    assert poll_mod.pd_identity(cfg) == ("", [])


def test_pd_identity_autodetects_via_users_me(poll_mod, monkeypatch):
    cfg = {"pagerduty": {"enabled": True, "api_token": "tok",
                         "user_id": "", "team_ids": []}}
    monkeypatch.setattr(poll_mod, "_pd_get", lambda t, p, params=None: {
        "user": {"id": "PME", "teams": [{"id": "PTX"}, {"id": ""}]}})
    assert poll_mod.pd_identity(cfg) == ("PME", ["PTX"])


def test_pd_get_parses_json(poll_mod, monkeypatch):
    payload = json.dumps({"user": {"id": "PME"}}).encode()

    class FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp())
    assert poll_mod._pd_get("tok", "/users/me") == {"user": {"id": "PME"}}


def test_pd_get_with_params_builds_query(poll_mod, monkeypatch):
    captured = {}

    class FakeResp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, *a, **k):
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", fake_urlopen)
    poll_mod._pd_get("tok", "/incidents", [("user_ids[]", "P1"), ("limit", "5")])
    assert "user_ids%5B%5D=P1" in captured["url"]
    assert captured["url"].startswith("https://api.pagerduty.com/incidents?")


def test_pd_get_swallows_network_error(poll_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("pd down")

    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._pd_get("tok", "/users/me") == {}


def test_default_log_prints(poll_mod, capsys):
    # The module-level default _log just prints; exercise it directly.
    import importlib
    importlib.reload(poll_mod)
    poll_mod._log("plain message")
    assert "plain message" in capsys.readouterr().out
