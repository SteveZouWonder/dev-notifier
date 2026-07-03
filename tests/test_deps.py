"""Tests for src/deps.py — gh/Jira dependency checks and LaunchAgent login item.

All subprocess and filesystem side effects are mocked so nothing on the real
machine is touched.

@author SteveZou
"""
import importlib

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
    monkeypatch.setenv("PATH", "/usr/bin")
    env = deps_mod.augmented_env()
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    # Existing entries are preserved and not duplicated.
    assert env["PATH"].split(":").count("/usr/bin") == 1


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
