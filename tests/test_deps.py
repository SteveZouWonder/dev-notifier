"""Tests for src/deps.py — gh/Jira dependency checks and LaunchAgent login item.

All subprocess and filesystem side effects are mocked so nothing on the real
machine is touched.

@author SteveZou
"""
import importlib
import sys

import pytest


@pytest.fixture
def deps_mod(temp_home):
    import deps as deps_mod
    importlib.reload(deps_mod)
    return deps_mod


# ---------------------------------------------------------------------------
# augmented_env / gh_path
# ---------------------------------------------------------------------------

def test_augmented_env_appends_common_paths(deps_mod, monkeypatch):
    import os
    # The extra paths are platform-specific; pin macOS so this passes on the
    # native Windows CI job too.
    monkeypatch.setattr(deps_mod.sys, "platform", "darwin")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = deps_mod.augmented_env()
    # augmented_env joins with os.pathsep (':' on POSIX, ';' on Windows).
    parts = env["PATH"].split(os.pathsep)
    assert "/opt/homebrew/bin" in parts
    # Existing entries are preserved and not duplicated.
    assert parts.count("/usr/bin") == 1


def test_extra_paths_windows_uses_gh_install_dirs(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\me")
    paths = deps_mod._extra_paths()
    # os.path.join uses the host separator, so only assert on the components.
    assert any(p.endswith("GitHub CLI") and p.startswith(r"C:\Program Files")
               for p in paths)
    assert any(p.endswith("shims") and "scoop" in p for p in paths)
    assert "/opt/homebrew/bin" not in paths


def test_extra_paths_windows_skips_unset_roots(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod.sys, "platform", "win32")
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "USERPROFILE"):
        monkeypatch.delenv(var, raising=False)
    assert deps_mod._extra_paths() == []


# ---------------------------------------------------------------------------
# subprocess_kwargs / gh_install_hint
# ---------------------------------------------------------------------------

def test_subprocess_kwargs_decodes_utf8_with_replacement(deps_mod):
    # gh emits UTF-8; decoding with the locale codec (cp1252 on Windows) used to
    # raise UnicodeDecodeError on any non-ASCII title and drop the whole poll.
    kw = deps_mod.subprocess_kwargs()
    assert kw["encoding"] == "utf-8"
    assert kw["errors"] == "replace"


def test_subprocess_kwargs_hides_console_window_on_windows(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod.sys, "platform", "win32")
    monkeypatch.setattr(deps_mod.subprocess, "CREATE_NO_WINDOW", 0x08000000,
                        raising=False)
    assert deps_mod.subprocess_kwargs()["creationflags"] == 0x08000000


def test_subprocess_kwargs_no_creationflags_off_windows(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod.sys, "platform", "darwin")
    assert "creationflags" not in deps_mod.subprocess_kwargs()


def test_run_passes_subprocess_kwargs(deps_mod, monkeypatch, fake_proc):
    seen = {}

    def fake_run(args, **kw):
        seen.update(kw)
        return fake_proc(returncode=0)

    monkeypatch.setattr(deps_mod.subprocess, "run", fake_run)
    assert deps_mod._run(["gh", "auth", "status"]) is not None
    assert seen["encoding"] == "utf-8"
    assert seen["capture_output"] is True
    assert "text" not in seen  # encoding= implies text mode


@pytest.mark.parametrize("platform,expected", [
    ("win32", "winget install --id GitHub.cli"),
    ("darwin", "brew install gh"),
    ("linux", "https://cli.github.com"),
])
def test_gh_install_hint_is_platform_aware(deps_mod, monkeypatch, platform, expected):
    monkeypatch.setattr(deps_mod.sys, "platform", platform)
    assert deps_mod.gh_install_hint() == expected


