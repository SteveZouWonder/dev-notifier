"""Tests for src/poll.py — Jira/GitHub/PagerDuty item collection.

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


@pytest.fixture(autouse=True)
def _no_jira_myself(request, poll_mod, monkeypatch):
    """Keep tests hermetic: stub the /myself lookup (no real network).

    Individual self-suppression tests override this with a concrete accountId.
    Tests marked ``real_myself`` exercise ``_jira_myself`` directly and opt out.
    """
    if request.node.get_closest_marker("real_myself"):
        return
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "")


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


# ---------------------------------------------------------------------------
# Jira — self-suppression (suppress_self)
# ---------------------------------------------------------------------------

def test_jira_items_suppresses_own_changelog(poll_mod, monkeypatch, sample_cfg):
    # A status change authored by the current user (accountId ME) is dropped.
    hist = [{
        "id": "1", "created": _recent_iso(),
        "author": {"displayName": "Me", "accountId": "ME"},
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "ME")
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_items_keeps_others_changelog(poll_mod, monkeypatch, sample_cfg):
    # Same shape but a different author -> still notified.
    hist = [{
        "id": "1", "created": _recent_iso(),
        "author": {"displayName": "Ann", "accountId": "OTHER"},
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "ME")
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    assert len(items) == 1


def test_jira_items_suppresses_own_comment(poll_mod, monkeypatch, sample_cfg):
    comments = [{
        "id": "1", "created": _recent_iso(),
        "author": {"displayName": "Me", "accountId": "ME"},
        "body": "self note",
    }]
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "ME")
    issue = _issue_with_changelog([], comments=comments)
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: [issue])
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_items_suppress_self_disabled_keeps_own(poll_mod, monkeypatch,
                                                     sample_cfg):
    # With suppress_self off, _jira_myself is never called and own changes show.
    sample_cfg["jira"]["suppress_self"] = False

    def _boom(cfg):
        raise AssertionError("_jira_myself must not be called when disabled")

    monkeypatch.setattr(poll_mod, "_jira_myself", _boom)
    hist = [{
        "id": "1", "created": _recent_iso(),
        "author": {"displayName": "Me", "accountId": "ME"},
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    assert len(poll_mod.jira_items(sample_cfg, 10)) == 1


@pytest.mark.real_myself
def test_jira_myself_returns_account_id(poll_mod, monkeypatch, sample_cfg):
    payload = json.dumps({"accountId": "ABC123"}).encode()

    class Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen",
                        lambda *a, **k: Resp())
    assert poll_mod._jira_myself(sample_cfg) == "ABC123"


@pytest.mark.real_myself
def test_jira_myself_missing_creds_returns_empty(poll_mod):
    assert poll_mod._jira_myself({"jira": {}}) == ""


@pytest.mark.real_myself
def test_jira_myself_swallows_network_error(poll_mod, monkeypatch, sample_cfg):
    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._jira_myself(sample_cfg) == ""
    assert poll_mod._jira_myself_cache == {}  # failures are not cached


def _myself_resp(payload):
    class Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return Resp()


@pytest.mark.real_myself
def test_jira_myself_is_cached_and_survives_failures(poll_mod, monkeypatch,
                                                     sample_cfg):
    """One ``/myself`` timeout used to switch self-suppression off for the whole
    poll (52 such polls in one user's log). The id is now cached and, when a
    refresh fails, the last known value is reused."""
    calls = []

    def ok(*a, **k):
        calls.append("ok")
        return _myself_resp({"accountId": "ABC123"})

    def boom(*a, **k):
        calls.append("boom")
        raise OSError("timeout")

    logged = []
    monkeypatch.setattr(poll_mod, "_log", logged.append)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", ok)
    assert poll_mod._jira_myself(sample_cfg) == "ABC123"
    assert poll_mod._jira_myself(sample_cfg) == "ABC123"
    assert calls == ["ok"]  # second call served from the cache
    # Expire the cache; the refresh fails -> stale value reused, WARN logged.
    key, (_ts, val) = next(iter(poll_mod._jira_myself_cache.items()))
    poll_mod._jira_myself_cache[key] = (0, val)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._jira_myself(sample_cfg) == "ABC123"
    assert calls == ["ok", "boom"]
    assert any(m.startswith("WARN jira_myself") for m in logged)


@pytest.mark.real_myself
def test_jira_myself_empty_account_id_not_cached(poll_mod, monkeypatch,
                                                sample_cfg):
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen",
                        lambda *a, **k: _myself_resp({}))
    assert poll_mod._jira_myself(sample_cfg) == ""
    assert poll_mod._jira_myself_cache == {}


# ---------------------------------------------------------------------------
# Jira — automation suppression (suppress_automation)
# ---------------------------------------------------------------------------

_AUTOMATION = {"displayName": "Automation for Jira", "accountId": "APP1",
               "accountType": "app"}


def test_jira_items_suppresses_automation_changes(poll_mod, monkeypatch,
                                                  sample_cfg):
    # "Automation for Jira" moved the ticket (typically right after *you*
    # opened a PR) -> dropped by default.
    hist = [{
        "id": "1", "created": _recent_iso(), "author": _AUTOMATION,
        "items": [{"field": "status", "fromString": "To Do",
                   "toString": "Code Review"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_items_keeps_automation_assigning_to_you(poll_mod, monkeypatch,
                                                      sample_cfg):
    # ... unless the rule hands the issue to you: that is worth hearing about.
    hist = [{
        "id": "1", "created": _recent_iso(), "author": _AUTOMATION,
        "items": [{"field": "assignee", "from": None, "to": "ME",
                   "fromString": None, "toString": "Me"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "ME")
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    items = poll_mod.jira_items(sample_cfg, 10)
    assert len(items) == 1
    assert "by Automation for Jira" in items[0]["message"]


def test_jira_items_automation_assigning_someone_else_suppressed(poll_mod,
                                                                 monkeypatch,
                                                                 sample_cfg):
    hist = [{
        "id": "1", "created": _recent_iso(), "author": _AUTOMATION,
        "items": [{"field": "assignee", "to": "OTHER", "toString": "Ann"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "ME")
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_items_suppress_automation_disabled(poll_mod, monkeypatch,
                                                 sample_cfg):
    sample_cfg["jira"]["suppress_automation"] = False
    hist = [{
        "id": "1", "created": _recent_iso(), "author": _AUTOMATION,
        "items": [{"field": "status", "fromString": "A", "toString": "B"}],
    }]
    monkeypatch.setattr(poll_mod, "_jira_search",
                        lambda cfg, w: [_issue_with_changelog(hist)])
    assert len(poll_mod.jira_items(sample_cfg, 10)) == 1


def test_is_jira_app_author(poll_mod):
    assert poll_mod._is_jira_app_author({}) is False
    assert poll_mod._is_jira_app_author(None) is False
    assert poll_mod._is_jira_app_author({"accountType": "atlassian",
                                         "displayName": "Ann"}) is False
    assert poll_mod._is_jira_app_author({"accountType": "app"}) is True
    # Fallback when accountType is missing from the payload.
    assert poll_mod._is_jira_app_author(
        {"displayName": "Automation for Jira"}) is True


def test_history_targets_self(poll_mod):
    h = {"items": [{"field": "assignee", "to": "ME"}]}
    assert poll_mod._history_targets_self(h, "ME") is True
    assert poll_mod._history_targets_self(h, "") is False
    assert poll_mod._history_targets_self(
        {"items": [{"field": "status", "to": "ME"}]}, "ME") is False


# ---------------------------------------------------------------------------
# Jira — legacy (issue-level) mode honours suppress_self
# ---------------------------------------------------------------------------

def _legacy_issue(histories=(), comments=(), key="ACME-1"):
    return {
        "key": key,
        "fields": {
            "summary": "Fix the thing",
            "status": {"name": "In Progress"},
            "updated": _recent_iso(),
            "comment": {"comments": list(comments)},
        },
        "changelog": {"histories": list(histories),
                      "total": len(histories)},
    }


def _hist(account_id, minutes_ago=1):
    return {"id": "1", "created": _recent_iso(minutes_ago),
            "author": {"accountId": account_id, "displayName": account_id},
            "items": [{"field": "status", "fromString": "A", "toString": "B"}]}


def _comment(account_id, minutes_ago=1):
    return {"id": "1", "created": _recent_iso(minutes_ago),
            "author": {"accountId": account_id}, "body": "hi"}


def test_jira_legacy_suppresses_issue_only_you_touched(poll_mod, monkeypatch,
                                                       sample_cfg):
    """Legacy mode fingerprints on ``updated``, so every edit of yours minted a
    new notification. Issues whose only in-window activity is yours are skipped."""
    sample_cfg["jira"]["event_mode"] = False
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "ME")
    issues = [_legacy_issue([_hist("ME")], [_comment("ME")])]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)
    assert poll_mod.jira_items(sample_cfg, 10) == []


def test_jira_legacy_keeps_issue_others_touched(poll_mod, monkeypatch,
                                                sample_cfg):
    sample_cfg["jira"]["event_mode"] = False
    monkeypatch.setattr(poll_mod, "_jira_myself", lambda cfg: "ME")
    # Your change plus someone else's comment -> still notified.
    issues = [_legacy_issue([_hist("ME")], [_comment("OTHER")]),
              # Only stale (out-of-window) activity -> cannot tell, notified.
              _legacy_issue([_hist("ME", minutes_ago=999)], key="ACME-2")]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)
    assert len(poll_mod.jira_items(sample_cfg, 10)) == 2


def test_jira_legacy_suppress_self_disabled(poll_mod, monkeypatch, sample_cfg):
    sample_cfg["jira"]["event_mode"] = False
    sample_cfg["jira"]["suppress_self"] = False

    def _boom(cfg):
        raise AssertionError("_jira_myself must not be called when disabled")

    monkeypatch.setattr(poll_mod, "_jira_myself", _boom)
    issues = [_legacy_issue([_hist("ME")])]
    monkeypatch.setattr(poll_mod, "_jira_search", lambda cfg, w: issues)
    assert len(poll_mod.jira_items(sample_cfg, 10)) == 1


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
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: notifs)

    items = poll_mod.gh_notifications(sample_cfg)
    assert len(items) == 1
    assert items[0]["subtitle"] == "acme/app · review requested"
    assert items[0]["url"] == "https://github.com/acme/app/pull/7"


def test_gh_notifications_fp_includes_updated_at(poll_mod, monkeypatch, sample_cfg):
    """New activity on a thread bumps ``updated_at`` and must produce a new
    fingerprint: a second review on the same PR is news. (Keying on the id
    alone meant a PR notified exactly once in its lifetime.) The same
    ``updated_at`` seen across two polls yields the same fingerprint."""
    def _notif(updated):
        return [{
            "id": "42", "reason": "review_requested",
            "subject": {"title": "Please review",
                        "url": "https://api.github.com/repos/acme/app/pulls/7"},
            "repository": {"full_name": "acme/app"}, "updated_at": updated,
        }]

    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: _notif("2026-01-01T00:00:00Z"))
    fp_first = poll_mod.gh_notifications(sample_cfg)[0]["fp"]
    fp_again = poll_mod.gh_notifications(sample_cfg)[0]["fp"]
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: _notif("2026-02-02T09:09:09Z"))
    fp_second = poll_mod.gh_notifications(sample_cfg)[0]["fp"]
    assert fp_first == fp_again == "gh-notif:42:2026-01-01T00:00:00Z"
    assert fp_second == "gh-notif:42:2026-02-02T09:09:09Z"


def test_gh_notifications_list_failure_records_error(poll_mod, monkeypatch,
                                                     sample_cfg):
    """A failing ``gh api notifications`` is a *source failure*, not "no
    news": it is recorded so the app can show it and hold the poll cursor."""
    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: poll_mod._GH_FAIL)
    assert poll_mod.gh_notifications(sample_cfg) == []
    assert "github" in poll_mod.poll_errors()
    assert "gh auth status" in poll_mod.poll_errors()["github"]


