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
             "data": data or {}})

    stub.App = _App
    stub.MenuItem = _MenuItem
    stub.Timer = _Timer
    stub.separator = object()
    stub.notification = _notification
    stub.notifications = lambda fn: fn  # decorator passthrough
    stub.quit_application = lambda *a, **k: None

    saved = sys.modules.get("rumps")
    sys.modules["rumps"] = stub
    # Drop a previously imported notifier_app so it re-imports against the stub.
    sys.modules.pop("notifier_app", None)
    try:
        yield stub
    finally:
        if saved is not None:
            sys.modules["rumps"] = saved
        else:
            sys.modules.pop("rumps", None)
        sys.modules.pop("notifier_app", None)
