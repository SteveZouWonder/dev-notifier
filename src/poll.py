"""Fetch Jira + GitHub + PagerDuty items relevant to the current user.

Pure data-gathering: returns a list of item dicts. Deduplication and
notification are handled by the caller. Uses the Jira REST API v3 and the
PagerDuty REST API v2 directly, and the ``gh`` CLI for GitHub (so no GitHub
token is stored).

Item contract: ``{"fp", "title", "subtitle", "message", "url"}`` plus an
optional ``"sound"`` (default True) that lets a source ask for a silent
notification (e.g. low-urgency PagerDuty incidents), and an optional
``"quiet"`` (default False) that asks the app to remember the item (mark it
seen) without notifying at all (e.g. a passing/pending CI roll-up).

@author SteveZou
"""
import base64
import hashlib
import json
import re
import socket
import ssl
import subprocess
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import urllib.request

import deps as _deps


def _log(msg: str) -> None:
    # Lazy import to avoid a hard dependency loop; app wires real logging.
    print(msg)


# ---------------------------------------------------------------------------
# Per-poll error tracking
# ---------------------------------------------------------------------------
#
# Every fetch helper used to swallow its exception and return an empty result,
# which made "the token is wrong" indistinguishable from "nothing happened".
# Failures are now *also* recorded here (first error per source wins) so that
# ``collect_all`` can hand them to the app via ``extra["errors"]``. The app uses
# that to (a) show the problem in the Status menu, (b) tell the user on a
# manual check, and (c) refuse to advance the poll cursor, so the events in
# the failed window are fetched again next time instead of being lost.

_errors = {}  # source ("jira" | "github" | "pagerduty") -> human-readable error

_HTTP_HINTS = {
    401: "unauthorized — check the API token / username",
    403: "forbidden — token lacks permission or is rate-limited",
    404: "not found — check base_url (Jira Cloud only)",
    429: "rate limited — will retry next poll",
}


def describe_error(e) -> str:
    """Short, user-facing description of a fetch exception."""
    if isinstance(e, urllib.error.HTTPError):
        hint = _HTTP_HINTS.get(e.code)
        return f"HTTP {e.code}" + (f" {hint}" if hint else f" {e.reason}")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timed out"
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        text = str(reason)
        if "CERTIFICATE_VERIFY_FAILED" in text:
            return "TLS certificate verification failed (corporate proxy?)"
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
            return "timed out"
        return f"network error: {text}"[:120]
    if isinstance(e, subprocess.TimeoutExpired):
        return "gh timed out"
    return f"{type(e).__name__}: {e}"[:120]


def _record_error(source: str, e) -> None:
    """Remember the first failure for ``source`` in this poll."""
    text = e if isinstance(e, str) else describe_error(e)
    _errors.setdefault(source, text)


def reset_errors() -> None:
    _errors.clear()


def poll_errors() -> dict:
    """Errors recorded since the last ``reset_errors()`` (source -> text)."""
    return dict(_errors)


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
        _record_error("jira", e)
        return []


_JIRA_MYSELF_TTL_S = 6 * 3600  # re-fetch /myself at most every 6 hours
_jira_myself_cache = {}  # (base_url, username) -> (fetched_at_epoch, accountId)


def _jira_myself(cfg: dict) -> str:
    """Return the current Jira user's ``accountId`` (empty string on failure).

    Used to suppress notifications for changes/comments the user made
    themselves. ``accountId`` is compared rather than the display name because
    display names are not unique and are not present on every payload.

    The lookup is cached per (base_url, username) for ``_JIRA_MYSELF_TTL_S``.
    When a refresh fails, the last known id is reused (however old) instead of
    returning "" — otherwise a single timeout on ``/myself`` would switch self-
    suppression off for that poll and let all of your own changes through.
    """
    jira = cfg.get("jira", {})
    base = jira.get("base_url", "").rstrip("/")
    user = jira.get("username", "")
    token = jira.get("api_token", "")
    if not (base and user and token):
        return ""
    cache_key = (base, user)
    now = datetime.now(timezone.utc).timestamp()
    cached = _jira_myself_cache.get(cache_key)
    if cached and now - cached[0] < _JIRA_MYSELF_TTL_S:
        return cached[1]
    url = f"{base}/rest/api/3/myself"
    b64 = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            account_id = json.loads(resp.read().decode()).get("accountId", "")
    except Exception as e:  # noqa: BLE001
        if cached:
            _log(f"WARN jira_myself: {e}; reusing cached accountId")
            return cached[1]
        _log(f"ERROR jira_myself: {e}")
        # No cached identity: self-suppression is off for this poll, so treat
        # it as a failed poll (cursor not advanced) rather than notifying the
        # user about their own changes.
        _record_error("jira", e)
        return ""
    if account_id:
        _jira_myself_cache[cache_key] = (now, account_id)
    return account_id