def test_gh_notifications_non_list_payload_is_empty(poll_mod, monkeypatch,
                                                    sample_cfg):
    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: {"message": "weird"})
    assert poll_mod.gh_notifications(sample_cfg) == []
    assert poll_mod.poll_errors() == {}


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
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: notifs)
    items = poll_mod.gh_notifications(sample_cfg)
    assert items[0]["url"] == "https://github.com/acme/app/pulls"


def test_gh_notifications_falls_back_to_notifications_url(poll_mod, monkeypatch, sample_cfg):
    notifs = [{
        "id": "4", "reason": "assign",
        "subject": {"title": "orphan"},  # no url, no repo
        "repository": {}, "updated_at": "t",
    }]
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: notifs)
    items = poll_mod.gh_notifications(sample_cfg)
    assert items[0]["url"] == "https://github.com/notifications"


# ---------------------------------------------------------------------------
# GitHub — self-suppression (suppress_self)
# ---------------------------------------------------------------------------

_PR_URL = "https://api.github.com/repos/acme/app/pulls/1"
_TIMELINE_URL = "repos/acme/app/issues/1/timeline?per_page=100&page=1"


def _own_pr_notif(latest_comment_url=_PR_URL, reason="author", nid="1",
                  updated="t"):
    """A notification thread for a PR the current user (octocat) authored."""
    return {
        "id": nid, "reason": reason,
        "subject": {"title": "My PR", "type": "PullRequest", "url": _PR_URL,
                    "latest_comment_url": latest_comment_url},
        "repository": {"full_name": "acme/app"}, "updated_at": updated,
    }


def _gh_router(notifs, routes):
    """Build a fake ``_gh_api``: ``notifications`` -> notifs, ``user`` -> a
    profile without a public email (unless overridden), otherwise look up the
    request path in ``routes`` (exact match), else ``_GH_FAIL``-free ``[]``.
    ``routes`` values may be callables (called with no args) for side effects.
    """
    calls = []
    routes = {"user": {"login": "octocat", "email": None}, **routes}

    def fake(args):
        calls.append(args)
        if args[:2] == ["api", "notifications"]:
            return notifs
        target = args[1] if args and args[0] == "api" else " ".join(args)
        val = routes.get(target, [])
        return val() if callable(val) else val

    fake.calls = calls
    return fake


