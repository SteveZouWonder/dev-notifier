"""Tests for src/platform_backend — backend interface, dispatch, macOS backend.

The macOS backend delegates login-item management to ``deps`` and shells out to
``open`` for URLs; those side effects are mocked. ``run_on_main`` lazily imports
``PyObjCTools.AppHelper``, so the ``fake_rumps`` fixture (which stubs PyObjC) is
used to exercise it on any platform.

@author SteveZou
"""
import importlib

import pytest

from platform_backend.base import MenuItem, TrayBackend


# ---------------------------------------------------------------------------
# MenuItem data structure
# ---------------------------------------------------------------------------

def test_menuitem_defaults():
    item = MenuItem("Hello")
    assert item.title == "Hello"
    assert item.callback is None
    assert item.state == 0
    assert item.separator is False
    assert item.children == []


def test_menuitem_children_are_independent():
    # A mutable default must not be shared across instances.
    a = MenuItem("a")
    b = MenuItem("b")
    a.add(MenuItem("child"))
    assert b.children == []


def test_menuitem_sep_factory():
    sep = MenuItem.sep()
    assert sep.separator is True


def test_menuitem_submenu():
    parent = MenuItem("Parent")
    parent.add(MenuItem("Child", callback=lambda s: None))
    assert parent.children[0].title == "Child"
    assert callable(parent.children[0].callback)
    # children and _children are the same list (compat alias for tests).
    assert parent._children is parent.children


def test_menuitem_set_icon_records_path():
    item = MenuItem("X")
    item.set_icon("/tmp/x.png", dimensions=(16, 16), template=False)
    assert item.icon == "/tmp/x.png"


def test_menuitem_state_and_tags():
    item = MenuItem("Theme", callback=lambda s: None)
    item.state = 1
    item.theme_name = "Green"  # arbitrary tag attribute
    assert item.state == 1
    assert item.theme_name == "Green"


# ---------------------------------------------------------------------------
# get_backend dispatch
# ---------------------------------------------------------------------------

def test_get_backend_darwin_returns_macos_backend():
    import platform_backend
    from platform_backend.macos import MacOSBackend

    backend = platform_backend.get_backend("darwin")
    assert isinstance(backend, MacOSBackend)
    assert isinstance(backend, TrayBackend)


def test_get_backend_defaults_to_sys_platform(monkeypatch):
    import platform_backend

    monkeypatch.setattr(platform_backend.sys, "platform", "darwin")
    backend = platform_backend.get_backend()
    from platform_backend.macos import MacOSBackend
    assert isinstance(backend, MacOSBackend)


def test_get_backend_unsupported_platform_raises():
    import platform_backend

    with pytest.raises(NotImplementedError):
        platform_backend.get_backend("sunos")


def test_get_backend_win32_returns_windows_backend():
    import platform_backend
    from platform_backend.windows import WindowsBackend

    backend = platform_backend.get_backend("win32")
    assert isinstance(backend, WindowsBackend)
    assert isinstance(backend, TrayBackend)


# ---------------------------------------------------------------------------
# MacOSBackend
# ---------------------------------------------------------------------------

@pytest.fixture
def macos_backend():
    from platform_backend.macos import MacOSBackend
    return MacOSBackend()


def test_macos_open_url_calls_open(macos_backend, monkeypatch):
    import platform_backend.macos as macos_mod
    captured = {}
    monkeypatch.setattr(macos_mod.subprocess, "run",
                        lambda args, **k: captured.setdefault("args", args))
    macos_backend.open_url("https://example.com")
    assert captured["args"] == ["open", "https://example.com"]


def test_macos_login_item_delegates_to_deps(macos_backend, monkeypatch):
    import platform_backend.macos as macos_mod
    calls = []
    monkeypatch.setattr(macos_mod._deps, "login_item_enabled",
                        lambda: calls.append("enabled") or True)
    monkeypatch.setattr(macos_mod._deps, "enable_login_item",
                        lambda: calls.append("enable") or True)
    monkeypatch.setattr(macos_mod._deps, "disable_login_item",
                        lambda: calls.append("disable") or True)

    assert macos_backend.login_item_enabled() is True
    assert macos_backend.enable_login_item() is True
    assert macos_backend.disable_login_item() is True
    assert calls == ["enabled", "enable", "disable"]