def _is_jira_app_author(author: dict) -> bool:
    """True when a changelog/comment author is an app or automation actor
    (e.g. "Automation for Jira") rather than a person.

    Jira Cloud tags such actors with ``accountType: "app"``; the display-name
    check is a fallback for payloads that omit ``accountType``.
    """
    if not author:
        return False
    if author.get("accountType", "") == "app":
        return True
    return author.get("displayName", "").lower().startswith("automation for jira")


def _history_targets_self(history: dict, self_id: str) -> bool:
    """True when a changelog history hands the issue to the current user
    (``assignee`` changed *to* ``self_id``). Such automation-made changes are
    kept even when app-authored changes are otherwise suppressed.
    """
    if not self_id:
        return False
    for it in history.get("items", []):
        if it.get("field", "") == "assignee" and it.get("to", "") == self_id:
            return True
    return False


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
        _record_error("jira", e)
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


def _events_from_changelog(issue, cfg, window_start, whitelist, self_id="",
                           suppress_automation=False):
    """Yield event dicts for whitelisted changelog field changes in-window.

    When ``self_id`` is set, changes authored by the current user (matched on
    ``author.accountId``) are skipped so self-triggered edits do not notify.

    When ``suppress_automation`` is set, changes made by apps/automation rules
    (e.g. "Automation for Jira" transitioning a ticket after *you* opened a PR)
    are skipped too — unless the change assigns the issue to you, which is
    worth hearing about whoever's rule did it.
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
        if (suppress_automation and _is_jira_app_author(author_obj)
                and not _history_targets_self(h, self_id)):
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
    suppress_automation = bool(jira.get("suppress_automation", True))
    events = []
    for issue in issues:
        events += _events_from_changelog(issue, cfg, window_start, whitelist,
                                         self_id, suppress_automation)
        events += _events_from_comments(issue, cfg, window_start, self_id)
    # Oldest first for stable notification ordering.
    events.sort(key=lambda e: e["ts"])
    return [_event_to_item(ev, base) for ev in events]


def _only_self_activity(issue: dict, cfg: dict, window_start, self_id: str) -> bool:
    """True when every in-window changelog entry and comment on ``issue`` was
    authored by the current user (and there is at least one). Used by the
    legacy issue-level mode, whose fingerprint is the issue's ``updated`` stamp
    and therefore cannot tell *who* touched the issue.
    """
    if not self_id:
        return False
    actors = []
    for h in _issue_histories(issue, cfg):
        ts = _parse_jira_dt(h.get("created", ""))
        if ts is not None and ts >= window_start:
            actors.append((h.get("author") or {}).get("accountId", ""))
    f = issue.get("fields", {})
    for c in (f.get("comment", {}) or {}).get("comments", []):
        ts = _parse_jira_dt(c.get("created", ""))
        if ts is not None and ts >= window_start:
            actors.append((c.get("author") or {}).get("accountId", ""))
    return bool(actors) and all(a == self_id for a in actors)


def _jira_items_legacy(cfg: dict, window_min: int, issues: list) -> list:
    """Legacy issue-level behaviour: one notification per updated issue."""
    jira = cfg.get("jira", {})
    base = jira.get("base_url", "").rstrip("/")
    user = jira.get("username", "")
    window_start = datetime.now(timezone.utc) - timedelta(minutes=window_min)
    self_id = _jira_myself(cfg) if jira.get("suppress_self", True) else ""
    handle = user.split("@")[0].lower() if "@" in user else user.lower()
    items = []
    for issue in issues:
        # ``suppress_self`` applies here too: skip issues whose only in-window
        # activity is your own.
        if _only_self_activity(issue, cfg, window_start, self_id):
            continue
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

_GH_FAIL = object()  # sentinel: the gh call itself failed (network, auth, ...)


def _gh_api(args: list):
    """Run ``gh <args>`` and parse its JSON output.

    Returns ``_GH_FAIL`` when the command fails (so callers that must tell
    "no data" from "could not fetch" can), ``[]`` for empty output.
    """
    try:
        out = subprocess.run(
            [_deps.gh_path()] + args, check=True, capture_output=True,
            timeout=45, env=_deps.augmented_env(), **_deps.subprocess_kwargs(),
        ).stdout
        return json.loads(out) if out.strip() else []
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()
        if "no checks reported" not in err:
            _log(f"ERROR gh {' '.join(args)}: {err[:200]}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, FileNotFoundError) as e:
        _log(f"ERROR gh {' '.join(args)}: {e}")
    return _GH_FAIL


def _gh_json(args: list):
    """Like ``_gh_api`` but folds failures into ``[]`` (fail-open)."""
    data = _gh_api(args)
    return [] if data is _GH_FAIL else data


_gh_login_cache = {}  # "login" -> auto-detected login (never the empty string)


def gh_login(cfg: dict) -> str:
    login = cfg.get("github", {}).get("login", "")
    if login:
        return login
    if _gh_login_cache.get("login"):
        return _gh_login_cache["login"]
    # `gh api user --jq .login` returns a bare string (not JSON), so call it
    # raw rather than through _gh_json (which would try json.loads and fail).
    try:
        out = subprocess.run(
            [_deps.gh_path(), "api", "user", "--jq", ".login"],
            check=True, capture_output=True, timeout=30,
            env=_deps.augmented_env(), **_deps.subprocess_kwargs(),
        ).stdout.strip()
    except Exception as e:  # noqa: BLE001
        _log(f"WARN gh login lookup failed: {e}")
        if cfg.get("github", {}).get("enabled"):
            _record_error("github", e)
        return ""
    if out:
        _gh_login_cache["login"] = out
    return out


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


_GH_SUBJECT_RE = re.compile(
    r"https://api\.github\.com/repos/([^/]+/[^/]+)/(?:pulls|issues)/(\d+)$")
_GH_TIMELINE_PAGE = 100
_GH_TIMELINE_MAX_PAGES = 5
# Timeline entries that are side effects of someone *else's* action and name
# the recipient (e.g. ``mentioned``'s actor is the person who was mentioned),
# so they must not be mistaken for the latest actor.
_GH_TIMELINE_PASSIVE = {"mentioned", "subscribed", "unsubscribed"}
_GH_ACTOR_CACHE_MAX = 1000
_gh_actor_cache = {}  # "<thread id>:<updated_at>" -> resolved actor login


def _gh_user_login(obj) -> str:
    """``login`` of a GitHub user object, or "" for null/malformed objects."""
    return (obj.get("login") or "") if isinstance(obj, dict) else ""


def _gh_timeline_last_page(repo: str, num: str):
    """Return the newest page of an issue/PR timeline (oldest-first order),
    walking at most ``_GH_TIMELINE_MAX_PAGES`` pages; ``_GH_FAIL`` on error."""
    events = []
    for page in range(1, _GH_TIMELINE_MAX_PAGES + 1):
        data = _gh_api(["api", f"repos/{repo}/issues/{num}/timeline"
                               f"?per_page={_GH_TIMELINE_PAGE}&page={page}"])
        if data is _GH_FAIL:
            return _GH_FAIL
        if not isinstance(data, list) or not data:
            break
        events = data
        if len(data) < _GH_TIMELINE_PAGE:
            break
    return events


_gh_emails_cache = {}  # "auto" -> set of emails discovered via `gh api user`
_gh_unlinked_hint_logged = set()  # emails already warned about (once each)


def _gh_my_emails(cfg: dict) -> frozenset:
    """Git author emails that count as *you* when GitHub cannot link a commit
    to an account: ``github.emails`` from the config plus the public email on
    your GitHub profile (looked up once per process; retried if it failed).
    """
    configured = {e.strip().lower()
                  for e in (cfg.get("github", {}).get("emails") or [])
                  if isinstance(e, str) and e.strip()}
    if "auto" not in _gh_emails_cache:
        data = _gh_api(["api", "user"])
        if data is not _GH_FAIL:
            email = data.get("email") if isinstance(data, dict) else None
            _gh_emails_cache["auto"] = {email.lower()} if email else set()
    return frozenset(configured | _gh_emails_cache.get("auto", set()))


def _gh_commit_actor(repo: str, sha: str, me: str = "",
                     my_emails: frozenset = frozenset(),
                     own_thread: bool = False):
    """Login behind a commit. The timeline's ``committed`` entries only carry
    the git author name/email, so the commit is fetched for the linked GitHub
    account. When GitHub has no link (the git email is not registered on the
    account — a typo'd ``user.email`` is a classic cause), fall back to the
    email list, and finally, on a PR *you* authored, assume the push was yours
    (someone else pushing to your branch with an unregistered email is rare;
    your own pushes are the common case).
    """
    commit = _gh_api(["api", f"repos/{repo}/commits/{sha}"]) if sha else {}
    if commit is _GH_FAIL:
        return _GH_FAIL
    if not isinstance(commit, dict):
        return ""
    login = (_gh_user_login(commit.get("author"))
             or _gh_user_login(commit.get("committer")))
    if login or not me:
        return login
    email = (((commit.get("commit") or {}).get("author") or {})
             .get("email") or "").lower()
    if email in my_emails:
        return me
    if own_thread:
        if email not in _gh_unlinked_hint_logged:
            _gh_unlinked_hint_logged.add(email)
            _log(f"WARN gh: commit {sha[:7]} by <{email}> is not linked to a "
                 f"GitHub account; assuming it is yours (you authored the PR). "
                 f"Add the address to your GitHub account or to github.emails "
                 f"in config.json to make this explicit.")
        return me
    return ""


def _gh_event_login(ev: dict) -> str:
    """Login named by a (non-commit) timeline event, or ""."""
    login = _gh_user_login(ev.get("actor")) or _gh_user_login(ev.get("user"))
    comments = ev.get("comments")
    if not login and isinstance(comments, list) and comments:
        # ``line-commented`` / ``commit-commented`` group several comments.
        login = _gh_user_login(comments[-1].get("user"))
    return login


def _gh_timeline_actor(repo: str, num: str, me: str = "",
                       my_emails: frozenset = frozenset(),
                       own_thread: bool = False):
    """Return the login behind the most recent event on an issue/PR timeline.

    The timeline lists comments, reviews, pushes (``committed``), review
    requests, merges, closes, ... Returns "" when no entry identifies an actor
    and ``_GH_FAIL`` when a request failed. ``me``/``my_emails``/``own_thread``
    feed the push attribution fallbacks (see ``_gh_commit_actor``).
    """
    events = _gh_timeline_last_page(repo, num)
    if events is _GH_FAIL:
        return _GH_FAIL
    for ev in reversed(events):
        if not isinstance(ev, dict) or ev.get("event") in _GH_TIMELINE_PASSIVE:
            continue
        if ev.get("event") == "committed":
            # A push: whoever's commit it is acted last (unknown -> "").
            return _gh_commit_actor(repo, ev.get("sha") or "", me, my_emails,
                                    own_thread)
        login = _gh_event_login(ev)
        if login:
            return login
    return ""


def _gh_latest_actor(subject: dict, reason: str = "", me: str = "",
                     my_emails: frozenset = frozenset()):
    """Return the login of whoever produced a thread's latest activity.

    For pull requests and issues the thread timeline is consulted: GitHub's
    ``subject.latest_comment_url`` names the newest *comment* only, so after a
    push, review, review request, merge or close it is stale, empty, or points
    at the PR itself — whose ``user`` is the PR *author*, not the actor. (That
    last case used to make every review of your own PR look like your own
    activity, and every push of yours look like someone else's.)

    Other subject types (commits, releases, discussions) fall back to
    ``latest_comment_url``. Returns "" when the actor cannot be identified (the
    caller then notifies) and ``_GH_FAIL`` when a request failed (the caller
    defers the thread to the next poll instead of guessing).
    """
    subject = subject or {}
    url = subject.get("url") or ""
    m = _GH_SUBJECT_RE.match(url)
    if m:
        return _gh_timeline_actor(m.group(1), m.group(2), me, my_emails,
                                  own_thread=(reason == "author"))
    latest = subject.get("latest_comment_url") or ""
    if not latest:
        return ""
    # gh api expects a path or full URL; pass the full URL through as-is.
    data = _gh_api(["api", latest])
    if data is _GH_FAIL:
        return _GH_FAIL
    if not isinstance(data, dict):
        return ""
    # Comments/reviews carry ``user``; commits carry ``author``/``committer``.
    return (_gh_user_login(data.get("user"))
            or _gh_user_login(data.get("author"))
            or _gh_user_login(data.get("committer")))


def _gh_thread_actor(notif: dict, me: str = "",
                     my_emails: frozenset = frozenset()):
    """``_gh_latest_actor`` memoised per (thread id, updated_at).

    Unread threads are returned by ``gh api notifications`` on every poll until
    they are read, so without this the same actor lookups repeat every minute.
    A thread's ``updated_at`` changes with each new activity, which invalidates
    the entry naturally. Failures are not cached (so they are retried).
    """
    key = f"{notif.get('id', '')}:{notif.get('updated_at', '')}"
    if key in _gh_actor_cache:
        return _gh_actor_cache[key]
    actor = _gh_latest_actor(notif.get("subject") or {},
                             notif.get("reason", ""), me, my_emails)
    if actor is not _GH_FAIL:
        if len(_gh_actor_cache) >= _GH_ACTOR_CACHE_MAX:
            _gh_actor_cache.clear()
        _gh_actor_cache[key] = actor
    return actor


def gh_notifications(cfg: dict, login: str = "") -> list:
    gh = cfg.get("github", {})
    if not gh.get("enabled"):
        return []
    notifs = _gh_api(["api", "notifications"])
    if notifs is _GH_FAIL:
        _record_error("github", "could not fetch notifications via gh "
                                "(run `gh auth status`)")
        return []
    if not isinstance(notifs, list):
        notifs = []
    # Only look up the latest actor when we can compare it to a known login.
    suppress_self = gh.get("suppress_self", True) and bool(login)
    me = login.lower()  # GitHub logins are case-insensitive
    my_emails = _gh_my_emails(cfg) if suppress_self else frozenset()
    items = []
    for n in notifs:
        reason = n.get("reason", "")
        if reason not in RELEVANT_REASONS:
            continue
        subj = n.get("subject", {}) or {}
        if suppress_self:
            # ``ci_activity`` is, by GitHub's definition, "a workflow run that
            # *you* triggered" — always self-caused. CI on your open PRs is
            # covered by ``gh_ci_fallback`` anyway.
            if reason == "ci_activity":
                continue
            # Suppress threads whose latest activity was your own (e.g. reason
            # "author" right after you pushed to / commented on your own PR).
            actor = _gh_thread_actor(n, login, my_emails)
            if actor is _GH_FAIL:
                # Could not tell who acted. Do not guess: skipping the item here
                # leaves it un-seen, so it is re-evaluated next poll (the thread
                # stays unread on GitHub until then).
                _log(f"WARN gh: actor lookup failed for thread "
                     f"{n.get('id', '')} ({subj.get('title', '')[:60]}); "
                     f"retrying next poll")
                continue
            if actor.lower() == me:
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
            # Fingerprint on thread id + ``updated_at``. GitHub bumps
            # ``updated_at`` on every new activity in a thread (a new review,
            # comment, push, re-request), and each of those *is* news the user
            # asked for. Keying on the id alone meant a PR notified exactly once
            # in its lifetime and every later review on it was silent. Your own
            # activity does not re-notify: ``suppress_self`` above skips threads
            # whose latest actor is you.
            "fp": f"gh-notif:{n.get('id','')}:{n.get('updated_at','')}",
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
    checks = _gh_json(["pr", "checks", num, "--repo", repo,
                       "--json", "state,bucket,link"])
    if not isinstance(checks, list):
        checks = []
    checks = [c for c in checks if isinstance(c, dict)]
    buckets = [c.get("bucket", "") for c in checks]
    if "fail" in buckets:
        rollup, emoji = "fail", "\u274c"
    elif "pending" in buckets:
        rollup, emoji = "pending", "\u23f3"
    elif buckets:
        rollup, emoji = "pass", "\u2705"
    else:
        return None
    # Identify *this* run of the checks. A fingerprint of (PR, roll-up) alone
    # meant that once a PR had failed CI, no later failure on it (after another
    # push, or a re-run) ever notified again. Each check's ``link`` points at a
    # concrete run (e.g. .../actions/runs/<id>/job/<id>), so hashing the links
    # distinguishes runs while staying stable across polls of the same run.
    links = sorted(str(c.get("link") or "") for c in checks if c.get("link"))
    run_id = (hashlib.sha256("\n".join(links).encode()).hexdigest()[:10]
              if links else "")
    fp = f"gh-ci:{repo}#{num}:{rollup}" + (f":{run_id}" if run_id else "")
    return {
        "fp": fp,
        "title": "GitHub CI",
        "subtitle": f"{repo} · PR #{num}",
        "message": f"{emoji} CI {rollup}: {pr.get('title', '')}",
        "url": url,
        "ci_only": True,
        "ci_rollup": rollup,
    }


_CI_NOTIFY_DEFAULT = ["fail"]


def gh_ci_fallback(cfg: dict, login: str) -> list:
    """CI roll-up items for your own open PRs.

    Every roll-up state is returned (so the app can mark it seen), but only the
    states listed in ``github.ci_notify`` (default: just ``fail``) actually
    notify; the rest are flagged ``quiet``. A push of yours always produces a
    ``pending`` first, which you already know about — so it is silent unless
    asked for.
    """
    gh = cfg.get("github", {})
    if not (gh.get("enabled") and login):
        return []
    prs = _gh_api([
        "search", "prs", f"--author={login}", "--state=open",
        "--json", "title,url,number,repository", "--limit", "30",
    ])
    if prs is _GH_FAIL:
        _record_error("github", "could not list your open PRs via gh")
        return []
    if not prs or not isinstance(prs, list):
        return []
    notify = set(gh.get("ci_notify", _CI_NOTIFY_DEFAULT) or [])
    items = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(_ci_rollup_for_pr, prs):
            if result:
                if result.get("ci_rollup") not in notify:
                    result["quiet"] = True
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
        _record_error("pagerduty", e)
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
    max_level = _pd_oncall_max_level(pd)

    items = []
    seen = set()
    current = None   # (end_dt or None, name, url, level)
    upcoming = None  # (start_dt, name, url, level)
    for oc in oncalls:
        # ``/oncalls`` lists *every* escalation level you sit on. PagerDuty's
        # own "ON CALL NOW" only shows level 1; being the level-3 fallback on
        # a policy is not what anyone means by "I'm on-call", so deeper levels
        # are ignored unless ``oncall_max_level`` is raised.
        level = _pd_level(oc)
        if level > max_level:
            continue
        sched = oc.get("schedule") or {}
        pol = oc.get("escalation_policy") or {}
        name = sched.get("summary") or pol.get("summary") or "on-call"
        if level > 1:
            name = f"{name} · level {level}"
        url = sched.get("html_url") or pol.get("html_url") or ""
        start = _parse_dt(oc.get("start"))
        end = _parse_dt(oc.get("end"))
        if start is None or end is None:
            # A direct user target on the escalation policy (no schedule):
            # on-call indefinitely. A schedule-based shift is the more useful
            # thing to show (it has an end time), so this only fills in when
            # nothing else does — and a later scheduled shift may replace it.
            if current is None:
                current = (None, name, url, level)
            continue
        key = (sched.get("id") or pol.get("id") or "", oc.get("start"))
        if key in seen:
            continue
        seen.add(key)
        if start <= now < end and (current is None or current[0] is None
                                   or end > current[0]):
            current = (end, name, url, level)
        elif start > now and (upcoming is None or start < upcoming[0]):
            upcoming = (start, name, url, level)

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
            status["url"] = current[2]
            status["level"] = current[3]
        if upcoming is not None:
            status["next_start"] = upcoming[0].isoformat()
            status["next_schedule"] = upcoming[1]
            status["next_url"] = upcoming[2]
    return items


_PD_ONCALL_MAX_LEVEL_DEFAULT = 1


def _pd_oncall_max_level(pd: dict) -> int:
    """``pagerduty.oncall_max_level`` (default 1): deepest escalation level
    that still counts as "on-call". Invalid values fall back to the default."""
    try:
        level = int(pd.get("oncall_max_level", _PD_ONCALL_MAX_LEVEL_DEFAULT))
    except (TypeError, ValueError):
        return _PD_ONCALL_MAX_LEVEL_DEFAULT
    return level if level >= 1 else _PD_ONCALL_MAX_LEVEL_DEFAULT


def _pd_level(oncall: dict) -> int:
    """``escalation_level`` of an ``/oncalls`` entry (1 when missing/odd)."""
    try:
        level = int(oncall.get("escalation_level", 1))
    except (TypeError, ValueError):
        return 1
    return level if level >= 1 else 1


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


def _guarded(source: str, fn, *args, **kwargs):
    """Run one source's fetch; an unexpected exception becomes a recorded
    error for that source instead of aborting the whole poll (the other
    sources' results are kept)."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        _log(f"ERROR {source}: {type(e).__name__}: {e}")
        _record_error(source, e)
        return []


def _github_phases(cfg: dict):
    """GitHub notifications + CI roll-up (share one login lookup)."""
    if not cfg.get("github", {}).get("enabled"):
        return [], []
    login = gh_login(cfg)
    return (_guarded("github", gh_notifications, cfg, login),
            _guarded("github", gh_ci_fallback, cfg, login))


def collect_all(cfg: dict, log=None, since_ts=None, extra=None):
    """Fetch every source and return ``[(phase_name, items), ...]``.

    `log` is an optional callable(str). `since_ts` is the epoch timestamp of
    the previous poll; the lookback window spans from it to now (capped by
    ``poll.max_window_minutes``). See ``resolve_window_minutes``.

    The three independent sources (Jira, GitHub, PagerDuty) are fetched
    concurrently so a slow one does not delay the others, and each is isolated:
    an unexpected exception in one is recorded as that source's error and the
    remaining results are still returned.

    ``extra`` (optional dict) receives non-notification side data for the
    menu:

    - ``extra["pagerduty"]`` = on-call summary + open incidents assigned to
      you (see ``pagerduty_items``);
    - ``extra["errors"]`` = ``{source: text}`` for every source whose fetch
      failed this poll (see ``describe_error``). Empty when all went well.
    """
    global _log
    if log:
        _log = log
    reset_errors()
    window_min = resolve_window_minutes(cfg, since_ts)
    _log(f"poll window: {window_min} min")
    pd_status = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_jira = pool.submit(_guarded, "jira", jira_items, cfg, window_min)
        f_gh = pool.submit(_github_phases, cfg)
        f_pd = pool.submit(_guarded, "pagerduty", pagerduty_items, cfg,
                           window_min, status=pd_status)
        jira = f_jira.result()
        gh_items, ci_items = f_gh.result()
        pd = f_pd.result()
    phases = [
        ("jira", jira),
        ("github", gh_items),
        ("ci", ci_items),
        ("pagerduty", pd),
    ]
    if extra is not None:
        extra["pagerduty"] = pd_status
        extra["errors"] = poll_errors()
    return phases