def test_gh_notifications_suppresses_own_push(poll_mod, monkeypatch,
                                              sample_cfg):
    """You pushed to your own PR: latest_comment_url still equals the PR URL,
    the timeline ends with a ``committed`` event, and the commit's author is
    you -> suppressed."""
    fake = _gh_router([_own_pr_notif()], {
        _TIMELINE_URL: [
            {"event": "commented", "user": {"login": "reviewer"}},
            {"event": "committed", "sha": "abc"},
        ],
        "repos/acme/app/commits/abc": {"author": {"login": "octocat"}},
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod.gh_notifications(sample_cfg, "octocat") == []


_UNLINKED_COMMIT = {"author": None, "committer": None,
                    "commit": {"author": {"email": "Me@Typo.om"}}}


def test_gh_notifications_unlinked_push_matched_by_configured_email(
        poll_mod, monkeypatch, sample_cfg):
    """GitHub cannot attribute a commit whose git email is not registered on
    any account (e.g. a typo'd ``user.email``). ``github.emails`` names such
    addresses as yours."""
    sample_cfg["github"]["emails"] = ["me@typo.om"]
    # Not an own-PR thread, so only the email list can attribute the push.
    fake = _gh_router([_own_pr_notif(reason="review_requested")], {
        _TIMELINE_URL: [{"event": "committed", "sha": "abc"}],
        "repos/acme/app/commits/abc": _UNLINKED_COMMIT,
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod.gh_notifications(sample_cfg, "octocat") == []


def test_gh_notifications_unlinked_push_matched_by_profile_email(
        poll_mod, monkeypatch, sample_cfg):
    fake = _gh_router([_own_pr_notif(reason="review_requested")], {
        "user": {"login": "octocat", "email": "ME@typo.om"},
        _TIMELINE_URL: [{"event": "committed", "sha": "abc"}],
        "repos/acme/app/commits/abc": _UNLINKED_COMMIT,
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod.gh_notifications(sample_cfg, "octocat") == []
    # The profile is fetched once per process, not per poll.
    poll_mod.gh_notifications(sample_cfg, "octocat")
    assert fake.calls.count(["api", "user"]) == 1


def test_gh_notifications_unlinked_push_on_own_pr_assumed_yours(
        poll_mod, monkeypatch, sample_cfg):
    """Last resort: an unattributed push to a PR *you* authored is treated as
    your own, with a one-time hint in the log."""
    logged = []
    monkeypatch.setattr(poll_mod, "_log", logged.append)
    fake = _gh_router([_own_pr_notif(reason="author")], {
        _TIMELINE_URL: [{"event": "committed", "sha": "abc"}],
        "repos/acme/app/commits/abc": _UNLINKED_COMMIT,
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod.gh_notifications(sample_cfg, "octocat") == []
    hints = [m for m in logged if "not linked to a GitHub account" in m]
    assert len(hints) == 1 and "me@typo.om" in hints[0]
    # Same email again (new thread update) -> no second hint.
    poll_mod._gh_actor_cache.clear()
    poll_mod.gh_notifications(sample_cfg, "octocat")
    assert len([m for m in logged if "not linked" in m]) == 1


def test_gh_notifications_unlinked_push_on_others_pr_notifies(
        poll_mod, monkeypatch, sample_cfg):
    # Not your PR, email unknown -> cannot attribute -> notify.
    fake = _gh_router([_own_pr_notif(reason="review_requested")], {
        _TIMELINE_URL: [{"event": "committed", "sha": "abc"}],
        "repos/acme/app/commits/abc": _UNLINKED_COMMIT,
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert len(poll_mod.gh_notifications(sample_cfg, "octocat")) == 1


def test_gh_my_emails_profile_failure_is_retried(poll_mod, monkeypatch,
                                                 sample_cfg):
    sample_cfg["github"]["emails"] = [" A@x.com ", "", 42]
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: poll_mod._GH_FAIL)
    assert poll_mod._gh_my_emails(sample_cfg) == frozenset({"a@x.com"})
    assert "auto" not in poll_mod._gh_emails_cache
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: [])  # odd payload
    assert poll_mod._gh_my_emails(sample_cfg) == frozenset({"a@x.com"})
    assert poll_mod._gh_emails_cache["auto"] == set()


def test_gh_notifications_keeps_review_of_own_pr(poll_mod, monkeypatch,
                                                 sample_cfg):
    """Someone reviewed your PR. GitHub leaves latest_comment_url pointing at
    the PR itself (whose ``user`` is *you*); the old lookup therefore hid every
    review of your own PRs. The timeline's last event names the reviewer."""
    fake = _gh_router([_own_pr_notif()], {
        _TIMELINE_URL: [
            {"event": "committed", "sha": "abc"},
            {"event": "reviewed", "user": {"login": "reviewer"}},
        ],
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    items = poll_mod.gh_notifications(sample_cfg, "octocat")
    assert len(items) == 1
    # The PR object itself must not have been fetched for its ``user``.
    assert ["api", _PR_URL] not in fake.calls


def test_gh_notifications_keeps_others_activity(poll_mod, monkeypatch,
                                                sample_cfg):
    fake = _gh_router([_own_pr_notif()], {
        _TIMELINE_URL: [{"event": "merged", "actor": {"login": "someone-else"}}],
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert len(poll_mod.gh_notifications(sample_cfg, "octocat")) == 1


def test_gh_notifications_login_compare_is_case_insensitive(poll_mod,
                                                            monkeypatch,
                                                            sample_cfg):
    fake = _gh_router([_own_pr_notif()], {
        _TIMELINE_URL: [{"event": "commented", "user": {"login": "octocat"}}],
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod.gh_notifications(sample_cfg, "OctoCat") == []


def test_gh_notifications_ignores_passive_timeline_events(poll_mod, monkeypatch,
                                                          sample_cfg):
    """Bob's comment mentioning you produces ``commented`` (Bob) followed by
    ``mentioned``/``subscribed`` whose actor is *you*. Those must not make the
    thread look like your own activity."""
    fake = _gh_router([_own_pr_notif(reason="mention")], {
        _TIMELINE_URL: [
            {"event": "commented", "user": {"login": "bob"}},
            {"event": "mentioned", "actor": {"login": "octocat"}},
            {"event": "subscribed", "actor": {"login": "octocat"}},
        ],
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert len(poll_mod.gh_notifications(sample_cfg, "octocat")) == 1


def test_gh_notifications_defers_thread_when_lookup_fails(poll_mod, monkeypatch,
                                                          sample_cfg):
    """A failed actor lookup must not fall through to notifying (that is how
    your own pushes popped up on a flaky network). The item is skipped so it
    is retried next poll, and the failure is not cached."""
    logged = []
    monkeypatch.setattr(poll_mod, "_log", logged.append)
    fake = _gh_router([_own_pr_notif()], {_TIMELINE_URL: lambda: poll_mod._GH_FAIL})
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod.gh_notifications(sample_cfg, "octocat") == []
    assert any("retrying next poll" in m for m in logged)
    assert poll_mod._gh_actor_cache == {}


def test_gh_notifications_unknown_actor_notifies(poll_mod, monkeypatch,
                                                 sample_cfg):
    # Empty timeline -> cannot tell who acted -> fail-open (notify).
    fake = _gh_router([_own_pr_notif()], {_TIMELINE_URL: []})
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert len(poll_mod.gh_notifications(sample_cfg, "octocat")) == 1


def test_gh_notifications_actor_lookup_is_cached_per_update(poll_mod,
                                                            monkeypatch,
                                                            sample_cfg):
    fake = _gh_router([_own_pr_notif(updated="t1")], {
        _TIMELINE_URL: [{"event": "commented", "user": {"login": "bob"}}],
    })
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    poll_mod.gh_notifications(sample_cfg, "octocat")
    poll_mod.gh_notifications(sample_cfg, "octocat")
    timeline_calls = [c for c in fake.calls if c[1] == _TIMELINE_URL]
    assert len(timeline_calls) == 1  # second poll served from the cache
    assert poll_mod._gh_actor_cache == {"1:t1": "bob"}


def test_gh_actor_cache_is_bounded(poll_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(poll_mod, "_GH_ACTOR_CACHE_MAX", 2)
    poll_mod._gh_actor_cache.update({"a:1": "x", "b:1": "y"})
    monkeypatch.setattr(poll_mod, "_gh_latest_actor", lambda *a: "bob")
    assert poll_mod._gh_thread_actor(_own_pr_notif(nid="c")) == "bob"
    assert poll_mod._gh_actor_cache == {"c:t": "bob"}


def test_gh_notifications_suppresses_ci_activity(poll_mod, monkeypatch,
                                                 sample_cfg):
    """``ci_activity`` = a workflow run *you* triggered (GitHub's definition),
    so it is dropped under suppress_self without any lookup."""
    notifs = [{
        "id": "9", "reason": "ci_activity",
        "subject": {"title": "CI failed for main", "type": "CheckSuite",
                    "url": None, "latest_comment_url": None},
        "repository": {"full_name": "acme/app"}, "updated_at": "t",
    }]
    fake = _gh_router(notifs, {})
    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod.gh_notifications(sample_cfg, "octocat") == []
    # No per-thread lookup was made (only the notifications list + profile).
    assert set(map(tuple, fake.calls)) == {("api", "notifications"),
                                           ("api", "user")}
    # ... but kept when suppress_self is off.
    sample_cfg["github"]["suppress_self"] = False
    assert len(poll_mod.gh_notifications(sample_cfg, "octocat")) == 1


def test_gh_notifications_no_login_skips_actor_lookup(poll_mod, monkeypatch,
                                                      sample_cfg):
    # Without a known login, suppression is off and no lookup is made.
    notifs = [{
        "id": "1", "reason": "author",
        "subject": {"title": "My PR",
                    "url": "https://api.github.com/repos/acme/app/pulls/1",
                    "latest_comment_url": "https://x"},
        "repository": {"full_name": "acme/app"}, "updated_at": "t",
    }]

    def fake_gh(args):
        assert args[:2] == ["api", "notifications"], "no actor lookup expected"
        return notifs

    monkeypatch.setattr(poll_mod, "_gh_api", fake_gh)
    assert len(poll_mod.gh_notifications(sample_cfg, "")) == 1


def test_gh_notifications_suppress_self_disabled(poll_mod, monkeypatch,
                                                 sample_cfg):
    sample_cfg["github"]["suppress_self"] = False
    notifs = [{
        "id": "1", "reason": "author",
        "subject": {"title": "My PR",
                    "url": "https://api.github.com/repos/acme/app/pulls/1",
                    "latest_comment_url": "https://x"},
        "repository": {"full_name": "acme/app"}, "updated_at": "t",
    }]

    def fake_gh(args):
        assert args[:2] == ["api", "notifications"], "no actor lookup expected"
        return notifs

    monkeypatch.setattr(poll_mod, "_gh_api", fake_gh)
    assert len(poll_mod.gh_notifications(sample_cfg, "octocat")) == 1


def test_gh_latest_actor_no_url_returns_empty(poll_mod):
    assert poll_mod._gh_latest_actor({}) == ""
    assert poll_mod._gh_latest_actor(None) == ""


def test_gh_latest_actor_non_pr_subject_uses_latest_comment(poll_mod,
                                                            monkeypatch):
    # A commit-comment thread: subject.url is a commit, not a PR/issue, so the
    # latest_comment_url lookup is used; commits expose ``author``, not ``user``.
    subj = {"url": "https://api.github.com/repos/acme/app/commits/abc",
            "latest_comment_url": "https://api.github.com/repos/acme/app/commits/abc"}
    monkeypatch.setattr(poll_mod, "_gh_api",
                        lambda args: {"author": {"login": "pusher"}})
    assert poll_mod._gh_latest_actor(subj) == "pusher"
    monkeypatch.setattr(poll_mod, "_gh_api",
                        lambda args: {"user": {"login": "commenter"}})
    assert poll_mod._gh_latest_actor(subj) == "commenter"


def test_gh_latest_actor_non_dict_response(poll_mod, monkeypatch):
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: [])
    assert poll_mod._gh_latest_actor({"latest_comment_url": "https://x"}) == ""


def test_gh_latest_actor_propagates_failure(poll_mod, monkeypatch):
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: poll_mod._GH_FAIL)
    assert poll_mod._gh_latest_actor(
        {"latest_comment_url": "https://x"}) is poll_mod._GH_FAIL
    assert poll_mod._gh_latest_actor(
        {"url": _PR_URL}) is poll_mod._GH_FAIL


def test_gh_timeline_actor_walks_pages(poll_mod, monkeypatch):
    monkeypatch.setattr(poll_mod, "_GH_TIMELINE_PAGE", 2)
    pages = {
        "repos/acme/app/issues/1/timeline?per_page=2&page=1": [
            {"event": "commented", "user": {"login": "a"}},
            {"event": "commented", "user": {"login": "b"}},
        ],
        "repos/acme/app/issues/1/timeline?per_page=2&page=2": [
            {"event": "labeled", "actor": {"login": "c"}},
        ],
    }
    calls = []

    def fake(args):
        calls.append(args[1])
        return pages.get(args[1], [])

    monkeypatch.setattr(poll_mod, "_gh_api", fake)
    assert poll_mod._gh_timeline_actor("acme/app", "1") == "c"
    assert len(calls) == 2  # stopped after the short page


def test_gh_timeline_actor_exact_page_boundary(poll_mod, monkeypatch):
    # Exactly one full page followed by an empty page: the full page's last
    # event wins and paging stops at the empty response.
    monkeypatch.setattr(poll_mod, "_GH_TIMELINE_PAGE", 1)
    pages = {"repos/acme/app/issues/1/timeline?per_page=1&page=1":
             [{"event": "closed", "actor": {"login": "z"}}]}
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: pages.get(args[1], []))
    assert poll_mod._gh_timeline_actor("acme/app", "1") == "z"


def test_gh_timeline_actor_line_comments_and_unknown(poll_mod, monkeypatch):
    events = [
        {"event": "line-commented",
         "comments": [{"user": {"login": "r1"}}, {"user": {"login": "r2"}}]},
        {"event": "weird", "actor": None},  # no login -> skipped
        "not-a-dict",
    ]
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: events)
    assert poll_mod._gh_timeline_actor("acme/app", "1") == "r2"
    # Nothing identifies an actor -> "".
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: [{"event": "x"}])
    assert poll_mod._gh_timeline_actor("acme/app", "1") == ""


def test_gh_timeline_actor_commit_resolution(poll_mod, monkeypatch):
    timeline = [{"event": "committed", "sha": "abc"}]

    def with_commit(commit):
        def fake(args):
            if "timeline" in args[1]:
                return timeline
            return commit
        return fake

    # Author not linked to a GitHub account -> unknown, not the previous actor.
    monkeypatch.setattr(poll_mod, "_gh_api",
                        with_commit({"author": None, "committer": None}))
    assert poll_mod._gh_timeline_actor("acme/app", "1") == ""
    # ... even on an own thread when no login is known to attribute it to.
    assert poll_mod._gh_timeline_actor("acme/app", "1", own_thread=True) == ""
    # Commit payload without a ``commit.author`` block -> email is "".
    monkeypatch.setattr(poll_mod, "_gh_api",
                        with_commit({"author": None, "commit": {}}))
    assert poll_mod._gh_timeline_actor("acme/app", "1", me="octocat") == ""
    # Committer used when author has no login.
    monkeypatch.setattr(poll_mod, "_gh_api",
                        with_commit({"author": None,
                                     "committer": {"login": "bot"}}))
    assert poll_mod._gh_timeline_actor("acme/app", "1") == "bot"
    # Commit lookup failure propagates.
    monkeypatch.setattr(poll_mod, "_gh_api", with_commit(poll_mod._GH_FAIL))
    assert poll_mod._gh_timeline_actor("acme/app", "1") is poll_mod._GH_FAIL
    # Non-dict commit payload -> unknown.
    monkeypatch.setattr(poll_mod, "_gh_api", with_commit([]))
    assert poll_mod._gh_timeline_actor("acme/app", "1") == ""
    # A committed event without a sha cannot be resolved.
    monkeypatch.setattr(poll_mod, "_gh_api",
                        lambda args: [{"event": "committed"}])
    assert poll_mod._gh_timeline_actor("acme/app", "1") == ""


def test_gh_api_returns_fail_sentinel(poll_mod, monkeypatch):
    import subprocess as sp

    def boom(*a, **k):
        raise sp.CalledProcessError(1, "gh", stderr="boom")

    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.subprocess, "run", boom)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod._gh_api(["api", "x"]) is poll_mod._GH_FAIL
    assert poll_mod._gh_json(["api", "x"]) == []


def test_gh_login_autodetect_swallows_error(poll_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("gh missing")

    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.subprocess, "run", boom)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    assert poll_mod.gh_login({"github": {"login": ""}}) == ""
    assert poll_mod._gh_login_cache == {}  # failures are not cached


def test_gh_login_autodetect_is_cached(poll_mod, monkeypatch, fake_proc):
    calls = []

    def run(*a, **k):
        calls.append(a)
        return fake_proc(stdout="autodetected\n")

    monkeypatch.setattr(poll_mod.subprocess, "run", run)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    cfg = {"github": {"login": ""}}
    assert poll_mod.gh_login(cfg) == "autodetected"
    assert poll_mod.gh_login(cfg) == "autodetected"
    assert len(calls) == 1


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
    # A non-list / malformed payload is treated like "no checks".
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: {"oops": 1})
    assert poll_mod._ci_rollup_for_pr(pr) is None
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: ["not-a-dict"])
    assert poll_mod._ci_rollup_for_pr(pr) is None


def test_ci_rollup_fp_distinguishes_runs(poll_mod, monkeypatch):
    """Two CI runs on the same PR with the same roll-up (fail, push, fail
    again) must have different fingerprints — otherwise the second failure
    never notifies. The run identity comes from the checks' ``link``s."""
    pr = {"url": "https://github.com/acme/app/pull/5", "title": "My PR"}
    run1 = [{"bucket": "fail",
             "link": "https://github.com/acme/app/actions/runs/111/job/1"}]
    run2 = [{"bucket": "fail",
             "link": "https://github.com/acme/app/actions/runs/222/job/1"}]
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: run1)
    fp1 = poll_mod._ci_rollup_for_pr(pr)["fp"]
    fp1_again = poll_mod._ci_rollup_for_pr(pr)["fp"]
    monkeypatch.setattr(poll_mod, "_gh_json", lambda args: run2)
    fp2 = poll_mod._ci_rollup_for_pr(pr)["fp"]
    assert fp1 == fp1_again
    assert fp1 != fp2
    assert fp1.startswith("gh-ci:acme/app#5:fail:")
    # The requested fields include ``link`` so the run id is available.
    seen_args = []
    monkeypatch.setattr(poll_mod, "_gh_json",
                        lambda args: seen_args.append(args) or run1)
    poll_mod._ci_rollup_for_pr(pr)
    assert "state,bucket,link" in seen_args[0]


def test_gh_ci_fallback_search_failure_records_error(poll_mod, monkeypatch,
                                                     sample_cfg):
    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: poll_mod._GH_FAIL)
    assert poll_mod.gh_ci_fallback(sample_cfg, "octocat") == []
    assert "github" in poll_mod.poll_errors()
    # A non-list payload is treated as "no PRs".
    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: {"total_count": 0})
    assert poll_mod.gh_ci_fallback(sample_cfg, "octocat") == []
    assert poll_mod.poll_errors() == {}


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
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg, login=None: ["g"])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: ["c"])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w, status=None: ["p"])

    phases = poll_mod.collect_all(sample_cfg)
    assert phases == [("jira", ["j"]), ("github", ["g"]), ("ci", ["c"]),
                      ("pagerduty", ["p"])]


def test_collect_all_wires_custom_logger(poll_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: [])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg, login=None: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w, status=None: [])

    captured = []
    poll_mod.collect_all(sample_cfg, log=captured.append)
    # The passed-in logger replaces the module-level _log.
    poll_mod._log("hello")
    assert "hello" in captured


def test_collect_all_passes_dynamic_window(poll_mod, monkeypatch, sample_cfg):
    # since_ts is turned into a lookback window and handed to the sources.
    seen = {}
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: seen.setdefault("jira", w) or [])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg, login=None: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w, status=None: seen.setdefault("pd", w) or [])

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
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: [])
    assert poll_mod.gh_ci_fallback(sample_cfg, "octocat") == []


