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


class FakeTimer:
    """Records start/stop; never actually fires (tests drive callbacks)."""

    def __init__(self, fn, interval):
        self.fn = fn
        self.interval = interval
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeBackend:
    """A recording TrayBackend for exercising NotifierApp logic headlessly.

    Captures notifications, opened URLs, the rendered menu, icon/title, created
    timers, and an in-memory login-item flag — so app-logic tests assert on the
    backend surface without any real GUI toolkit.
    """

    def __init__(self):
        self.notifications = []
        self.opened_urls = []
        self.menu = []
        self.icon = None
        self.title = None
        self.name = None
        self.timers = []
        self.ran = False
        self.quit_called = False
        self._login_enabled = False

    # lifecycle
    def setup(self, name, icon):
        self.name = name
        self.icon = icon

    def run(self):
        self.ran = True

    def quit(self):
        self.quit_called = True

    def run_on_main(self, fn):
        fn(None)

    # tray appearance / menu
    def set_icon(self, path):
        self.icon = path

    def set_title(self, title):
        self.title = title

    def set_menu(self, items):
        self.menu = items

    def add_timer(self, fn, interval_s):
        t = FakeTimer(fn, interval_s)
        t.start()
        self.timers.append(t)
        return t

    # notifications
    def notify(self, title="", subtitle="", message="", data=None, sound=False,
               icon=None):
        self.notifications.append(
            {"title": title, "subtitle": subtitle, "message": message,
             "data": data or {}, "sound": sound, "icon": icon})

    # system integration
    def open_url(self, url):
        self.opened_urls.append(url)

    def login_item_enabled(self):
        return self._login_enabled

    def enable_login_item(self):
        self._login_enabled = True
        return True

    def disable_login_item(self):
        self._login_enabled = False
        return True


@pytest.fixture
def fake_backend():
    """A fresh recording backend instance."""
    return FakeBackend()


@pytest.fixture
def fake_winreg():
    """In-memory stub of the stdlib ``winreg`` module (Windows-only).

    The Windows backend uses ``winreg`` to manage the per-user Run key. On
    macOS/Linux CI that module does not exist, so we inject a fake with an
    in-memory store that supports the create/set/query/delete round-trip the
    backend performs. Missing keys/values raise ``FileNotFoundError`` (an
    ``OSError`` subclass), matching real winreg semantics.
    """
    stub = types.ModuleType("winreg")
    stub.HKEY_CURRENT_USER = "HKCU"
    stub.REG_SZ = 1
    stub.KEY_SET_VALUE = 0x0002
    # store: {(hive, subkey): {value_name: (data, type)}}
    stub._store = {}

    class _Key:
        def __init__(self, ident):
            self.ident = ident

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open_key(hive, subkey, reserved=0, access=0):
        if (hive, subkey) not in stub._store:
            raise FileNotFoundError(2, "key not found")
        return _Key((hive, subkey))

    def _create_key(hive, subkey):
        stub._store.setdefault((hive, subkey), {})
        return _Key((hive, subkey))

    def _set_value_ex(key, name, reserved, vtype, data):
        stub._store.setdefault(key.ident, {})[name] = (data, vtype)

    def _query_value_ex(key, name):
        values = stub._store.get(key.ident, {})
        if name not in values:
            raise FileNotFoundError(2, "value not found")
        return values[name]

    def _delete_value(key, name):
        values = stub._store.get(key.ident, {})
        if name not in values:
            raise FileNotFoundError(2, "value not found")
        del values[name]

    stub.OpenKey = _open_key
    stub.CreateKey = _create_key
    stub.SetValueEx = _set_value_ex
    stub.QueryValueEx = _query_value_ex
    stub.DeleteValue = _delete_value

    saved = sys.modules.get("winreg")
    sys.modules["winreg"] = stub
    try:
        yield stub
    finally:
        if saved is not None:
            sys.modules["winreg"] = saved
        else:
            sys.modules.pop("winreg", None)


@pytest.fixture
def fake_pystray():
    """Stub of the ``pystray`` package for testing the Windows tray UI.

    Provides Icon/Menu/MenuItem shims that record structure and callbacks so the
    neutral-MenuItem -> pystray translation and icon/menu wiring can be asserted
    off-Windows.
    """
    stub = types.ModuleType("pystray")

    class _Menu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = list(items)

    class _MenuItem:
        def __init__(self, title, action=None, checked=None, enabled=True):
            self.title = title
            self.action = action
            self.checked = checked
            self.enabled = enabled

    class _Icon:
        def __init__(self, name, icon=None, title=None, menu=None):
            self.name = name
            self.icon = icon
            self.title = title
            self.menu = menu
            self.ran = False
            self.stopped = False

        def run(self):
            self.ran = True

        def stop(self):
            self.stopped = True

    stub.Menu = _Menu
    stub.MenuItem = _MenuItem
    stub.Icon = _Icon

    saved = sys.modules.get("pystray")
    sys.modules["pystray"] = stub
    try:
        yield stub
    finally:
        if saved is not None:
            sys.modules["pystray"] = saved
        else:
            sys.modules.pop("pystray", None)


@pytest.fixture
def fake_pil():
    """Stub of ``PIL.Image`` so icon loading is testable without Pillow."""
    pil = types.ModuleType("PIL")
    image_mod = types.ModuleType("PIL.Image")

    class _Img:
        def __init__(self, path):
            self.path = path

    image_mod.Image = _Img
    image_mod.open = lambda path: _Img(path)
    pil.Image = image_mod

    saved_pil = sys.modules.get("PIL")
    saved_img = sys.modules.get("PIL.Image")
    sys.modules["PIL"] = pil
    sys.modules["PIL.Image"] = image_mod
    try:
        yield image_mod
    finally:
        for name, saved in (("PIL", saved_pil), ("PIL.Image", saved_img)):
            if saved is not None:
                sys.modules[name] = saved
            else:
                sys.modules.pop(name, None)


@pytest.fixture
def fake_winotify():
    """Stub of the ``winotify`` package so toast logic is testable off-Windows.

    Records constructed notifications and their actions so tests can assert on
    title/body/URL without a real Windows Action Center.
    """
    stub = types.ModuleType("winotify")
    stub._shown = []

    class _Notification:
        def __init__(self, app_id="", title="", msg="", icon=None, **k):
            self.app_id = app_id
            self.title = title
            self.msg = msg
            self.icon = icon
            self.actions = []
            self.shown = False

        def add_actions(self, label="", launch=""):
            self.actions.append({"label": label, "launch": launch})

        def show(self):
            self.shown = True
            stub._shown.append(self)

    stub.Notification = _Notification

    saved = sys.modules.get("winotify")
    sys.modules["winotify"] = stub
    try:
        yield stub
    finally:
        if saved is not None:
            sys.modules["winotify"] = saved
        else:
            sys.modules.pop("winotify", None)
