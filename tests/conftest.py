"""Shared pytest fixtures and helpers for the dev-notifier test suite.

Keeps the tests hermetic: no real network, subprocess, or user-home writes.
Fixtures isolate ``$HOME`` (so config/state/cache land in a temp dir) and offer
small factories for the config dict and fake ``subprocess`` results the app code
expects.

@author SteveZou
"""
import importlib
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Point ``Path.home()`` at a temp dir so config/state/cache stay isolated.

    Modules that compute paths at import time (config, deps, updater) resolve
    ``Path.home()`` in module globals, so we patch and reload them to pick up
    the temp home.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    yield tmp_path


@pytest.fixture
def sample_cfg():
    """A fully configured cfg dict (both sources enabled and usable)."""
    return {
        "jira": {
            "enabled": True,
            "base_url": "https://acme.atlassian.net",
            "username": "dev@acme.com",
            "api_token": "secret-token",
        },
        "github": {"enabled": True, "login": "octocat"},
        "pagerduty": {
            "enabled": True,
            "api_token": "pd-secret-token",
            "user_id": "PUSER1",
            "team_ids": ["PTEAM1"],
        },
        "poll": {"interval_seconds": 300, "window_minutes": 10},
        "update": {
            "enabled": True,
            "check_interval_hours": 24,
            "skipped_version": "",
        },
        "theme": "Orange",
    }


class FakeCompletedProcess:
    """Minimal stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_proc():
    """Factory building fake CompletedProcess objects for subprocess mocks."""
    return FakeCompletedProcess


@pytest.fixture
def fake_rumps():
    """Install a stub ``rumps`` module so notifier_app imports on any platform.

    rumps depends on AppKit/PyObjC and only imports on macOS. For core logic
    tests we inject a lightweight fake providing just the attributes the module
    touches at import time and in the functions under test.
    """
    stub = types.ModuleType("rumps")

    class _Menu(list):
        """List-like menu supporting clear() and assignment, like rumps.App.menu."""

        def clear(self):
            del self[:]

    class _App:
        def __init__(self, name="", icon=None, template=False, quit_button=None,
                     **k):
            self.name = name
            self.icon = icon
            self.title = None
            self._menu = _Menu()

        @property
        def menu(self):
            return self._menu

        @menu.setter
        def menu(self, value):
            # rumps accepts either a list (replace) or the menu object itself.
            self._menu = _Menu(value) if isinstance(value, list) else value

        def run(self):  # pragma: no cover - never actually run in tests
            pass

    class _MenuItem:
        def __init__(self, title="", callback=None, **k):
            self.title = title
            self.callback = callback
            self.state = 0
            self._children = []

        def add(self, item):
            self._children.append(item)

        def set_icon(self, *a, **k):
            pass

    class _Timer:
        def __init__(self, callback=None, interval=0, **k):
            self.callback = callback
            self.interval = interval

        def start(self):
            pass

        def stop(self):
            pass

    # Track notifications so tests can assert on them.
    stub._notifications_sent = []

    def _notification(title="", subtitle="", message="", data=None, **k):
        stub._notifications_sent.append(
            {"title": title, "subtitle": subtitle, "message": message,
             "data": data or {}, "icon": k.get("icon")})

    stub.App = _App
    stub.MenuItem = _MenuItem
    stub.Timer = _Timer
    stub.separator = object()
    stub.notification = _notification
    stub.notifications = lambda fn: fn  # decorator passthrough
    stub.quit_application = lambda *a, **k: None

    # notifier_app imports ``from PyObjCTools import AppHelper`` (macOS/PyObjC
    # only) to marshal worker-thread results onto the main run loop. PyObjC is
    # not installed on Linux CI, so stub it too. ``callAfter`` runs the callback
    # synchronously here, which is what the tests want (deterministic, no loop).
    pyobjctools = types.ModuleType("PyObjCTools")
    apphelper = types.ModuleType("PyObjCTools.AppHelper")

    def _call_after(fn, *args):
        fn(*args)

    apphelper.callAfter = _call_after
    pyobjctools.AppHelper = apphelper

    saved_rumps = sys.modules.get("rumps")
    saved_pot = sys.modules.get("PyObjCTools")
    saved_ah = sys.modules.get("PyObjCTools.AppHelper")
    sys.modules["rumps"] = stub
    sys.modules["PyObjCTools"] = pyobjctools
    sys.modules["PyObjCTools.AppHelper"] = apphelper
    # Drop a previously imported notifier_app so it re-imports against the stub.
    sys.modules.pop("notifier_app", None)
    try:
        yield stub
    finally:
        for name, saved in (("rumps", saved_rumps),
                            ("PyObjCTools", saved_pot),
                            ("PyObjCTools.AppHelper", saved_ah)):
            if saved is not None:
                sys.modules[name] = saved
            else:
                sys.modules.pop(name, None)
        sys.modules.pop("notifier_app", None)
