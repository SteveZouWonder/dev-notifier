"""Fetch Jira + GitHub + PagerDuty items relevant to the current user.

Pure data-gathering: returns a list of item dicts. Deduplication and
notification are handled by the caller. Uses the Jira REST API v3 and the
PagerDuty REST API v2 directly, and the ``gh`` CLI for GitHub (so no GitHub
token is stored).

Item contract: ``{"fp", "title", "subtitle", "message", "url"}`` plus an
optional ``"sound"`` (default True) that lets a source ask for a silent
notification (e.g. low-urgency PagerDuty incidents).

@author SteveZou
"""
import base64
import json
import re
import ssl
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import urllib.request

import deps as _deps


def _log(msg: str) -> None:
    # Lazy import to avoid a hard dependency loop; app wires real logging.
    print(msg)


def _ssl_context() -> ssl.SSLContext:
    """Return an SSL context that can verify public CAs.

    The stock macOS ``python.org`` / system Python does not read the system
    keychain, so ``urlopen`` falls back to a CA bundle that often cannot verify
    Atlassian/PagerDuty certificate chains (CERTIFICATE_VERIFY_FAILED). Prefer
    ``certifi``'s bundle when available; otherwise use the default context so
    packaged/CI environments without certifi still work.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi missing or unreadable bundle
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

# Jira timestamps come back like "2026-07-05T20:57:15.858-0400" — an offset
# *without* a colon, which datetime.fromisoformat() rejects on Python < 3.11.
_TZ_FIX = re.compile(r"([+-]\d{2})(\d{2})$")


def _parse_jira_dt(s):
    """Parse a Jira timestamp to an aware UTC datetime; ``None`` on failure.

    Handles a trailing ``Z``, offsets without a colon (``-0400``) and with a
    colon (``-04:00``), and naive timestamps (treated as UTC). Always returns a
    UTC-normalized aware datetime so callers can compare safely.
    """
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    s = _TZ_FIX.sub(r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _jira_search(cfg: dict, window_min: int) -> list:
    jira = cfg.get("jira", {})
    base = jira.get("base_url", "").rstrip("/")
    user = jira.get("username", "")
    token = jira.get("api_token", "")
    if not (base and user and token):
        return []
    jql = (
        "(assignee = currentUser() OR reporter = currentUser() "
        "OR watcher = currentUser()) "
        f'AND updated >= "-{window_min}m" ORDER BY updated DESC'
    )
    url = f"{base}/rest/api/3/search/jql"
    payload = json.dumps({
        "jql": jql,
        "maxResults": 50,
        "fields": ["summary", "status", "updated", "comment"],
        # Inline changelog so event mode needs no extra request per issue
        # (must be a string; passing a list returns HTTP 400).
        "expand": "changelog",
    }).encode()
    b64 = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode()).get("issues", [])
    except Exception as e:  # noqa: BLE001
        _log(f"ERROR jira_search: {e}")
        return []


def _jira_myself(cfg: dict) -> str:
    """Return the current Jira user's ``accountId`` (empty string on failure).

    Used to suppress notifications for changes/comments the user made
    themselves. ``accountId`` is compared rather than the display name because
    display names are not unique and are not present on every payload.
    """
    jira = cfg.get("jira", {})
    base = jira.get("base_url", "").rstrip("/")
    user = jira.get("username", "")
    token = jira.get("api_token", "")
    if not (base and user and token):
        return ""
    url = f"{base}/rest/api/3/myself"
    b64 = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode()).get("accountId", "")
    except Exception as e:  # noqa: BLE001
        _log(f"ERROR jira_myself: {e}")
        return ""


def _jira_changelog(cfg: dict, key: str) -> list:
    """Fetch the full changelog for one issue (used when the inline copy in the
    search response is paginated/truncated). Returns a list of history dicts.
    """
    jira = cfg.get("jira", {})
    base = jira.get("base_url", "").rstrip("/")
    user = jira.get("username", "")
    token = jira.get("api_token", "")
    if not (base and user and token):
        return []
    url = f"{base}/rest/api/3/issue/{key}/changelog?maxResults=100"
    b64 = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode()).get("values", [])
    except Exception as e:  # noqa: BLE001
        _log(f"ERROR jira_changelog {key}: {e}")
        return []


def _format_change(field: str, from_str, to_str) -> str:
    """Human-readable one-liner for a changelog field change."""
    frm = from_str if from_str else "∅"
    to = to_str if to_str else "∅"
    return f"{field}: {frm} → {to}"


def _issue_histories(issue: dict, cfg: dict) -> list:
    """Return the changelog histories for an issue, fetching the full log when
    the inline copy from the search response was paginated.
    """
    cl = issue.get("changelog") or {}
    histories = cl.get("histories", [])
    total = cl.get("total")
    if total is not None and total > len(histories):
        full = _jira_changelog(cfg, issue.get("key", ""))
        if full:
            return full
    return histories


def _events_from_changelog(issue, cfg, window_start, whitelist, self_id=""):
    """Yield event dicts for whitelisted changelog field changes in-window.

    When ``self_id`` is set, changes authored by the current user (matched on
    ``author.accountId``) are skipped so self-triggered edits do not notify.
    """
    key = issue.get("key", "")
    f = issue.get("fields", {})
    summary = f.get("summary", "")
    status = (f.get("status") or {}).get("name", "")
    events = []
    for h in _issue_histories(issue, cfg):
        ts = _parse_jira_dt(h.get("created", ""))
        if ts is None or ts < window_start:
            continue
        author_obj = h.get("author") or {}
        if self_id and author_obj.get("accountId", "") == self_id:
            continue
        author = author_obj.get("displayName", "")
        for it in h.get("items", []):
            field = it.get("field", "")
            if field not in whitelist:
                continue
            events.append({
                "key": key, "summary": summary, "status": status,
                "kind": field, "event_id": f"cl:{h.get('id', '')}:{field}",
                "ts": ts, "author": author,
                "text": _format_change(field, it.get("fromString"),
                                        it.get("toString")),
            })
    return events


def _events_from_comments(issue, cfg, window_start, self_id=""):
    """Yield one event dict per in-window comment on the issue.

    When ``self_id`` is set, comments authored by the current user (matched on
    ``author.accountId``) are skipped so your own comments do not notify.
    """
    key = issue.get("key", "")
    f = issue.get("fields", {})
    summary = f.get("summary", "")
    status = (f.get("status") or {}).get("name", "")
    user = cfg.get("jira", {}).get("username", "")
    handle = user.split("@")[0].lower() if "@" in user else user.lower()
    events = []
    for c in (f.get("comment", {}) or {}).get("comments", []):
        ts = _parse_jira_dt(c.get("created", ""))
        if ts is None or ts < window_start:
            continue
        if self_id and (c.get("author") or {}).get("accountId", "") == self_id:
            continue
        body = json.dumps(c.get("body", ""))
        mentioned = (handle and handle in body.lower()) or "mention" in body
        events.append({
            "key": key, "summary": summary, "status": status,
            "kind": "comment", "event_id": f"comment:{c.get('id', '')}",
            "ts": ts,
            "author": (c.get("author") or {}).get("displayName", ""),
            "text": "mentioned you" if mentioned else "commented",
        })
    return events


def _event_to_item(ev: dict, base: str) -> dict:
    """Map an internal event dict to the notification item contract."""
    key = ev["key"]
    author = f" by {ev['author']}" if ev.get("author") else ""
    return {
        "fp": f"jira:{key}:{ev['event_id']}",
        "title": "Jira",
        "subtitle": f"{key} · {ev['status']}",
        "message": f"[{ev['kind']}] {ev['text']}{author} — {ev['summary']}",
        "url": f"{base}/browse/{key}",
    }


def _jira_items_events(cfg: dict, window_min: int, issues: list) -> list:
    """Event-level Jira items: one notification per changelog change / comment,
    matching Jira's notification-feed granularity.
    """
    jira = cfg.get("jira", {})
    base = jira.get("base_url", "").rstrip("/")
    whitelist = set(jira.get("event_fields", ["status", "assignee"]))
    window_start = datetime.now(timezone.utc) - timedelta(minutes=window_min)
    # Resolve the current user's accountId once so self-triggered changes/
    # comments can be suppressed (only when suppress_self is enabled).
    self_id = _jira_myself(cfg) if jira.get("suppress_self", True) else ""
    events = []
    for issue in issues:
        events += _events_from_changelog(issue, cfg, window_start, whitelist,
                                         self_id)
        events += _events_from_comments(issue, cfg, window_start, self_id)
    # Oldest first for stable notification ordering.
    events.sort(key=lambda e: e["ts"])
    return [_event_to_item(ev, base) for ev in events]


def _jira_items_legacy(cfg: dict, window_min: int, issues: list) -> list:
    """Legacy issue-level behaviour: one notification per updated issue."""
    jira = cfg.get("jira", {})
    base = jira.get("base_url", "").rstrip("/")
    user = jira.get("username", "")
    window_start = datetime.now(timezone.utc) - timedelta(minutes=window_min)
    items = []
    for issue in issues:
        key = issue.get("key", "")
        f = issue.get("fields", {})
        summary = f.get("summary", "")
        status = (f.get("status") or {}).get("name", "")
        updated = f.get("updated", "")
        mentioned = False
        for c in (f.get("comment", {}) or {}).get("comments", []):
            cdt = _parse_jira_dt(c.get("created", ""))
            if cdt is None or cdt < window_start:
                continue
            body = json.dumps(c.get("body", ""))
            handle = user.split("@")[0].lower() if "@" in user else user.lower()
            if (handle and handle in body.lower()) or "mention" in body:
                mentioned = True
        reason = "comment mention" if mentioned else "updated"
        items.append({
            "fp": f"jira:{key}:{updated}",
            "title": "Jira",
            "subtitle": f"{key} · {status}",
            "message": f"[{reason}] {summary}",
            "url": f"{base}/browse/{key}",
        })
    return items


def jira_items(cfg: dict, window_min: int) -> list:
    jira = cfg.get("jira", {})
    if not jira.get("enabled"):
        return []
    issues = _jira_search(cfg, window_min)
    if jira.get("event_mode", True):
        return _jira_items_events(cfg, window_min, issues)
    return _jira_items_legacy(cfg, window_min, issues)


# ---------------------------------------------------------------------------
# GitHub (via gh CLI)
# ---------------------------------------------------------------------------

def _gh_json(args: list):
    try:
        out = subprocess.run(
            [_deps.gh_path()] + args, check=True, capture_output=True,
            text=True, timeout=45, env=_deps.augmented_env(),
        ).stdout
        return json.loads(out) if out.strip() else []
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()
        if "no checks reported" not in err:
            _log(f"ERROR gh {' '.join(args)}: {err[:200]}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, FileNotFoundError) as e:
        _log(f"ERROR gh {' '.join(args)}: {e}")
    return []


def gh_login(cfg: dict) -> str:
    login = cfg.get("github", {}).get("login", "")
    if login:
        return login
    # `gh api user --jq .login` returns a bare string (not JSON), so call it
    # raw rather than through _gh_json (which would try json.loads and fail).
    try:
        out = subprocess.run(
            [_deps.gh_path(), "api", "user", "--jq", ".login"],
            check=True, capture_output=True, text=True, timeout=30,
            env=_deps.augmented_env(),
        ).stdout.strip()
        return out
    except Exception:  # noqa: BLE001
        return ""


REASON_LABEL = {
    "review_requested": "review requested",
    "mention": "mentioned you",
    "assign": "assigned to you",
    "ci_activity": "CI status",
    "comment": "new comment",
    "state_change": "state change",
    "author": "your PR updated",
}
RELEVANT_REASONS = set(REASON_LABEL)  # excludes 'subscribed' (noise)


def _html_url(api_url: str) -> str:
    if not api_url:
        return ""
    u = api_url.replace("https://api.github.com/repos/", "https://github.com/")
    return u.replace("/pulls/", "/pull/")


def _gh_latest_actor(subject: dict) -> str:
    """Return the login of whoever produced a thread's latest activity.

    Reads ``subject.latest_comment_url`` (the API URL of the most recent
    comment/review/commit) and returns its ``user.login``. Returns "" when the
    URL is missing or the lookup fails, so callers fall back to notifying.
    """
    url = (subject or {}).get("latest_comment_url") or ""
    if not url:
        return ""
    # gh api expects a path or full URL; pass the full URL through as-is.
    data = _gh_json(["api", url])
    if not isinstance(data, dict):
        return ""
    return (data.get("user") or {}).get("login", "")


def gh_notifications(cfg: dict, login: str = "") -> list:
    gh = cfg.get("github", {})
    if not gh.get("enabled"):
        return []
    notifs = _gh_json(["api", "notifications"])
    # Only look up the latest actor when we can compare it to a known login.
    suppress_self = gh.get("suppress_self", True) and bool(login)
    items = []
    for n in notifs:
        reason = n.get("reason", "")
        if reason not in RELEVANT_REASONS:
            continue
        subj = n.get("subject", {}) or {}
        # Suppress threads whose only latest activity was triggered by you
        # (e.g. reason "author" when you just pushed/commented on your own PR).
        if suppress_self and _gh_latest_actor(subj) == login:
            continue
        repo = (n.get("repository", {}) or {}).get("full_name", "")
        api_url = subj.get("url") or ""
        if api_url:
            url = _html_url(api_url)
        elif repo:
            url = f"https://github.com/{repo}/pulls"
        else:
            url = "https://github.com/notifications"
        items.append({
            # Fingerprint on the thread id only. GitHub bumps ``updated_at`` on
            # every new activity in a thread (comment, push, re-request), so
            # including it would mint a fresh fingerprint each time and re-notify
            # the same thread repeatedly. The id is stable for the life of the
            # notification thread, so one notification per thread is emitted.
            "fp": f"gh-notif:{n.get('id','')}",
            "title": "GitHub",
            "subtitle": f"{repo} · {REASON_LABEL.get(reason, reason)}",
            "message": subj.get("title", ""),
            "url": url,
        })
    return items


def _ci_rollup_for_pr(pr: dict):
    url = pr.get("url", "")
    m = re.match(r"https://github.com/([^/]+/[^/]+)/pull/(\d+)", url or "")
    if not m:
        return None
    repo, num = m.group(1), m.group(2)
    checks = _gh_json(["pr", "checks", num, "--repo", repo, "--json", "state,bucket"])
    buckets = [c.get("bucket", "") for c in checks] if checks else []
    if "fail" in buckets:
        rollup, emoji = "fail", "\u274c"
    elif "pending" in buckets:
        rollup, emoji = "pending", "\u23f3"
    elif buckets:
        rollup, emoji = "pass", "\u2705"
    else:
        return None
    return {
        "fp": f"gh-ci:{repo}#{num}:{rollup}",
        "title": "GitHub CI",
        "subtitle": f"{repo} · PR #{num}",
        "message": f"{emoji} CI {rollup}: {pr.get('title', '')}",
        "url": url,
        "ci_only": True,
        "ci_rollup": rollup,
    }


def gh_ci_fallback(cfg: dict, login: str) -> list:
    if not (cfg.get("github", {}).get("enabled") and login):
        return []
    prs = _gh_json([
        "search", "prs", f"--author={login}", "--state=open",
        "--json", "title,url,number,repository", "--limit", "30",
    ])
    if not prs:
        return []
    items = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(_ci_rollup_for_pr, prs):
            if result:
                items.append(result)
    return items


# ---------------------------------------------------------------------------
# PagerDuty (REST API v2)
# ---------------------------------------------------------------------------
#
# Two kinds of items are produced:
#
# * Incident **events**, sourced from ``/log_entries`` (the incident timeline)
#   instead of polling incident status. Every timeline entry has a stable id,
#   so the fingerprint is exact: each acknowledge / resolve / escalate /
#   reassign / note / priority change notifies exactly once and says who did
#   it. Reassignments and escalations *to you* are detected from the entry's
#   assignees, which the old status-timestamp fingerprint could not see.
#
# * **On-call** shift reminders, sourced from ``/oncalls``: a heads-up before
#   your shift, when it starts, and when it ends. The current/next shift is
#   also exposed to the app (via ``collect_all(extra=...)``) for the menu.

_PD_API = "https://api.pagerduty.com"
_PD_ACTIVE_STATUSES = ("triggered", "acknowledged")
_PD_PAGE_SIZE = 100
_PD_PROFILE_TTL_S = 6 * 3600  # re-fetch /users/me at most every 6 hours
_PD_MAX_PER_INCIDENT_FETCH = 10  # cap on per-incident timeline requests/poll
_PD_TRIGGER_ASSIGN_MERGE_S = 5  # trigger + initial assign within N s = one event
_pd_profile_cache = {}  # api_token -> (fetched_at_epoch, /users/me user dict)

# Timeline entry type -> human label. Types not listed here are ignored (the
# very chatty ``notify_log_entry`` / ``repeat_escalation_path_log_entry``,
# link/unlink entries, ...).
_PD_EVENT_LABELS = {
    "trigger_log_entry": "triggered",
    "acknowledge_log_entry": "acknowledged",
    "unacknowledge_log_entry": "unacknowledged (ack timed out)",
    "resolve_log_entry": "resolved",
    "escalate_log_entry": "escalated",
    "assign_log_entry": "assigned",
    "reassign_log_entry": "reassigned",
    "annotate_log_entry": "note",
    "priority_change_log_entry": "priority changed",
    "urgency_change_log_entry": "urgency changed",
    "snooze_log_entry": "snoozed",
    "responder_request_log_entry": "responder requested",
    "exhaust_escalation_path_log_entry":
        "escalation exhausted — nobody acknowledged",
}
# Entries that carry ``assignees`` (who the incident went to).
_PD_ASSIGN_TYPES = {"assign_log_entry", "reassign_log_entry",
                    "escalate_log_entry", "trigger_log_entry"}
# Entries whose PagerDuty-provided summary is the most useful text
# (e.g. "Changed the priority to P1").
_PD_SUMMARY_TYPES = {"priority_change_log_entry", "urgency_change_log_entry",
                     "snooze_log_entry"}

_parse_dt = _parse_jira_dt  # generic ISO-8601 parser, works for PagerDuty too


def _pd_get(token: str, path: str, params=None):
    """GET a PagerDuty REST API v2 endpoint; return the parsed JSON dict.

    ``params`` is a list of (key, value) pairs (list allows repeated keys such
    as ``user_ids[]``). Returns ``{}`` on any network/parse error.
    """
    from urllib.parse import urlencode

    url = f"{_PD_API}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Token token={token}",
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001
        _log(f"ERROR pagerduty GET {path}: {e}")
        return {}


def _pd_get_all(token: str, path: str, params, key: str, max_pages: int = 5):
    """Follow PagerDuty's ``limit``/``offset``/``more`` pagination.

    Returns the concatenated ``key`` lists from up to ``max_pages`` pages.
    """
    out = []
    offset = 0
    for _ in range(max_pages):
        page = _pd_get(token, path, list(params) + [
            ("limit", str(_PD_PAGE_SIZE)), ("offset", str(offset))])
        out.extend(page.get(key, []) or [])
        if not page.get("more"):
            break
        offset += _PD_PAGE_SIZE
    return out


def pd_profile(cfg: dict, now_ts=None) -> dict:
    """Resolve who "you" are in PagerDuty.

    Returns ``{"user_id", "team_ids", "name", "email"}``. Configured
    ``user_id``/``team_ids`` win; anything missing is filled from
    ``GET /users/me``, which is cached per token for ``_PD_PROFILE_TTL_S`` so
    identity is not re-fetched on every poll.
    """
    pd = cfg.get("pagerduty", {})
    token = pd.get("api_token", "")
    prof = {
        "user_id": pd.get("user_id", "") or "",
        "team_ids": list(pd.get("team_ids", []) or []),
        "name": "",
        "email": "",
    }
    if not token:
        return prof
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()
    cached = _pd_profile_cache.get(token)
    if cached and now_ts - cached[0] < _PD_PROFILE_TTL_S:
        me = cached[1]
    else:
        me = (_pd_get(token, "/users/me") or {}).get("user", {}) or {}
        if me.get("id"):
            _pd_profile_cache[token] = (now_ts, me)
    if not prof["user_id"]:
        prof["user_id"] = me.get("id", "") or ""
    if not prof["team_ids"]:
        prof["team_ids"] = [t.get("id", "") for t in me.get("teams", [])
                            if t.get("id")]
    prof["name"] = me.get("name", "") or ""
    prof["email"] = me.get("email", "") or ""
    return prof


def pd_identity(cfg: dict):
    """Resolve (user_id, team_ids) for PagerDuty.

    Uses configured values when present (no network); otherwise auto-detects
    via the cached ``pd_profile``. Returns ("", []) on failure.
    """
    pd = cfg.get("pagerduty", {})
    user_id = pd.get("user_id", "")
    team_ids = list(pd.get("team_ids", []) or [])
    if user_id and team_ids:
        return user_id, team_ids
    prof = pd_profile(cfg)
    return prof["user_id"], prof["team_ids"]


# -- incident events --------------------------------------------------------

def _pd_targets(entry: dict) -> list:
    """User refs an entry was directed at (assignees / requested responders)."""
    out = []
    for key in ("assignees", "responders"):
        for t in entry.get(key) or []:
            if isinstance(t, dict):
                out.append(t.get("user") if isinstance(t.get("user"), dict) else t)
    r = entry.get("responder")
    if isinstance(r, dict):
        out.append(r.get("user") if isinstance(r.get("user"), dict) else r)
    return out


def _pd_note_text(entry: dict) -> str:
    ch = entry.get("channel") or {}
    return (ch.get("summary") or ch.get("content") or ch.get("details")
            or entry.get("summary") or "")


def _pd_my_incidents(token: str, user_id: str, window_start) -> list:
    """Incidents assigned to me: every open one plus those resolved in-window.

    Resolved incidents are sorted by ``resolved_at`` (not creation time) so an
    old incident resolved just now is still found.
    """
    if not user_id:
        return []
    params = [("user_ids[]", user_id), ("sort_by", "created_at:desc")]
    for st in _PD_ACTIVE_STATUSES:
        params.append(("statuses[]", st))
    mine = _pd_get_all(token, "/incidents", params, "incidents")
    params = [("user_ids[]", user_id), ("statuses[]", "resolved"),
              ("sort_by", "resolved_at:desc")]
    for inc in _pd_get_all(token, "/incidents", params, "incidents",
                           max_pages=1):
        ts = _parse_dt(inc.get("resolved_at") or inc.get("last_status_change_at"))
        if ts is not None and ts >= window_start:
            mine.append(inc)
    return mine


def _pd_collapse_trigger_assign(entries: list) -> list:
    """Merge the initial ``assign`` entry into its ``trigger`` entry.

    A new incident produces a trigger entry immediately followed by an assign
    entry (the escalation policy's first level). Notifying both would be two
    pop-ups for one event, so the assignees are folded into the trigger and
    the assign entry is dropped.
    """
    triggers = {}
    for e in entries:
        if e.get("type") == "trigger_log_entry":
            triggers[(e.get("incident") or {}).get("id", "")] = e
    out = []
    for e in entries:
        if e.get("type") == "assign_log_entry":
            trig = triggers.get((e.get("incident") or {}).get("id", ""))
            if trig is not None:
                t0 = _parse_dt(trig.get("created_at"))
                t1 = _parse_dt(e.get("created_at"))
                if t0 is not None and t1 is not None and \
                        abs((t1 - t0).total_seconds()) <= _PD_TRIGGER_ASSIGN_MERGE_S:
                    trig.setdefault("assignees", e.get("assignees") or [])
                    continue
        out.append(e)
    return out


def _pd_event_item(entry: dict, prof: dict, my_ids: set, pd: dict):
    """Map one timeline entry to a notification item, or ``None`` to skip."""
    etype = entry.get("type", "")
    label = _PD_EVENT_LABELS.get(etype)
    if not label:
        return None
    inc = entry.get("incident") or {}
    iid = inc.get("id", "")
    user_id = prof.get("user_id", "")
    agent = entry.get("agent") or {}
    agent_is_user = str(agent.get("type", "")).startswith("user")
    if pd.get("suppress_self", True) and user_id and agent_is_user \
            and agent.get("id", "") == user_id:
        return None  # you did this yourself

    targets = _pd_targets(entry)
    to_me = bool(user_id) and any(t.get("id", "") == user_id for t in targets)
    names = ", ".join(("you" if t.get("id", "") == user_id
                       else (t.get("summary") or t.get("name") or "?"))
                      for t in targets)

    text = label
    if etype in _PD_ASSIGN_TYPES and to_me:
        reason = "assigned to you"
        text = (f"triggered, assigned to {names}" if etype == "trigger_log_entry"
                else f"{label} to {names}")
    elif etype == "responder_request_log_entry" and to_me:
        reason = "responder requested"
        text = "you were requested as a responder"
    elif etype == "annotate_log_entry" and _pd_mentions_me(entry, prof):
        reason = "mentioned you"
        text = f"note: {_pd_note_text(entry)[:120]}"
    elif iid in my_ids:
        reason = "your incident"
    else:
        if not pd.get("notify_team_incidents", True):
            return None
        reason = "team incident"

    if reason not in ("assigned to you", "responder requested", "mentioned you"):
        if etype in _PD_ASSIGN_TYPES and names:
            text = f"{label} to {names}"
        elif etype == "annotate_log_entry":
            text = f"note: {_pd_note_text(entry)[:120]}"
        elif etype in _PD_SUMMARY_TYPES and entry.get("summary"):
            text = entry["summary"]
    by = f" by {agent.get('summary')}" if agent_is_user and agent.get("summary") \
        else ""

    num = inc.get("incident_number", "")
    status = inc.get("status", "")
    service = (inc.get("service", {}) or {}).get("summary", "")
    urgency = inc.get("urgency", "") or ""
    subtitle = f"#{num} · {status}" + (f" · {service}" if service else "")
    if urgency == "low":
        subtitle += " · low urgency"
    return {
        "fp": f"pd:{iid}:{entry.get('id', '')}",
        "title": "PagerDuty",
        "subtitle": subtitle,
        "message": f"[{reason}] {text}{by} — {inc.get('title', '')}",
        "url": inc.get("html_url", ""),
        # Low-urgency incidents notify silently unless the user opts in.
        "sound": not (urgency == "low" and not pd.get("low_urgency_sound", False)),
    }


def _pd_mentions_me(entry: dict, prof: dict) -> bool:
    text = _pd_note_text(entry).lower()
    if not text:
        return False
    for probe in (prof.get("name", ""), prof.get("email", "")):
        if probe and probe.lower() in text:
            return True
    return False


def _pd_incident_events(pd: dict, token: str, prof: dict, window_start, now):
    """Return (items, my_active_incidents) for the poll window."""
    user_id = prof["user_id"]
    team_ids = prof["team_ids"]
    since, until = window_start.isoformat(), now.isoformat()

    entries = []
    if team_ids:
        params = [("since", since), ("until", until),
                  ("is_overview", "false"), ("include[]", "incidents")]
        for tid in team_ids:
            params.append(("team_ids[]", tid))
        entries += _pd_get_all(token, "/log_entries", params, "log_entries")

    mine = _pd_my_incidents(token, user_id, window_start)
    my_ids = {inc.get("id", "") for inc in mine}
    team_set = set(team_ids)
    fetched = 0
    for inc in mine:
        # Incidents on my teams are already covered by the team timeline.
        if any((t or {}).get("id", "") in team_set for t in inc.get("teams") or []):
            continue
        if fetched >= _PD_MAX_PER_INCIDENT_FETCH:
            break
        fetched += 1
        params = [("since", since), ("until", until), ("is_overview", "false")]
        for e in _pd_get_all(token, f"/incidents/{inc.get('id', '')}/log_entries",
                             params, "log_entries", max_pages=2):
            e["incident"] = inc  # full incident (status/urgency/service)
            entries.append(e)

    # De-dupe by entry id (an incident may appear via several teams).
    uniq = {}
    for e in entries:
        uniq.setdefault(e.get("id", ""), e)
    entries = _pd_collapse_trigger_assign(list(uniq.values()))
    entries.sort(key=lambda e: e.get("created_at", ""))

    items = []
    for e in entries:
        it = _pd_event_item(e, prof, my_ids, pd)
        if it:
            items.append(it)
    active = [inc for inc in mine if inc.get("status") in _PD_ACTIVE_STATUSES]
    return items, active


# -- on-call shifts ---------------------------------------------------------

def pd_format_time(value) -> str:
    """Short local-time label ("Thu 18:00") for an ISO string or datetime."""
    dt = value if isinstance(value, datetime) else _parse_dt(value)
    if dt is None:
        return ""
    return dt.astimezone().strftime("%a %H:%M")


def _pd_fmt_minutes(m: int) -> str:
    if m % 1440 == 0:
        d = m // 1440
        return f"{d} day" if d == 1 else f"{d} days"
    if m % 60 == 0:
        return f"{m // 60}h"
    return f"{m} min"


def _pd_oncall_item(fp: str, name: str, text: str, url: str, sound=True) -> dict:
    return {
        "fp": fp,
        "title": "PagerDuty",
        "subtitle": f"On-call · {name}",
        "message": f"[on-call] {text}",
        "url": url,
        "sound": sound,
    }


def _pd_oncall_items(pd: dict, token: str, user_id: str, window_start, now,
                     status=None):
    """Shift reminders (before / start / end) + current & next shift summary.

    ``status`` (if given) is filled with ``on_call``, ``until``, ``schedule``,
    ``next_start`` and ``next_schedule`` for the menu.
    """
    if status is not None:
        status.update({"on_call": False, "until": None, "schedule": "",
                       "next_start": None, "next_schedule": ""})
    if not user_id or not pd.get("oncall_reminders", True):
        return []
    remind = []
    for m in pd.get("oncall_remind_before_minutes", [1440, 60]) or []:
        try:
            m = int(m)
        except (TypeError, ValueError):
            continue
        if m > 0 and m not in remind:
            remind.append(m)
    remind.sort(reverse=True)
    horizon = (remind[0] if remind else 0) + 60
    params = [("user_ids[]", user_id), ("time_zone", "UTC"),
              ("since", window_start.isoformat()),
              ("until", (now + timedelta(minutes=horizon)).isoformat())]
    oncalls = _pd_get_all(token, "/oncalls", params, "oncalls", max_pages=2)

    items = []
    seen = set()
    current = None   # (end_dt or None, name)
    upcoming = None  # (start_dt, name)
    for oc in oncalls:
        sched = oc.get("schedule") or {}
        pol = oc.get("escalation_policy") or {}
        name = sched.get("summary") or pol.get("summary") or "on-call"
        url = sched.get("html_url") or pol.get("html_url") or ""
        start = _parse_dt(oc.get("start"))
        end = _parse_dt(oc.get("end"))
        if start is None or end is None:
            # Directly on an escalation policy level: always on-call.
            if current is None:
                current = (None, name)
            continue
        key = (sched.get("id") or pol.get("id") or "", oc.get("start"))
        if key in seen:
            continue
        seen.add(key)
        if start <= now < end and (current is None or
                                   (current[0] is not None and end > current[0])):
            current = (end, name)
        elif start > now and (upcoming is None or start < upcoming[0]):
            upcoming = (start, name)

        base = f"pd-oncall:{key[0]}:{oc.get('start', '')}"
        span = f"{pd_format_time(start)} – {pd_format_time(end)}"
        if window_start < start <= now:
            items.append(_pd_oncall_item(
                f"{base}:start", name, f"Your on-call shift started ({span})", url))
        elif start > now:
            for m in remind:
                at = start - timedelta(minutes=m)
                if window_start < at <= now:
                    items.append(_pd_oncall_item(
                        f"{base}:before:{m}", name,
                        f"You go on-call in {_pd_fmt_minutes(m)} ({span})", url))
                    break  # only the nearest due reminder
        if window_start < end <= now:
            items.append(_pd_oncall_item(
                f"{base}:end", name, f"Your on-call shift ended ({span})", url,
                sound=False))

    if status is not None:
        if current is not None:
            status["on_call"] = True
            status["until"] = current[0].isoformat() if current[0] else None
            status["schedule"] = current[1]
        if upcoming is not None:
            status["next_start"] = upcoming[0].isoformat()
            status["next_schedule"] = upcoming[1]
    return items


def pagerduty_items(cfg: dict, window_min: int, status=None) -> list:
    """Collect PagerDuty items: incident timeline events + on-call reminders.

    ``status`` (optional dict) receives the on-call summary and
    ``active_incidents`` (open incidents assigned to you) for the menu.
    """
    pd = cfg.get("pagerduty", {})
    if not pd.get("enabled"):
        return []
    token = pd.get("api_token", "")
    if not token:
        return []
    prof = pd_profile(cfg)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_min)

    items, active = _pd_incident_events(pd, token, prof, window_start, now)
    items += _pd_oncall_items(pd, token, prof["user_id"], window_start, now,
                              status)
    if status is not None:
        status["active_incidents"] = [{
            "number": inc.get("incident_number", ""),
            "status": inc.get("status", ""),
            "urgency": inc.get("urgency", ""),
            "title": inc.get("title", ""),
            "url": inc.get("html_url", ""),
        } for inc in active]
    return items


def resolve_window_minutes(cfg: dict, since_ts=None, now_ts=None) -> int:
    """Compute the lookback window (in minutes) for this poll.

    The window normally spans from the previous poll (``since_ts``, epoch
    seconds) to ``now``, so no update between polls is ever missed regardless of
    the poll interval. It is capped at ``poll.max_window_minutes`` (default 7
    days) so a long sleep/shutdown doesn't fetch an unbounded backlog. When
    ``since_ts`` is missing (first run), fall back to ``poll.window_minutes``
    (default 24 hours).
    """
    poll_cfg = cfg.get("poll", {})
    fallback = poll_cfg.get("window_minutes", 1440)
    cap = poll_cfg.get("max_window_minutes", 10080)
    if not since_ts:
        span = fallback
    else:
        now_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        span = (now_ts - since_ts) / 60.0
        if span < 1:
            span = 1  # never look back less than a minute (clock jitter)
    window = min(span, cap)
    return int(round(window))


def collect_all(cfg: dict, log=None, since_ts=None, extra=None):
    """Yield item lists in priority order (fast sources first).

    Returns a list of (phase_name, items) so the caller can notify
    incrementally. `log` is an optional callable(str). `since_ts` is the epoch
    timestamp of the previous poll; the lookback window spans from it to now
    (capped by ``poll.max_window_minutes``). See ``resolve_window_minutes``.

    ``extra`` (optional dict) receives non-notification side data for the
    menu: ``extra["pagerduty"]`` = on-call summary + open incidents assigned
    to you (see ``pagerduty_items``).
    """
    global _log
    if log:
        _log = log
    window_min = resolve_window_minutes(cfg, since_ts)
    _log(f"poll window: {window_min} min")
    login = gh_login(cfg)
    pd_status = {}
    phases = [
        ("jira", jira_items(cfg, window_min)),
        ("github", gh_notifications(cfg, login)),
        ("ci", gh_ci_fallback(cfg, login)),
        ("pagerduty", pagerduty_items(cfg, window_min, status=pd_status)),
    ]
    if extra is not None:
        extra["pagerduty"] = pd_status
    return phases