def test_check_dependencies_gh_hint_matches_platform(deps_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(deps_mod.sys, "platform", "win32")
    monkeypatch.setattr(deps_mod, "check_gh", lambda: {
        "installed": False, "authed": False, "login": "", "detail": "x"})
    status = deps_mod.check_dependencies(sample_cfg)
    assert any("winget install --id GitHub.cli" in p for p in status["problems"])
    assert not any("brew" in p for p in status["problems"])


def test_gh_path_falls_back_to_bare_gh(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod.shutil, "which", lambda *a, **k: None)
    assert deps_mod.gh_path() == "gh"


def test_gh_path_returns_found_binary(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod.shutil, "which", lambda *a, **k: "/opt/homebrew/bin/gh")
    assert deps_mod.gh_path() == "/opt/homebrew/bin/gh"


# ---------------------------------------------------------------------------
# check_gh
# ---------------------------------------------------------------------------

def test_check_gh_not_installed(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod.shutil, "which", lambda *a, **k: None)
    result = deps_mod.check_gh()
    assert result == {"installed": False, "authed": False, "login": "",
                      "detail": "gh CLI not found"}


def test_check_gh_installed_not_authed(deps_mod, monkeypatch, fake_proc):
    monkeypatch.setattr(deps_mod.shutil, "which", lambda *a, **k: "/usr/bin/gh")
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: fake_proc(returncode=1))
    result = deps_mod.check_gh()
    assert result["installed"] is True
    assert result["authed"] is False
    assert result["login"] == ""


def test_check_gh_installed_and_authed(deps_mod, monkeypatch, fake_proc):
    monkeypatch.setattr(deps_mod.shutil, "which", lambda *a, **k: "/usr/bin/gh")

    def fake_run(args, timeout=15):
        if "status" in args:
            return fake_proc(returncode=0)
        if "api" in args:
            return fake_proc(returncode=0, stdout="octocat\n")
        return fake_proc(returncode=0)

    monkeypatch.setattr(deps_mod, "_run", fake_run)
    result = deps_mod.check_gh()
    assert result["authed"] is True
    assert result["login"] == "octocat"