def test_gh_ci_fallback_collects_rollups(poll_mod, monkeypatch, sample_cfg):
    prs = [
        {"url": "https://github.com/acme/app/pull/1", "title": "A"},
        {"url": "https://github.com/acme/app/pull/2", "title": "B"},
    ]
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: prs)
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


def _ci_prs_and_rollups(poll_mod, monkeypatch):
    prs = [{"url": f"https://github.com/acme/app/pull/{i}", "title": r}
           for i, r in enumerate(("fail", "pending", "pass"), start=1)]
    monkeypatch.setattr(poll_mod, "_gh_api", lambda args: prs)
    monkeypatch.setattr(
        poll_mod, "_ci_rollup_for_pr",
        lambda pr: {"fp": f"gh-ci:{pr['title']}", "ci_only": True,
                    "ci_rollup": pr["title"]})


def test_gh_ci_fallback_only_fail_notifies_by_default(poll_mod, monkeypatch,
                                                      sample_cfg):
    """You just pushed, so a ``pending`` roll-up tells you nothing new: it (and
    ``pass``) are returned ``quiet`` so the app remembers them silently."""
    _ci_prs_and_rollups(poll_mod, monkeypatch)
    items = poll_mod.gh_ci_fallback(sample_cfg, "octocat")
    assert {it["ci_rollup"]: it.get("quiet", False) for it in items} == {
        "fail": False, "pending": True, "pass": True}


def test_gh_ci_fallback_ci_notify_configurable(poll_mod, monkeypatch,
                                               sample_cfg):
    _ci_prs_and_rollups(poll_mod, monkeypatch)
    sample_cfg["github"]["ci_notify"] = ["fail", "pending"]
    items = poll_mod.gh_ci_fallback(sample_cfg, "octocat")
    assert {it["ci_rollup"]: it.get("quiet", False) for it in items} == {
        "fail": False, "pending": False, "pass": True}
    # An empty list turns CI notifications off entirely (all quiet).
    sample_cfg["github"]["ci_notify"] = []
    items = poll_mod.gh_ci_fallback(sample_cfg, "octocat")
    assert all(it["quiet"] for it in items)


# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------

def _pd_incident(iid="PINC1", num=42, status="triggered", title="Disk full",
                 service="prod-api", urgency="high", teams=("PTEAM1",),
                 resolved_at=None,
                 html_url="https://acme.pagerduty.com/incidents/PINC1"):
    inc = {
        "id": iid,
        "incident_number": num,
        "status": status,
        "urgency": urgency,
        "service": {"summary": service} if service else {},
        "teams": [{"id": t, "type": "team_reference"} for t in teams],
        "title": title,
        "html_url": html_url,
    }
    if resolved_at:
        inc["resolved_at"] = resolved_at
    return inc


def _pd_entry(eid, etype, incident, created=None, agent=None, assignees=None,
              channel=None, summary=None, **extra):
    e = {
        "id": eid,
        "type": etype,
        "created_at": created or _recent_iso(2),
        "incident": incident,
        "agent": agent if agent is not None
        else {"id": "PSVC", "type": "service_reference", "summary": "prod-api"},
    }
    if assignees is not None:
        e["assignees"] = assignees
    if channel is not None:
        e["channel"] = channel
    if summary is not None:
        e["summary"] = summary
    e.update(extra)
    return e


def _user(uid, name="Bob"):
    return {"id": uid, "type": "user_reference", "summary": name}


ME = _user("PUSER1", "Me Myself")
BOB = _user("PBOB", "Bob")
ALICE = _user("PALICE", "Alice")


def _has(params, key, value=None):
    return any(k == key and (value is None or v == value) for k, v in params)


