"""Fetch Jira + GitHub items relevant to the current user.

Pure data-gathering: returns a list of item dicts. Deduplication and
notification are handled by the caller. Uses the Jira REST API v3 directly and
the ``gh`` CLI for GitHub (so no GitHub token is stored).

@author SteveZou
"""
import base64
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import urllib.request

import deps as _deps


def _log(msg: str) -> None:
    # Lazy import to avoid a hard dependency loop; app wires real logging.
    print(msg)


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

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
    }).encode()
    b64 = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": f"Basic {b64}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()).get("issues", [])
    except Exception as e:  # noqa: BLE001
        _log(f"ERROR jira_search: {e}")
        return []


def jira_items(cfg: dict, window_min: int) -> list:
    jira = cfg.get("jira", {})
    if not jira.get("enabled"):
        return []
    base = jira.get("base_url", "").rstrip("/")
    user = jira.get("username", "")
    issues = _jira_search(cfg, window_min)
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
            created = c.get("created", "")
            try:
                cdt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
            if cdt < window_start:
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


def gh_notifications(cfg: dict) -> list:
    if not cfg.get("github", {}).get("enabled"):
        return []
    notifs = _gh_json(["api", "notifications"])
    items = []
    for n in notifs:
        reason = n.get("reason", "")
        if reason not in RELEVANT_REASONS:
            continue
        subj = n.get("subject", {}) or {}
        repo = (n.get("repository", {}) or {}).get("full_name", "")
        api_url = subj.get("url") or ""
        if api_url:
            url = _html_url(api_url)
        elif repo:
            url = f"https://github.com/{repo}/pulls"
        else:
            url = "https://github.com/notifications"
        items.append({
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


def collect_all(cfg: dict, log=None):
    """Yield item lists in priority order (fast sources first).

    Returns a list of (phase_name, items) so the caller can notify
    incrementally. `log` is an optional callable(str).
    """
    global _log
    if log:
        _log = log
    window_min = cfg.get("poll", {}).get("window_minutes", 10)
    login = gh_login(cfg)
    return [
        ("jira", jira_items(cfg, window_min)),
        ("github", gh_notifications(cfg)),
        ("ci", gh_ci_fallback(cfg, login)),
    ]