def test_run_returns_none_on_oserror(deps_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("no such binary")

    monkeypatch.setattr(deps_mod.subprocess, "run", boom)
    assert deps_mod._run(["whatever"]) is None


# ---------------------------------------------------------------------------
# check_jira
# ---------------------------------------------------------------------------

def test_check_jira_disabled(deps_mod):
    result = deps_mod.check_jira({"jira": {"enabled": False}})
    assert result == {"enabled": False, "configured": False, "detail": "disabled"}


def test_check_jira_configured(deps_mod, sample_cfg):
    result = deps_mod.check_jira(sample_cfg)
    assert result == {"enabled": True, "configured": True, "detail": "ok"}


@pytest.mark.parametrize("base,user,token", [
    ("https://your-domain.atlassian.net", "dev@acme.com", "tok"),  # placeholder base
    ("https://acme.atlassian.net", "you@example.com", "tok"),      # placeholder user
    ("https://acme.atlassian.net", "dev@acme.com", ""),            # missing token
])
def test_check_jira_not_configured(deps_mod, base, user, token):
    cfg = {"jira": {"enabled": True, "base_url": base, "username": user,
                    "api_token": token}}
    result = deps_mod.check_jira(cfg)
    assert result["configured"] is False


# ---------------------------------------------------------------------------
# check_pagerduty
# ---------------------------------------------------------------------------

def test_check_pagerduty_disabled(deps_mod):
    result = deps_mod.check_pagerduty({"pagerduty": {"enabled": False}})
    assert result == {"enabled": False, "configured": False, "detail": "disabled"}


def test_check_pagerduty_configured(deps_mod, sample_cfg):
    result = deps_mod.check_pagerduty(sample_cfg)
    assert result == {"enabled": True, "configured": True, "detail": "ok"}


def test_check_pagerduty_missing_token(deps_mod):
    cfg = {"pagerduty": {"enabled": True, "api_token": ""}}
    result = deps_mod.check_pagerduty(cfg)
    assert result["configured"] is False


# ---------------------------------------------------------------------------
# check_dependencies (aggregate)
# ---------------------------------------------------------------------------

def test_check_dependencies_all_ok(deps_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(deps_mod, "check_gh", lambda: {
        "installed": True, "authed": True, "login": "octocat", "detail": "ok"})
    status = deps_mod.check_dependencies(sample_cfg)
    assert status["ok"] is True
    assert status["github_ok"] is True
    assert status["jira_ok"] is True
    assert status["pagerduty_ok"] is True
    assert status["problems"] == []


def test_check_dependencies_pagerduty_only(deps_mod, monkeypatch):
    # Only PagerDuty is usable -> overall ok via PagerDuty.
    cfg = {"jira": {"enabled": False}, "github": {"enabled": False},
           "pagerduty": {"enabled": True, "api_token": "tok"}}
    monkeypatch.setattr(deps_mod, "check_gh", lambda: {
        "installed": False, "authed": False, "login": "", "detail": "x"})
    status = deps_mod.check_dependencies(cfg)
    assert status["pagerduty_ok"] is True
    assert status["ok"] is True
    assert status["problems"] == []


def test_check_dependencies_pagerduty_enabled_not_configured(deps_mod, monkeypatch):
    cfg = {"jira": {"enabled": False}, "github": {"enabled": False},
           "pagerduty": {"enabled": True, "api_token": ""}}
    monkeypatch.setattr(deps_mod, "check_gh", lambda: {
        "installed": False, "authed": False, "login": "", "detail": "x"})
    status = deps_mod.check_dependencies(cfg)
    assert status["ok"] is False
    assert any("PagerDuty is enabled but not configured" in p
               for p in status["problems"])


def test_check_dependencies_github_not_installed(deps_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(deps_mod, "check_gh", lambda: {
        "installed": False, "authed": False, "login": "", "detail": "x"})
    status = deps_mod.check_dependencies(sample_cfg)
    # Jira still ok so overall ok, but a gh problem is reported.
    assert status["ok"] is True
    assert any("gh CLI is not installed" in p for p in status["problems"])


def test_check_dependencies_none_usable(deps_mod, monkeypatch):
    cfg = {"jira": {"enabled": True, "base_url": "https://your-domain.atlassian.net",
                    "username": "you@example.com", "api_token": ""},
           "github": {"enabled": True}}
    monkeypatch.setattr(deps_mod, "check_gh", lambda: {
        "installed": True, "authed": False, "login": "", "detail": "x"})
    status = deps_mod.check_dependencies(cfg)
    assert status["ok"] is False
    assert len(status["problems"]) == 2  # gh not logged in + jira not configured


# ---------------------------------------------------------------------------
# login item (LaunchAgent)
# ---------------------------------------------------------------------------

def test_login_item_enable_and_disable_roundtrip(deps_mod, monkeypatch):
    # Avoid actually invoking launchctl.
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    monkeypatch.setattr(deps_mod, "_app_launch_target", lambda: ["/usr/bin/true"])

    assert deps_mod.login_item_enabled() is False
    assert deps_mod.enable_login_item() is True
    assert deps_mod.LAUNCH_AGENT_PLIST.exists()
    assert deps_mod.login_item_enabled() is True

    assert deps_mod.disable_login_item() is True
    assert not deps_mod.LAUNCH_AGENT_PLIST.exists()
    assert deps_mod.login_item_enabled() is False


def test_enable_login_item_writes_valid_plist(deps_mod, monkeypatch):
    import plistlib
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    monkeypatch.setattr(deps_mod, "_app_launch_target", lambda: ["/usr/bin/true"])

    deps_mod.enable_login_item()
    with deps_mod.LAUNCH_AGENT_PLIST.open("rb") as f:
        data = plistlib.load(f)
    assert data["Label"] == deps_mod.LAUNCH_AGENT_LABEL
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"] == ["/usr/bin/true"]


def test_disable_login_item_noop_when_absent(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    # Nothing installed; disable should still succeed.
    assert deps_mod.disable_login_item() is True


def test_enable_login_item_returns_false_on_oserror(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "_app_launch_target", lambda: ["/usr/bin/true"])

    def boom(*a, **k):
        raise OSError("cannot write plist")

    # plistlib.dump raises inside the try -> enable returns False.
    monkeypatch.setattr(deps_mod.plistlib, "dump", boom)
    assert deps_mod.enable_login_item() is False


def test_disable_login_item_returns_false_on_oserror(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    monkeypatch.setattr(deps_mod, "_app_launch_target", lambda: ["/usr/bin/true"])
    deps_mod.enable_login_item()  # create the plist first

    def boom():
        raise OSError("cannot unlink")

    # Path.unlink raising -> disable returns False.
    monkeypatch.setattr(deps_mod.Path, "unlink", lambda self, *a, **k: boom())
    assert deps_mod.disable_login_item() is False


# ---------------------------------------------------------------------------
# _app_launch_target
# ---------------------------------------------------------------------------

def test_app_launch_target_from_source(deps_mod, monkeypatch):
    # Not frozen -> re-run the current interpreter with launcher.py.
    monkeypatch.setattr(deps_mod.sys, "frozen", False, raising=False)
    target = deps_mod._app_launch_target()
    assert target[0] == deps_mod.sys.executable
    assert target[1].endswith("launcher.py")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX .app bundle path semantics; deps LaunchAgent "
                           "handling is macOS-only")
def test_app_launch_target_frozen_app_bundle(deps_mod, monkeypatch):
    # Frozen inside a .app bundle -> use `open <AppBundle>`.
    monkeypatch.setattr(deps_mod.sys, "frozen", True, raising=False)
    exe = "/Applications/DevNotifier.app/Contents/MacOS/DevNotifier"
    monkeypatch.setattr(deps_mod.sys, "executable", exe, raising=False)
    target = deps_mod._app_launch_target()
    assert target == ["/usr/bin/open", "/Applications/DevNotifier.app"]


def test_app_launch_target_frozen_no_bundle(deps_mod, monkeypatch):
    # Frozen but not inside a .app (edge case) -> fall back to the executable.
    monkeypatch.setattr(deps_mod.sys, "frozen", True, raising=False)
    exe = "/opt/somewhere/DevNotifier"
    monkeypatch.setattr(deps_mod.sys, "executable", exe, raising=False)
    target = deps_mod._app_launch_target()
    assert target == [exe]


# ---------------------------------------------------------------------------
# login item: blocked from a mounted DMG, and path self-heal
# ---------------------------------------------------------------------------

def test_login_item_blocked_when_running_from_dmg(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open",
                                 "/Volumes/DevNotifier 1.5.8/DevNotifier.app"])
    reason = deps_mod.login_item_blocked_reason()
    assert reason and "disk image" in reason
    # enable refuses (and writes nothing) while blocked.
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    assert deps_mod.enable_login_item() is False
    assert not deps_mod.LAUNCH_AGENT_PLIST.exists()


def test_login_item_not_blocked_from_applications(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open", "/Applications/DevNotifier.app"])
    assert deps_mod.login_item_blocked_reason() is None
    monkeypatch.setattr(deps_mod, "_app_launch_target", lambda: [])
    assert deps_mod.login_item_blocked_reason() is None


def test_launch_agent_limited_to_gui_session(deps_mod, monkeypatch):
    import plistlib
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    monkeypatch.setattr(deps_mod, "_app_launch_target", lambda: ["/usr/bin/true"])
    deps_mod.enable_login_item()
    with deps_mod.LAUNCH_AGENT_PLIST.open("rb") as f:
        data = plistlib.load(f)
    assert data["LimitLoadToSessionType"] == "Aqua"


def test_repair_login_item_noop_when_not_enabled(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open", "/Applications/DevNotifier.app"])
    assert deps_mod.repair_login_item() is False


def test_repair_login_item_rewrites_stale_path(deps_mod, monkeypatch):
    import plistlib
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    # Enabled while the app lived in ~/Downloads ...
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open", "/Users/me/Downloads/DevNotifier.app"])
    assert deps_mod.enable_login_item() is True
    # ... then moved to /Applications: startup repair re-points the plist.
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open", "/Applications/DevNotifier.app"])
    assert deps_mod.repair_login_item() is True
    with deps_mod.LAUNCH_AGENT_PLIST.open("rb") as f:
        data = plistlib.load(f)
    assert data["ProgramArguments"] == ["/usr/bin/open", "/Applications/DevNotifier.app"]
    # Already correct -> nothing to do.
    assert deps_mod.repair_login_item() is False


def test_repair_login_item_skipped_from_dmg(deps_mod, monkeypatch):
    monkeypatch.setattr(deps_mod, "_run", lambda *a, **k: None)
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open", "/Applications/DevNotifier.app"])
    deps_mod.enable_login_item()
    # Running from the DMG must never re-point the agent at /Volumes/...
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open", "/Volumes/DN/DevNotifier.app"])
    assert deps_mod.repair_login_item() is False


def test_repair_login_item_handles_unreadable_plist(deps_mod, monkeypatch):
    import plistlib
    deps_mod.LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    deps_mod.LAUNCH_AGENT_PLIST.write_bytes(b"not a plist")
    monkeypatch.setattr(deps_mod, "_app_launch_target",
                        lambda: ["/usr/bin/open", "/Applications/DevNotifier.app"])
    assert deps_mod.repair_login_item() is True
    with deps_mod.LAUNCH_AGENT_PLIST.open("rb") as f:
        assert plistlib.load(f)["ProgramArguments"][-1].endswith("DevNotifier.app")