def _pd_router(poll_mod, monkeypatch, team_entries=(), mine_active=(),
               mine_resolved=(), per_incident=None, oncalls=(), me=None):
    """Install a fake ``_pd_get`` that answers by endpoint; returns the call log."""
    calls = []
    per_incident = per_incident or {}

    def fake_get(token, path, params=None):
        params = list(params or [])
        calls.append((path, params))
        if path == "/users/me":
            return {"user": me} if me is not None else {}
        if path == "/log_entries":
            return {"log_entries": list(team_entries), "more": False}
        if path.startswith("/incidents/") and path.endswith("/log_entries"):
            iid = path.split("/")[2]
            return {"log_entries": list(per_incident.get(iid, [])), "more": False}
        if path == "/incidents":
            if _has(params, "statuses[]", "resolved"):
                return {"incidents": list(mine_resolved), "more": False}
            return {"incidents": list(mine_active), "more": False}
        if path == "/oncalls":
            return {"oncalls": list(oncalls), "more": False}
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(poll_mod, "_pd_get", fake_get)
    return calls


def _incident_items(items):
    return [it for it in items if not it["message"].startswith("[on-call]")]


def test_pagerduty_items_disabled_returns_empty(poll_mod):
    assert poll_mod.pagerduty_items({"pagerduty": {"enabled": False}}, 10) == []


def test_pagerduty_items_no_token_returns_empty(poll_mod):
    cfg = {"pagerduty": {"enabled": True, "api_token": ""}}
    assert poll_mod.pagerduty_items(cfg, 10) == []


# -- _pd_get / pagination ---------------------------------------------------

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
        captured["auth"] = req.get_header("Authorization")
        return FakeResp()

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", fake_urlopen)
    poll_mod._pd_get("tok", "/incidents", [("user_ids[]", "P1"), ("limit", "5")])
    assert "user_ids%5B%5D=P1" in captured["url"]
    assert captured["url"].startswith("https://api.pagerduty.com/incidents?")
    assert captured["auth"] == "Token token=tok"


def test_pd_get_swallows_network_error(poll_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("pd down")

    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._pd_get("tok", "/users/me") == {}


def test_pd_get_all_follows_pagination(poll_mod, monkeypatch):
    calls = []

    def fake_get(token, path, params=None):
        calls.append(dict(params))
        offset = int(dict(params)["offset"])
        if offset == 0:
            return {"incidents": [{"id": "A"}], "more": True}
        return {"incidents": [{"id": "B"}], "more": False}

    monkeypatch.setattr(poll_mod, "_pd_get", fake_get)
    out = poll_mod._pd_get_all("tok", "/incidents", [("x", "1")], "incidents")
    assert [i["id"] for i in out] == ["A", "B"]
    assert [c["offset"] for c in calls] == ["0", str(poll_mod._PD_PAGE_SIZE)]
    assert all(c["x"] == "1" and c["limit"] == str(poll_mod._PD_PAGE_SIZE)
               for c in calls)


def test_pd_get_all_respects_max_pages(poll_mod, monkeypatch):
    monkeypatch.setattr(poll_mod, "_pd_get",
                        lambda t, p, params=None: {"incidents": [{"id": "X"}],
                                                   "more": True})
    out = poll_mod._pd_get_all("tok", "/incidents", [], "incidents", max_pages=3)
    assert len(out) == 3  # stopped after 3 pages even though more=True


def test_pd_get_all_handles_empty_response(poll_mod, monkeypatch):
    monkeypatch.setattr(poll_mod, "_pd_get", lambda t, p, params=None: {})
    assert poll_mod._pd_get_all("tok", "/incidents", [], "incidents") == []


# -- identity / profile -----------------------------------------------------

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


def test_pd_profile_caches_users_me(poll_mod, monkeypatch):
    cfg = {"pagerduty": {"enabled": True, "api_token": "tok",
                         "user_id": "", "team_ids": []}}
    calls = []

    def fake_get(t, p, params=None):
        calls.append(p)
        return {"user": {"id": "PME", "name": "Me", "email": "me@acme.com",
                         "teams": [{"id": "PTX"}]}}

    monkeypatch.setattr(poll_mod, "_pd_get", fake_get)
    p1 = poll_mod.pd_profile(cfg, now_ts=1000.0)
    p2 = poll_mod.pd_profile(cfg, now_ts=1000.0 + 60)
    assert p1 == {"user_id": "PME", "team_ids": ["PTX"], "name": "Me",
                  "email": "me@acme.com"}
    assert p2 == p1
    assert calls == ["/users/me"]  # second call served from cache
    # After the TTL the profile is refreshed.
    poll_mod.pd_profile(cfg, now_ts=1000.0 + poll_mod._PD_PROFILE_TTL_S + 1)
    assert calls == ["/users/me", "/users/me"]


def test_pd_profile_does_not_cache_failures(poll_mod, monkeypatch):
    cfg = {"pagerduty": {"enabled": True, "api_token": "tok"}}
    calls = []
    monkeypatch.setattr(poll_mod, "_pd_get",
                        lambda t, p, params=None: calls.append(p) or {})
    poll_mod.pd_profile(cfg, now_ts=1.0)
    poll_mod.pd_profile(cfg, now_ts=2.0)
    assert calls == ["/users/me", "/users/me"]


def test_pd_profile_prefers_configured_ids(poll_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(poll_mod, "_pd_get", lambda t, p, params=None: {
        "user": {"id": "OTHER", "name": "Me", "teams": [{"id": "PTZ"}]}})
    prof = poll_mod.pd_profile(sample_cfg)
    assert prof["user_id"] == "PUSER1" and prof["team_ids"] == ["PTEAM1"]
    assert prof["name"] == "Me"


def test_pd_profile_no_token(poll_mod):
    prof = poll_mod.pd_profile({"pagerduty": {"user_id": "X"}})
    assert prof == {"user_id": "X", "team_ids": [], "name": "", "email": ""}


def test_pd_profile_uses_real_clock_by_default(poll_mod, monkeypatch):
    cfg = {"pagerduty": {"api_token": "tok"}}
    monkeypatch.setattr(poll_mod, "_pd_get",
                        lambda t, p, params=None: {"user": {"id": "PME"}})
    assert poll_mod.pd_profile(cfg)["user_id"] == "PME"
    assert "tok" in poll_mod._pd_profile_cache


# -- incident timeline events ----------------------------------------------

def test_pagerduty_team_timeline_events(poll_mod, monkeypatch, sample_cfg):
    inc = _pd_incident(iid="PT1", num=7, status="acknowledged", title="CPU high")
    entries = [
        _pd_entry("E1", "trigger_log_entry", inc, created=_recent_iso(5)),
        _pd_entry("E2", "acknowledge_log_entry", inc, created=_recent_iso(3),
                  agent=BOB),
        _pd_entry("E3", "notify_log_entry", inc),  # ignored type
    ]
    calls = _pd_router(poll_mod, monkeypatch, team_entries=entries)
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))

    assert [it["fp"] for it in items] == ["pd:PT1:E1", "pd:PT1:E2"]
    assert items[0]["title"] == "PagerDuty"
    assert items[0]["subtitle"] == "#7 · acknowledged · prod-api"
    assert items[0]["message"] == "[team incident] triggered — CPU high"
    assert items[1]["message"] == "[team incident] acknowledged by Bob — CPU high"
    assert items[0]["url"] == "https://acme.pagerduty.com/incidents/PINC1"
    assert items[0]["sound"] is True
    # The team timeline is queried with the window + team filter + incidents.
    path, params = next(c for c in calls if c[0] == "/log_entries")
    assert _has(params, "team_ids[]", "PTEAM1")
    assert _has(params, "include[]", "incidents")
    assert _has(params, "since") and _has(params, "until")


def test_pagerduty_suppresses_own_actions_everywhere(poll_mod, monkeypatch,
                                                     sample_cfg):
    mine = _pd_incident(iid="PM1", num=1, teams=())
    team = _pd_incident(iid="PT1", num=2)
    _pd_router(poll_mod, monkeypatch,
               team_entries=[_pd_entry("E1", "resolve_log_entry", team, agent=ME)],
               mine_active=[mine],
               per_incident={"PM1": [_pd_entry("E2", "acknowledge_log_entry",
                                               mine, agent=ME)]})
    # Both my ack on my own incident and my resolve on a team incident are hidden.
    assert _incident_items(poll_mod.pagerduty_items(sample_cfg, 10)) == []


def test_pagerduty_suppress_self_disabled_keeps_own(poll_mod, monkeypatch,
                                                    sample_cfg):
    sample_cfg["pagerduty"]["suppress_self"] = False
    team = _pd_incident(iid="PT1", num=2)
    _pd_router(poll_mod, monkeypatch,
               team_entries=[_pd_entry("E1", "resolve_log_entry", team, agent=ME)])
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert items[0]["message"] == "[team incident] resolved by Me Myself — Disk full"


def test_pagerduty_escalated_to_me_is_assigned_to_you(poll_mod, monkeypatch,
                                                      sample_cfg):
    inc = _pd_incident(iid="PT1", num=3)
    entries = [_pd_entry("E1", "escalate_log_entry", inc, assignees=[ME, ALICE],
                         agent=BOB)]
    _pd_router(poll_mod, monkeypatch, team_entries=entries)
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert items[0]["message"] == \
        "[assigned to you] escalated to you, Alice by Bob — Disk full"


def test_pagerduty_reassigned_away_on_my_incident(poll_mod, monkeypatch,
                                                  sample_cfg):
    mine = _pd_incident(iid="PM1", num=4)  # on my team, assigned to me
    entries = [_pd_entry("E1", "reassign_log_entry", mine, assignees=[ALICE],
                         agent=BOB)]
    _pd_router(poll_mod, monkeypatch, team_entries=entries, mine_active=[mine])
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert items[0]["message"] == \
        "[your incident] reassigned to Alice by Bob — Disk full"


