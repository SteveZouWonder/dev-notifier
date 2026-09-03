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


def _extra_paths() -> list:
    """Common install locations for ``gh`` that a GUI app's PATH may lack."""
    if sys.platform == "win32":
        # winget / the MSI put gh under Program Files; scoop under the user dir.
        roots = [os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")]
        out = []
        for root in roots:
            if root:
                out.append(os.path.join(root, "GitHub CLI"))
        home = os.environ.get("USERPROFILE")
        if home:
            out.append(os.path.join(home, "scoop", "shims"))
        return out
    return list(_EXTRA_PATHS)


def augmented_env() -> dict:
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    for p in _extra_paths():
        if p not in parts:
            parts.append(p)
    env["PATH"] = os.pathsep.join(parts)
    return env


def subprocess_kwargs() -> dict:
    """Extra ``subprocess.run`` kwargs for invoking CLI tools such as ``gh``.

    - ``encoding="utf-8"`` + ``errors="replace"``: ``gh`` always emits UTF-8,
      but ``text=True`` alone decodes with the locale codec (cp1252 on most
      Windows installs), so any non-ASCII PR title / user name raised
      ``UnicodeDecodeError`` and the whole poll was dropped.
    - ``creationflags=CREATE_NO_WINDOW`` (Windows only): the app is a windowed
      (``console=False``) exe, so without this every ``gh`` call flashes a
      console window on the desktop.
    """
    kwargs = {"encoding": "utf-8", "errors": "replace"}
    flag = getattr(subprocess, "CREATE_NO_WINDOW", None)
    if sys.platform == "win32" and flag is not None:
        kwargs["creationflags"] = flag
    return kwargs


def gh_install_hint() -> str:
    """Platform-appropriate one-liner for installing the gh CLI."""
    if sys.platform == "win32":
        return "winget install --id GitHub.cli"
    if sys.platform == "darwin":
        return "brew install gh"
    return "https://cli.github.com"


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
            args, capture_output=True, timeout=timeout,
            env=augmented_env(), **subprocess_kwargs(),
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


def check_pagerduty(cfg: dict) -> dict:
    """Validate PagerDuty config presence (not a live API call)."""
    pd = cfg.get("pagerduty", {})
    if not pd.get("enabled"):
        return {"enabled": False, "configured": False, "detail": "disabled"}
    configured = bool(pd.get("api_token"))
    return {
        "enabled": True,
        "configured": configured,
        "detail": "ok" if configured else "add a PagerDuty user API token",
    }


def check_dependencies(cfg: dict) -> dict:
    """Aggregate status. `ok` is True when at least one source is usable."""
    gh = check_gh()
    jira = check_jira(cfg)
    pagerduty = check_pagerduty(cfg)
    github_enabled = cfg.get("github", {}).get("enabled", False)
    github_ok = github_enabled and gh["installed"] and gh["authed"]
    jira_ok = jira["enabled"] and jira["configured"]
    pagerduty_ok = pagerduty["enabled"] and pagerduty["configured"]
    problems = []
    if github_enabled and not gh["installed"]:
        problems.append("GitHub enabled but gh CLI is not installed "
                        f"({gh_install_hint()})")
    elif github_enabled and not gh["authed"]:
        problems.append("gh CLI is not logged in (run: gh auth login)")
    if jira["enabled"] and not jira["configured"]:
        problems.append("Jira is enabled but not configured "
                        "(edit config: base_url / username / api_token)")
    if pagerduty["enabled"] and not pagerduty["configured"]:
        problems.append("PagerDuty is enabled but not configured "
                        "(edit config: api_token)")
    return {
        "gh": gh,
        "jira": jira,
        "pagerduty": pagerduty,
        "github_ok": github_ok,
        "jira_ok": jira_ok,
        "pagerduty_ok": pagerduty_ok,
        "ok": bool(github_ok or jira_ok or pagerduty_ok),
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


def login_item_blocked_reason():
    """Why "Start at login" cannot be enabled right now, or ``None``.

    The LaunchAgent records the app's *current* path. When the app is running
    straight from the mounted disk image (the user double-clicked it inside the
    DMG window instead of dragging it to Applications) that path disappears as
    soon as the image is ejected, so login-launch would silently do nothing.
    """
    target = _app_launch_target()
    path = target[-1] if target else ""
    if path.startswith("/Volumes/"):
        return ("Dev Notifier is running from the disk image. Drag it to "
                "Applications first, then turn on Start at login.")
    return None


def _write_launch_agent() -> bool:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": _app_launch_target(),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        # Only load in a GUI login session (not over SSH), where ``open`` works.
        "LimitLoadToSessionType": "Aqua",
    }
    try:
        with LAUNCH_AGENT_PLIST.open("wb") as f:
            plistlib.dump(plist, f)
        return True
    except OSError:
        return False


def enable_login_item() -> bool:
    if login_item_blocked_reason():
        return False
    if not _write_launch_agent():
        return False
    # Load now so it is registered without a re-login. launchd also picks the
    # plist up at the next login regardless, so a non-zero exit here (e.g. the
    # job was already loaded) is logged by the caller but not fatal.
    _run(["launchctl", "unload", str(LAUNCH_AGENT_PLIST)])
    _run(["launchctl", "load", str(LAUNCH_AGENT_PLIST)])
    return True


def repair_login_item() -> bool:
    """Re-point an existing LaunchAgent at the app's current location.

    The plist stores an absolute path. If the user later moved/renamed the
    app (or installed an update into a different folder), login-launch would
    silently break — or start an old copy — while the menu still showed the
    toggle as on. Called at startup; rewrites the plist only when the stored
    path differs from where the app is running now (and the app is not running
    from a mounted DMG). Returns True when a repair was written.
    """
    if not LAUNCH_AGENT_PLIST.exists() or login_item_blocked_reason():
        return False
    try:
        with LAUNCH_AGENT_PLIST.open("rb") as f:
            current = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        current = {}
    want = _app_launch_target()
    if current.get("ProgramArguments") == want:
        return False
    return _write_launch_agent()


def disable_login_item() -> bool:
    try:
        if LAUNCH_AGENT_PLIST.exists():
            _run(["launchctl", "unload", str(LAUNCH_AGENT_PLIST)])
            LAUNCH_AGENT_PLIST.unlink()
        return True
    except OSError:
        return False
