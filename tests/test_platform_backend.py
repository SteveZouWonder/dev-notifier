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
    assert item.checked is False
    assert item.separator is False
    assert item.children == []


def test_menuitem_children_are_independent():
    # A mutable default must not be shared across instances.
    a = MenuItem("a")
    b = MenuItem("b")
    a.children.append(MenuItem("child"))
    assert b.children == []


def test_menuitem_sep_factory():
    sep = MenuItem.sep()
    assert sep.separator is True


def test_menuitem_submenu():
    parent = MenuItem("Parent", children=[MenuItem("Child", callback=lambda: None)])
    assert parent.children[0].title == "Child"
    assert callable(parent.children[0].callback)


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


def test_get_backend_win32_imports_windows_backend(monkeypatch):
    # The Windows backend does not exist yet (P2). Dispatch should attempt the
    # import and surface an ImportError rather than silently returning None.
    import platform_backend

    with pytest.raises(ImportError):
        platform_backend.get_backend("win32")


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