def test_pagerduty_collapses_trigger_and_initial_assign(poll_mod, monkeypatch,
                                                        sample_cfg):
    inc = _pd_incident(iid="PT1", num=5)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=2)
    entries = [
        _pd_entry("E1", "trigger_log_entry", inc, created=t0.isoformat()),
        _pd_entry("E2", "assign_log_entry", inc, assignees=[ME],
                  created=(t0 + timedelta(seconds=1)).isoformat()),
    ]
    _pd_router(poll_mod, monkeypatch, team_entries=entries)
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    # One notification, not two — and it says it landed on you.
    assert len(items) == 1
    assert items[0]["fp"] == "pd:PT1:E1"
    assert items[0]["message"] == \
        "[assigned to you] triggered, assigned to you — Disk full"


def test_pagerduty_late_assign_is_not_collapsed(poll_mod, monkeypatch,
                                                sample_cfg):
    inc = _pd_incident(iid="PT1", num=5)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    entries = [
        _pd_entry("E1", "trigger_log_entry", inc, created=t0.isoformat()),
        _pd_entry("E2", "assign_log_entry", inc, assignees=[BOB], agent=ALICE,
                  created=(t0 + timedelta(minutes=1)).isoformat()),
    ]
    _pd_router(poll_mod, monkeypatch, team_entries=entries)
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert [it["fp"] for it in items] == ["pd:PT1:E1", "pd:PT1:E2"]
    assert items[1]["message"] == \
        "[team incident] assigned to Bob by Alice — Disk full"


def test_pd_collapse_ignores_unparseable_timestamps(poll_mod):
    inc = {"id": "X"}
    entries = [
        _pd_entry("E1", "trigger_log_entry", inc, created="garbage"),
        _pd_entry("E2", "assign_log_entry", inc, created="garbage"),
        _pd_entry("E3", "assign_log_entry", {"id": "OTHER"}),  # no trigger
    ]
    out = poll_mod._pd_collapse_trigger_assign(entries)
    assert [e["id"] for e in out] == ["E1", "E2", "E3"]


def test_pagerduty_responder_request_to_me(poll_mod, monkeypatch, sample_cfg):
    inc = _pd_incident(iid="PT1", num=6)
    entries = [_pd_entry("E1", "responder_request_log_entry", inc, agent=BOB,
                         responders=[{"user": ME}])]
    _pd_router(poll_mod, monkeypatch, team_entries=entries)
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert items[0]["message"] == \
        "[responder requested] you were requested as a responder by Bob — Disk full"


def test_pd_targets_collects_all_shapes(poll_mod):
    entry = {"assignees": [BOB, "junk"], "responders": [{"user": ALICE}],
             "responder": {"user": ME}}
    ids = [t["id"] for t in poll_mod._pd_targets(entry)]
    assert ids == ["PBOB", "PALICE", "PUSER1"]
    assert poll_mod._pd_targets({"responder": BOB})[0]["id"] == "PBOB"


def test_pagerduty_note_mentioning_me(poll_mod, monkeypatch, sample_cfg):
    inc = _pd_incident(iid="PT1", num=8)
    entries = [
        _pd_entry("E1", "annotate_log_entry", inc, agent=BOB,
                  channel={"type": "note", "summary": "Me Myself please look"}),
        _pd_entry("E2", "annotate_log_entry", inc, agent=BOB,
                  channel={"type": "note", "content": "restarting service"}),
    ]
    _pd_router(poll_mod, monkeypatch, team_entries=entries,
               me={"id": "PUSER1", "name": "Me Myself", "email": "me@acme.com",
                   "teams": [{"id": "PTEAM1"}]})
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert items[0]["message"] == \
        "[mentioned you] note: Me Myself please look by Bob — Disk full"
    assert items[1]["message"] == \
        "[team incident] note: restarting service by Bob — Disk full"


def test_pd_mentions_me_edge_cases(poll_mod):
    assert poll_mod._pd_mentions_me({}, {"name": "Me"}) is False
    assert poll_mod._pd_mentions_me({"summary": "ping me@acme.com"},
                                    {"name": "", "email": "ME@acme.com"}) is True
    assert poll_mod._pd_mentions_me({"summary": "nothing"},
                                    {"name": "Me", "email": ""}) is False


def test_pagerduty_priority_change_uses_summary(poll_mod, monkeypatch,
                                                sample_cfg):
    inc = _pd_incident(iid="PT1", num=9)
    entries = [
        _pd_entry("E1", "priority_change_log_entry", inc, agent=BOB,
                  summary="Changed the priority to P1"),
        _pd_entry("E2", "snooze_log_entry", inc, agent=BOB),  # no summary
        _pd_entry("E3", "exhaust_escalation_path_log_entry", inc),
        _pd_entry("E4", "unacknowledge_log_entry", inc),
    ]
    _pd_router(poll_mod, monkeypatch, team_entries=entries)
    msgs = [it["message"] for it in
            _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))]
    assert msgs == [
        "[team incident] Changed the priority to P1 by Bob — Disk full",
        "[team incident] snoozed by Bob — Disk full",
        "[team incident] escalation exhausted — nobody acknowledged — Disk full",
        "[team incident] unacknowledged (ack timed out) — Disk full",
    ]


def test_pagerduty_notify_team_incidents_off_keeps_only_mine(poll_mod, monkeypatch,
                                                             sample_cfg):
    sample_cfg["pagerduty"]["notify_team_incidents"] = False
    mine = _pd_incident(iid="PM1", num=1)
    team = _pd_incident(iid="PT1", num=2)
    entries = [
        _pd_entry("E1", "acknowledge_log_entry", team, agent=BOB),
        _pd_entry("E2", "acknowledge_log_entry", mine, agent=BOB),
        _pd_entry("E3", "escalate_log_entry", team, assignees=[ME]),
    ]
    _pd_router(poll_mod, monkeypatch, team_entries=entries, mine_active=[mine])
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert [it["fp"] for it in items] == ["pd:PM1:E2", "pd:PT1:E3"]
    assert items[0]["message"].startswith("[your incident]")
    assert items[1]["message"].startswith("[assigned to you]")


def test_pagerduty_low_urgency_is_silent_and_tagged(poll_mod, monkeypatch,
                                                    sample_cfg):
    inc = _pd_incident(iid="PT1", num=10, urgency="low", service="")
    _pd_router(poll_mod, monkeypatch,
               team_entries=[_pd_entry("E1", "trigger_log_entry", inc)])
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert items[0]["subtitle"] == "#10 · triggered · low urgency"
    assert items[0]["sound"] is False
    sample_cfg["pagerduty"]["low_urgency_sound"] = True
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert items[0]["sound"] is True


def test_pagerduty_fetches_timeline_for_my_non_team_incidents(poll_mod,
                                                             monkeypatch,
                                                             sample_cfg):
    other = _pd_incident(iid="PX1", num=11, teams=("PTEAM9",), title="Other")
    on_team = _pd_incident(iid="PM1", num=12)
    calls = _pd_router(poll_mod, monkeypatch, mine_active=[other, on_team],
                       per_incident={"PX1": [
                           _pd_entry("E1", "acknowledge_log_entry",
                                     {"id": "PX1", "type": "incident_reference"},
                                     agent=BOB)]})
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    # Only the incident outside my teams needs its own timeline request...
    timeline_calls = [p for p, _ in calls if p.startswith("/incidents/")]
    assert timeline_calls == ["/incidents/PX1/log_entries"]
    # ...and the entry is enriched with the full incident for the text.
    assert items[0]["message"] == "[your incident] acknowledged by Bob — Other"
    assert items[0]["subtitle"] == "#11 · triggered · prod-api"


def test_pagerduty_caps_per_incident_timeline_requests(poll_mod, monkeypatch,
                                                       sample_cfg):
    mine = [_pd_incident(iid=f"PX{i}", num=i, teams=()) for i in range(15)]
    calls = _pd_router(poll_mod, monkeypatch, mine_active=mine)
    poll_mod.pagerduty_items(sample_cfg, 10)
    timeline_calls = [p for p, _ in calls if p.startswith("/incidents/")]
    assert len(timeline_calls) == poll_mod._PD_MAX_PER_INCIDENT_FETCH


def test_pagerduty_recently_resolved_mine_included(poll_mod, monkeypatch,
                                                   sample_cfg):
    fresh = _pd_incident(iid="PR1", num=13, status="resolved", teams=(),
                         resolved_at=_recent_iso(3))
    stale = _pd_incident(iid="PR2", num=14, status="resolved", teams=(),
                         resolved_at=_recent_iso(60 * 24))
    calls = _pd_router(poll_mod, monkeypatch, mine_resolved=[fresh, stale],
                       per_incident={"PR1": [_pd_entry("E1", "resolve_log_entry",
                                                       {"id": "PR1"}, agent=BOB)]})
    status = {}
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    assert [it["fp"] for it in items] == ["pd:PR1:E1"]
    assert items[0]["message"] == "[your incident] resolved by Bob — Disk full"
    # Resolved incidents are not "active" for the menu.
    assert status["active_incidents"] == []
    resolved_call = next(params for p, params in calls if p == "/incidents"
                         and _has(params, "statuses[]", "resolved"))
    assert _has(resolved_call, "sort_by", "resolved_at:desc")


def test_pd_my_incidents_without_user_id(poll_mod):
    assert poll_mod._pd_my_incidents("tok", "", None) == []


def test_pagerduty_dedupes_entries_across_teams(poll_mod, monkeypatch, sample_cfg):
    sample_cfg["pagerduty"]["team_ids"] = ["PTEAM1", "PTEAM2"]
    inc = _pd_incident(iid="PT1", num=15)
    e = _pd_entry("E1", "trigger_log_entry", inc)
    _pd_router(poll_mod, monkeypatch, team_entries=[e, dict(e)])
    items = _incident_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert len(items) == 1


