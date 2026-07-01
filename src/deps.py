"""Dependency checks and login-item (LaunchAgent) management.

- ``check_dependencies(cfg)`` inspects the gh CLI (installed + authenticated)
  and Jira configuration, returning a structured status the app renders in its
  menu and uses to guide first-time setup.
- ``login_item_*`` install/remove a per-user LaunchAgent so the app can start
  automatically at login. This is reversible and touches only the user's
  ``~/Library/LaunchAgents``.

@author SteveZou
"""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

# GUI apps launched from Finder inherit a minimal PATH without Homebrew, so gh
# (and other CLI tools) may not be found. Augment PATH with common locations.
_EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def augmented_env() -> dict:
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    for p in _EXTRA_PATHS:
        if p not in parts:
            parts.append(p)
    env["PATH"] = os.pathsep.join(parts)
    return env


def gh_path() -> str:
    """Absolute path to the gh binary, or 'gh' if only on an augmented PATH."""
    found = shutil.which("gh", path=augmented_env()["PATH"])
    return found or "gh"

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCH_AGENT_LABEL = "ai.stevezou.devnotifier"
LAUNCH_AGENT_PLIST = LAUNCH_AGENTS_DIR / f"{LAUNCH_AGENT_LABEL}.plist"


# ---------------------------------------------------------------------------
# dependency checks
# ---------------------------------------------------------------------------

def _run(args, timeout=15):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            env=augmented_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_gh() -> dict:
    """Return {'installed': bool, 'authed': bool, 'login': str, 'detail': str}."""
    gh = gh_path()
    if shutil.which("gh", path=augmented_env()["PATH"]) is None:
        return {"installed": False, "authed": False, "login": "",
                "detail": "gh CLI not found"}
    status = _run([gh, "auth", "status"])
    authed = bool(status and status.returncode == 0)
    login = ""
    if authed:
        r = _run([gh, "api", "user", "--jq", ".login"])
        if r and r.returncode == 0:
            login = r.stdout.strip()
    return {
        "installed": True,
        "authed": authed,
        "login": login,
        "detail": "ok" if authed else "gh installed but not logged in",
    }


def check_jira(cfg: dict) -> dict:
    """Validate Jira config presence (not a live API call)."""
    jira = cfg.get("jira", {})
    if not jira.get("enabled"):
        return {"enabled": False, "configured": False, "detail": "disabled"}
    base = jira.get("base_url", "")
    token = jira.get("api_token", "")
    user = jira.get("username", "")
    configured = bool(
        token and user
        and base and "your-domain" not in base
        and "@example.com" not in user
    )
    return {
        "enabled": True,
        "configured": configured,
        "detail": "ok" if configured else "fill in base_url / username / api_token",
    }


def check_dependencies(cfg: dict) -> dict:
    """Aggregate status. `ok` is True when at least one source is usable."""
    gh = check_gh()
    jira = check_jira(cfg)
    github_enabled = cfg.get("github", {}).get("enabled", False)
    github_ok = github_enabled and gh["installed"] and gh["authed"]
    jira_ok = jira["enabled"] and jira["configured"]
    problems = []
    if github_enabled and not gh["installed"]:
        problems.append("GitHub enabled but gh CLI is not installed "
                        "(brew install gh)")
    elif github_enabled and not gh["authed"]:
        problems.append("gh CLI is not logged in (run: gh auth login)")
    if jira["enabled"] and not jira["configured"]:
        problems.append("Jira is enabled but not configured "
                        "(edit config: base_url / username / api_token)")
    return {
        "gh": gh,
        "jira": jira,
        "github_ok": github_ok,
        "jira_ok": jira_ok,
        "ok": bool(github_ok or jira_ok),
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# login item (LaunchAgent)
# ---------------------------------------------------------------------------

def _app_launch_target() -> list:
    """Command that (re)launches this app.

    When frozen (PyInstaller .app), prefer ``open -a <AppBundle>`` so the full
    bundle launches. From source, re-run the current interpreter + launcher.
    """
    if getattr(sys, "frozen", False):
        exe = sys.executable  # .../DevNotifier.app/Contents/MacOS/DevNotifier
        # walk up to the .app bundle
        p = Path(exe)
        for parent in p.parents:
            if parent.suffix == ".app":
                return ["/usr/bin/open", str(parent)]
        return [exe]
    launcher = Path(__file__).resolve().parent.parent / "launcher.py"
    return [sys.executable, str(launcher)]


def login_item_enabled() -> bool:
    return LAUNCH_AGENT_PLIST.exists()


def enable_login_item() -> bool:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": _app_launch_target(),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }
    try:
        with LAUNCH_AGENT_PLIST.open("wb") as f:
            plistlib.dump(plist, f)
        # load (best effort; ignore if already loaded)
        _run(["launchctl", "unload", str(LAUNCH_AGENT_PLIST)])
        _run(["launchctl", "load", str(LAUNCH_AGENT_PLIST)])
        return True
    except OSError:
        return False


def disable_login_item() -> bool:
    try:
        if LAUNCH_AGENT_PLIST.exists():
            _run(["launchctl", "unload", str(LAUNCH_AGENT_PLIST)])
            LAUNCH_AGENT_PLIST.unlink()
        return True
    except OSError:
        return False