def test_macos_run_on_main_uses_callafter(macos_backend, fake_rumps):
    # fake_rumps stubs PyObjCTools.AppHelper.callAfter to run fn(None) inline.
    ran = {"done": False, "arg": "unset"}

    def fn(arg):
        ran["done"] = True
        ran["arg"] = arg

    macos_backend.run_on_main(fn)
    assert ran["done"] is True
    assert ran["arg"] is None


# -- macOS tray UI (rumps) --------------------------------------------------

def test_macos_setup_creates_app(macos_backend, fake_rumps):
    macos_backend.setup(name="DevNotifier", icon="/tmp/o.png")
    assert macos_backend._app is not None
    assert macos_backend._app.icon == "/tmp/o.png"


def test_macos_run_and_quit(macos_backend, fake_rumps, monkeypatch):
    macos_backend.setup(name="D", icon=None)
    ran = {"v": False}
    monkeypatch.setattr(macos_backend._app, "run",
                        lambda: ran.__setitem__("v", True), raising=False)
    macos_backend.run()
    assert ran["v"] is True
    # quit delegates to rumps.quit_application (stubbed as a no-op).
    macos_backend.quit()


def test_macos_set_icon_and_title(macos_backend, fake_rumps):
    macos_backend.setup(name="D", icon=None)
    macos_backend.set_icon("/tmp/x.png")
    assert macos_backend._app.icon == "/tmp/x.png"
    macos_backend.set_title("busy")
    assert macos_backend._app.title == "busy"


def test_macos_set_menu_translates_items(macos_backend, fake_rumps):
    from platform_backend.base import MenuItem
    macos_backend.setup(name="D", icon=None)

    parent = MenuItem("Parent")
    child = MenuItem("Child", callback=lambda s: None)
    child.state = 1
    child.set_icon("/tmp/i.png")
    parent.add(child)
    items = [MenuItem("Top", callback=lambda s: None), MenuItem.sep(), parent]

    macos_backend.set_menu(items)
    # The rumps app menu now holds the translated items.
    menu = list(macos_backend._app.menu)
    assert len(menu) == 3
    # Separator is the stub's sentinel object.
    assert menu[1] is fake_rumps.separator


def test_macos_set_menu_swallows_bad_icon(macos_backend, fake_rumps, monkeypatch):
    from platform_backend.base import MenuItem

    def boom(self, *a, **k):
        raise RuntimeError("bad icon")

    monkeypatch.setattr(fake_rumps.MenuItem, "set_icon", boom)
    macos_backend.setup(name="D", icon=None)
    item = MenuItem("X", callback=lambda s: None)
    item.icon = "/tmp/bad.png"
    macos_backend.set_menu([item])  # must not raise


def test_macos_add_timer_starts(macos_backend, fake_rumps):
    macos_backend.setup(name="D", icon=None)
    t = macos_backend.add_timer(lambda _: None, 5)
    assert t is not None
    # start/stop delegate to the stubbed rumps.Timer (no exception).
    t.start()
    t.stop()


def test_macos_notify_calls_rumps(macos_backend, fake_rumps):
    macos_backend.setup(name="D", icon=None)
    macos_backend.notify(title="Jira", subtitle="s", message="m",
                         data={"url": "u"}, sound=True, icon="/tmp/i.png")
    sent = fake_rumps._notifications_sent[-1]
    assert sent["title"] == "Jira"
    assert sent["icon"] == "/tmp/i.png"


def test_macos_notify_without_icon(macos_backend, fake_rumps):
    macos_backend.setup(name="D", icon=None)
    macos_backend.notify(title="t", subtitle="s", message="m", data={})
    sent = fake_rumps._notifications_sent[-1]
    assert sent["icon"] is None


def test_macos_setup_click_handler_opens_url(macos_backend, fake_rumps, monkeypatch):
    # The @rumps.notifications-decorated handler opens the URL in the click data.
    captured = {}
    monkeypatch.setattr("platform_backend.macos.subprocess.run",
                        lambda args, **k: captured.setdefault("args", args))

    handlers = {}
    monkeypatch.setattr(fake_rumps, "notifications",
                        lambda fn: handlers.setdefault("fn", fn) or fn)
    macos_backend.setup(name="D", icon=None)

    class Info:
        data = {"url": "https://clicked"}

    handlers["fn"](Info())
    assert captured["args"] == ["open", "https://clicked"]