def test_pagerduty_status_lists_active_incidents(poll_mod, monkeypatch,
                                                 sample_cfg):
    mine = [_pd_incident(iid="PM1", num=1, status="triggered"),
            _pd_incident(iid="PM2", num=2, status="acknowledged", urgency="low",
                         title="Slow")]
    _pd_router(poll_mod, monkeypatch, mine_active=mine)
    status = {}
    poll_mod.pagerduty_items(sample_cfg, 10, status)
    assert status["active_incidents"] == [
        {"number": 1, "status": "triggered", "urgency": "high",
         "title": "Disk full", "url": "https://acme.pagerduty.com/incidents/PINC1"},
        {"number": 2, "status": "acknowledged", "urgency": "low",
         "title": "Slow", "url": "https://acme.pagerduty.com/incidents/PINC1"},
    ]
    assert status["on_call"] is False


def test_pagerduty_only_teams_when_no_user_id(poll_mod, monkeypatch):
    cfg = {"pagerduty": {"enabled": True, "api_token": "tok",
                         "user_id": "", "team_ids": ["PTEAM1"]}}
    inc = _pd_incident(iid="PT1", num=9)
    calls = _pd_router(poll_mod, monkeypatch,
                       team_entries=[_pd_entry("E1", "trigger_log_entry", inc)],
                       me={"id": "", "teams": []})
    items = poll_mod.pagerduty_items(cfg, 10)
    # No user id -> no assigned-incident or on-call queries; team events only.
    assert not any(p in ("/incidents", "/oncalls") for p, _ in calls)
    assert len(items) == 1
    assert items[0]["message"].startswith("[team incident]")


# -- on-call shifts ---------------------------------------------------------

def _oncall(start, end, sched_id="PSCHED1", name="Primary", policy="Backend"):
    return {
        "schedule": {"id": sched_id, "summary": name,
                     "html_url": f"https://acme.pagerduty.com/schedules/{sched_id}"},
        "escalation_policy": {"id": "PPOL1", "summary": policy},
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def _oncall_items(items):
    return [it for it in items if it["message"].startswith("[on-call]")]


def test_pagerduty_oncall_shift_started(poll_mod, monkeypatch, sample_cfg):
    now = datetime.now(timezone.utc)
    oc = _oncall(now - timedelta(minutes=2), now + timedelta(hours=8))
    calls = _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    status = {}
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    assert len(items) == 1
    assert items[0]["fp"] == f"pd-oncall:PSCHED1:{oc['start']}:start"
    assert items[0]["subtitle"] == "On-call · Primary"
    assert items[0]["message"].startswith("[on-call] Your on-call shift started (")
    assert items[0]["url"] == "https://acme.pagerduty.com/schedules/PSCHED1"
    assert items[0]["sound"] is True
    assert status["on_call"] is True
    assert status["schedule"] == "Primary"
    assert poll_mod._parse_dt(status["until"]) == poll_mod._parse_dt(oc["end"])
    path, params = next(c for c in calls if c[0] == "/oncalls")
    assert _has(params, "user_ids[]", "PUSER1")


def test_pagerduty_oncall_upcoming_reminder(poll_mod, monkeypatch, sample_cfg):
    now = datetime.now(timezone.utc)
    # Starts in 58 min -> the 60-min reminder fell due 2 min ago (in window).
    oc = _oncall(now + timedelta(minutes=58), now + timedelta(hours=9))
    _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    status = {}
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    assert len(items) == 1
    assert items[0]["fp"] == f"pd-oncall:PSCHED1:{oc['start']}:before:60"
    assert items[0]["message"].startswith("[on-call] You go on-call in 1h (")
    assert status["on_call"] is False
    assert status["next_schedule"] == "Primary"
    assert poll_mod._parse_dt(status["next_start"]) == poll_mod._parse_dt(oc["start"])


def test_pagerduty_oncall_reminder_not_due(poll_mod, monkeypatch, sample_cfg):
    now = datetime.now(timezone.utc)
    # Starts in 3 days: neither the 1-day nor the 1-hour reminder is due.
    oc = _oncall(now + timedelta(days=3), now + timedelta(days=3, hours=8))
    _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    assert _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10)) == []


def test_pagerduty_oncall_day_reminder(poll_mod, monkeypatch, sample_cfg):
    sample_cfg["pagerduty"]["oncall_remind_before_minutes"] = [1440, "60", 0, "x"]
    now = datetime.now(timezone.utc)
    oc = _oncall(now + timedelta(minutes=1440 - 3), now + timedelta(days=2))
    _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10))
    assert len(items) == 1
    assert "You go on-call in 1 day (" in items[0]["message"]


def test_pagerduty_oncall_shift_ended(poll_mod, monkeypatch, sample_cfg):
    now = datetime.now(timezone.utc)
    oc = _oncall(now - timedelta(hours=8), now - timedelta(minutes=3))
    _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    status = {}
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    assert len(items) == 1
    assert items[0]["fp"].endswith(":end")
    assert "Your on-call shift ended (" in items[0]["message"]
    assert items[0]["sound"] is False
    assert status["on_call"] is False and status["next_start"] is None


def test_pagerduty_oncall_permanent_level_counts_as_on_call(poll_mod, monkeypatch,
                                                            sample_cfg):
    oc = {"schedule": None, "escalation_policy": {"id": "PPOL1", "summary": "Ops"},
          "start": None, "end": None}
    _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    status = {}
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    assert items == []  # no shift boundaries to remind about
    assert status["on_call"] is True and status["until"] is None
    assert status["schedule"] == "Ops"


def test_pagerduty_oncall_dedupes_same_shift_and_picks_latest_end(poll_mod,
                                                                  monkeypatch,
                                                                  sample_cfg):
    now = datetime.now(timezone.utc)
    a = _oncall(now - timedelta(hours=1), now + timedelta(hours=1))
    b = _oncall(now - timedelta(hours=1), now + timedelta(hours=1), policy="Other")
    c = _oncall(now - timedelta(hours=2), now + timedelta(hours=5),
                sched_id="PSCHED2", name="Secondary")
    _pd_router(poll_mod, monkeypatch, oncalls=[a, b, c])
    status = {}
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    assert items == []  # shifts started before the window
    assert status["schedule"] == "Secondary"  # the one ending last wins


def test_pagerduty_oncall_scheduled_shift_beats_permanent_target(poll_mod,
                                                                  monkeypatch,
                                                                  sample_cfg):
    """A schedule-based shift (which has an end time) is the useful thing to
    show; a direct policy target only fills in when there is no shift —
    regardless of the order PagerDuty returns them in."""
    now = datetime.now(timezone.utc)
    perm = {"schedule": None,
            "escalation_policy": {"id": "P", "summary": "Ops",
                                  "html_url": "https://acme.pagerduty.com/ep/P"},
            "start": None, "end": None}
    shift = _oncall(now - timedelta(hours=1), now + timedelta(hours=1))
    for order in ([shift, perm], [perm, shift]):
        _pd_router(poll_mod, monkeypatch, oncalls=order)
        status = {}
        poll_mod.pagerduty_items(sample_cfg, 10, status)
        assert status["schedule"] == "Primary" and status["until"] is not None
        assert status["url"] == "https://acme.pagerduty.com/schedules/PSCHED1"
        assert status["level"] == 1
    # Alone, the permanent target still counts (with its policy link).
    _pd_router(poll_mod, monkeypatch, oncalls=[perm])
    status = {}
    poll_mod.pagerduty_items(sample_cfg, 10, status)
    assert status["schedule"] == "Ops" and status["until"] is None
    assert status["url"] == "https://acme.pagerduty.com/ep/P"


def test_pagerduty_oncall_ignores_deeper_escalation_levels(poll_mod, monkeypatch,
                                                            sample_cfg):
    """Being the level-2/3 fallback on a policy is not "on-call now" (PagerDuty's
    own UI only shows level 1). Such entries produce neither status nor
    reminders by default."""
    now = datetime.now(timezone.utc)
    fallback = {"schedule": None,
                "escalation_policy": {"id": "P", "summary": "[TECH][FE] App-ep"},
                "start": None, "end": None, "escalation_level": 2}
    deep_shift = _oncall(now - timedelta(minutes=2), now + timedelta(hours=8),
                         sched_id="PS3", name="Tertiary")
    deep_shift["escalation_level"] = 3
    _pd_router(poll_mod, monkeypatch, oncalls=[fallback, deep_shift])
    status = {}
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    assert items == []
    assert status["on_call"] is False
    assert status["next_start"] is None


def test_pagerduty_oncall_max_level_configurable(poll_mod, monkeypatch,
                                                 sample_cfg):
    sample_cfg["pagerduty"]["oncall_max_level"] = 2
    now = datetime.now(timezone.utc)
    lvl2 = _oncall(now - timedelta(minutes=2), now + timedelta(hours=8),
                   sched_id="PS2", name="Secondary")
    lvl2["escalation_level"] = 2
    lvl3 = {"schedule": None, "escalation_policy": {"id": "P", "summary": "Ops"},
            "start": None, "end": None, "escalation_level": 3}
    _pd_router(poll_mod, monkeypatch, oncalls=[lvl3, lvl2])
    status = {}
    items = _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status))
    # Level 2 now counts (and is labelled), level 3 still does not.
    assert status["on_call"] is True
    assert status["schedule"] == "Secondary · level 2"
    assert status["level"] == 2
    assert len(items) == 1 and items[0]["subtitle"] == "On-call · Secondary · level 2"


def test_pagerduty_oncall_next_shift_carries_url(poll_mod, monkeypatch, sample_cfg):
    now = datetime.now(timezone.utc)
    oc = _oncall(now + timedelta(hours=5), now + timedelta(hours=13))
    _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    status = {}
    poll_mod.pagerduty_items(sample_cfg, 10, status)
    assert status["on_call"] is False
    assert status["next_url"] == "https://acme.pagerduty.com/schedules/PSCHED1"


@pytest.mark.parametrize("raw,expected", [
    (None, 1), (1, 1), (2, 2), ("3", 3), (0, 1), (-1, 1), ("x", 1), (2.0, 2),
])
def test_pd_oncall_max_level_parsing(poll_mod, raw, expected):
    pd = {} if raw is None else {"oncall_max_level": raw}
    assert poll_mod._pd_oncall_max_level(pd) == expected


@pytest.mark.parametrize("raw,expected", [
    ({}, 1), ({"escalation_level": 2}, 2), ({"escalation_level": "3"}, 3),
    ({"escalation_level": 0}, 1), ({"escalation_level": None}, 1),
    ({"escalation_level": "n/a"}, 1),
])
def test_pd_level_parsing(poll_mod, raw, expected):
    assert poll_mod._pd_level(raw) == expected


def test_pagerduty_oncall_disabled(poll_mod, monkeypatch, sample_cfg):
    sample_cfg["pagerduty"]["oncall_reminders"] = False
    now = datetime.now(timezone.utc)
    calls = _pd_router(poll_mod, monkeypatch,
                       oncalls=[_oncall(now - timedelta(minutes=1), now + timedelta(hours=1))])
    status = {}
    assert _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10, status)) == []
    assert not any(p == "/oncalls" for p, _ in calls)
    assert status["on_call"] is False


def test_pagerduty_oncall_no_reminders_configured(poll_mod, monkeypatch, sample_cfg):
    sample_cfg["pagerduty"]["oncall_remind_before_minutes"] = []
    now = datetime.now(timezone.utc)
    oc = _oncall(now + timedelta(minutes=30), now + timedelta(hours=9))
    _pd_router(poll_mod, monkeypatch, oncalls=[oc])
    assert _oncall_items(poll_mod.pagerduty_items(sample_cfg, 10)) == []


def test_pd_format_helpers(poll_mod):
    assert poll_mod.pd_format_time(None) == ""
    assert poll_mod.pd_format_time("garbage") == ""
    assert poll_mod.pd_format_time("2026-07-01T12:00:00Z")  # some label
    now = datetime.now(timezone.utc)
    assert poll_mod.pd_format_time(now) == now.astimezone().strftime("%a %H:%M")
    assert poll_mod._pd_fmt_minutes(1440) == "1 day"
    assert poll_mod._pd_fmt_minutes(2880) == "2 days"
    assert poll_mod._pd_fmt_minutes(120) == "2h"
    assert poll_mod._pd_fmt_minutes(45) == "45 min"


def test_collect_all_fills_extra_with_pagerduty_status(poll_mod, monkeypatch,
                                                       sample_cfg):
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: [])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg, login=None: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")

    def fake_pd(cfg, w, status=None):
        status["on_call"] = True
        return []

    monkeypatch.setattr(poll_mod, "pagerduty_items", fake_pd)
    extra = {}
    poll_mod.collect_all(sample_cfg, extra=extra)
    assert extra == {"pagerduty": {"on_call": True}, "errors": {}}


# ---------------------------------------------------------------------------
# Per-source error tracking (silent failures made visible)
# ---------------------------------------------------------------------------

def test_collect_all_reports_source_errors_in_extra(poll_mod, monkeypatch,
                                                    sample_cfg):
    """A source that recorded a fetch error shows up in ``extra["errors"]``
    and the *other* sources' items are still returned."""
    def bad_jira(cfg, w):
        poll_mod._record_error("jira", "HTTP 401 unauthorized")
        return []

    monkeypatch.setattr(poll_mod, "jira_items", bad_jira)
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg, login=None: ["g"])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w, status=None: ["p"])
    extra = {}
    phases = poll_mod.collect_all(sample_cfg, extra=extra)
    assert dict(phases)["github"] == ["g"]
    assert dict(phases)["pagerduty"] == ["p"]
    assert extra["errors"] == {"jira": "HTTP 401 unauthorized"}


def test_collect_all_isolates_unexpected_exceptions(poll_mod, monkeypatch,
                                                    sample_cfg):
    """One source blowing up must not abort the poll: it becomes that
    source's error and the rest is still delivered."""
    def boom(cfg, w, status=None):
        raise KeyError("bucket")

    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: ["j"])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg, login=None: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", boom)
    extra = {}
    phases = poll_mod.collect_all(sample_cfg, extra=extra)
    assert dict(phases) == {"jira": ["j"], "github": [], "ci": [],
                            "pagerduty": []}
    assert "KeyError" in extra["errors"]["pagerduty"]


def test_collect_all_skips_github_when_disabled(poll_mod, monkeypatch,
                                                sample_cfg):
    """No ``gh`` login lookup (and no WARN per poll) for non-GitHub users."""
    sample_cfg["github"]["enabled"] = False

    def no_login(cfg):
        raise AssertionError("gh_login must not be called when GitHub is off")

    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: [])
    monkeypatch.setattr(poll_mod, "gh_login", no_login)
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w, status=None: [])
    phases = poll_mod.collect_all(sample_cfg)
    assert dict(phases)["github"] == [] and dict(phases)["ci"] == []


def test_collect_all_resets_errors_between_polls(poll_mod, monkeypatch,
                                                 sample_cfg):
    poll_mod._record_error("jira", "stale from last poll")
    monkeypatch.setattr(poll_mod, "jira_items", lambda cfg, w: [])
    monkeypatch.setattr(poll_mod, "gh_notifications", lambda cfg, login=None: [])
    monkeypatch.setattr(poll_mod, "gh_ci_fallback", lambda cfg, login: [])
    monkeypatch.setattr(poll_mod, "gh_login", lambda cfg: "octocat")
    monkeypatch.setattr(poll_mod, "pagerduty_items", lambda cfg, w, status=None: [])
    extra = {}
    poll_mod.collect_all(sample_cfg, extra=extra)
    assert extra["errors"] == {}


def test_record_error_keeps_first_per_source(poll_mod):
    poll_mod.reset_errors()
    poll_mod._record_error("jira", "first")
    poll_mod._record_error("jira", "second")
    assert poll_mod.poll_errors() == {"jira": "first"}


def _http_error(code, reason):
    import urllib.error
    return lambda: urllib.error.HTTPError("u", code, reason, {}, None)


def _url_error(reason):
    import urllib.error
    return lambda: urllib.error.URLError(reason)


@pytest.mark.parametrize("make,expected", [
    (_http_error(401, "Unauthorized"),
     "HTTP 401 unauthorized — check the API token / username"),
    (_http_error(429, "Too Many"), "HTTP 429 rate limited — will retry next poll"),
    (_http_error(500, "Server Error"), "HTTP 500 Server Error"),
    (lambda: __import__("socket").timeout("t"), "timed out"),
    (_url_error("timed out"), "timed out"),
    (lambda: __import__("urllib.error").error.URLError(__import__("socket").timeout()),
     "timed out"),
    (_url_error("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
     "TLS certificate verification failed (corporate proxy?)"),
    (_url_error("Name or service not known"),
     "network error: Name or service not known"),
    (lambda: __import__("subprocess").TimeoutExpired("gh", 45), "gh timed out"),
    (lambda: ValueError("bad json"), "ValueError: bad json"),
], ids=["401", "429", "500", "socket-timeout", "url-timeout-str",
        "url-timeout-exc", "tls", "dns", "gh-timeout", "other"])
def test_describe_error(poll_mod, make, expected):
    assert poll_mod.describe_error(make()) == expected


def test_jira_search_failure_is_recorded(poll_mod, monkeypatch, sample_cfg):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._jira_search(sample_cfg, 10) == []
    assert poll_mod.poll_errors()["jira"].startswith("HTTP 401")


def test_jira_changelog_failure_is_recorded(poll_mod, monkeypatch, sample_cfg):
    def boom(*a, **k):
        raise OSError("net down")

    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._jira_changelog(sample_cfg, "ACME-1") == []
    assert "jira" in poll_mod.poll_errors()


@pytest.mark.real_myself
def test_jira_myself_failure_without_cache_is_recorded(poll_mod, monkeypatch,
                                                       sample_cfg):
    def boom(*a, **k):
        raise OSError("net down")

    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._jira_myself(sample_cfg) == ""
    assert "jira" in poll_mod.poll_errors()


def test_pd_get_failure_is_recorded(poll_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("net down")

    poll_mod.reset_errors()
    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", boom)
    assert poll_mod._pd_get("tok", "/users/me") == {}
    assert "pagerduty" in poll_mod.poll_errors()


def test_gh_login_failure_recorded_only_when_enabled(poll_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("gh missing")

    monkeypatch.setattr(poll_mod, "_log", lambda m: None)
    monkeypatch.setattr(poll_mod.subprocess, "run", boom)
    monkeypatch.setattr(poll_mod._deps, "gh_path", lambda: "gh")
    monkeypatch.setattr(poll_mod._deps, "augmented_env", lambda: {})
    poll_mod.reset_errors()
    assert poll_mod.gh_login({"github": {"login": "", "enabled": True}}) == ""
    assert "github" in poll_mod.poll_errors()
    poll_mod.reset_errors()
    assert poll_mod.gh_login({"github": {"login": "", "enabled": False}}) == ""
    assert poll_mod.poll_errors() == {}


def test_default_log_prints(poll_mod, capsys):
    # The module-level default _log just prints; exercise it directly.
    import importlib
    importlib.reload(poll_mod)
    poll_mod._log("plain message")
    assert "plain message" in capsys.readouterr().out
