"""Tests for src/platform_backend/windows.py — the Windows backend.

Windows-only APIs (``winreg``, ``winotify``, ``os.startfile``) do not exist on
macOS/Linux CI, so they are injected via the ``fake_winreg`` / ``fake_winotify``
fixtures and monkeypatched. This lets the pure logic (registry round-trip, toast
construction, launch-command building) run on any platform.

@author SteveZou
"""
import importlib

import pytest


@pytest.fixture
def win_mod():
    import platform_backend.windows as win_mod
    importlib.reload(win_mod)
    return win_mod


@pytest.fixture
def backend(win_mod):
    return win_mod.WindowsBackend()


# ---------------------------------------------------------------------------
# open_url
# ---------------------------------------------------------------------------

def test_open_url_uses_startfile(backend, win_mod, monkeypatch):
    opened = {}
    # os.startfile only exists on Windows; inject it for the test.
    monkeypatch.setattr(win_mod.os, "startfile",
                        lambda url: opened.setdefault("url", url), raising=False)
    backend.open_url("https://example.com")
    assert opened["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# run_on_main
# ---------------------------------------------------------------------------

def test_run_on_main_runs_inline_with_none(backend):
    seen = {}
    backend.run_on_main(lambda arg: seen.setdefault("arg", arg))
    assert "arg" in seen and seen["arg"] is None


# ---------------------------------------------------------------------------
# launch command
# ---------------------------------------------------------------------------

def test_launch_command_from_source(win_mod, monkeypatch):
    monkeypatch.setattr(win_mod.sys, "frozen", False, raising=False)
    monkeypatch.setattr(win_mod.sys, "executable", r"C:\Python\python.exe",
                        raising=False)
    cmd = win_mod._launch_command()
    assert cmd.startswith('"C:\\Python\\python.exe"')
    assert "launcher.py" in cmd


def test_launch_command_frozen(win_mod, monkeypatch):
    monkeypatch.setattr(win_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(win_mod.sys, "executable",
                        r"C:\Program Files\DevNotifier\DevNotifier.exe",
                        raising=False)
    cmd = win_mod._launch_command()
    assert cmd == '"C:\\Program Files\\DevNotifier\\DevNotifier.exe"'
    assert "launcher.py" not in cmd


# ---------------------------------------------------------------------------
# start-at-login (registry round-trip)
# ---------------------------------------------------------------------------

def test_login_item_enable_query_disable_roundtrip(backend, fake_winreg):
    assert backend.login_item_enabled() is False
    assert backend.enable_login_item() is True
    assert backend.login_item_enabled() is True
    # The value was written under the Run key with the expected name.
    values = fake_winreg._store[(fake_winreg.HKEY_CURRENT_USER,
                                 backend_run_key())]
    assert "DevNotifier" in values

    assert backend.disable_login_item() is True
    assert backend.login_item_enabled() is False


def backend_run_key():
    import platform_backend.windows as win_mod
    return win_mod.RUN_KEY


def test_enable_login_item_writes_launch_command(backend, fake_winreg, win_mod,
                                                 monkeypatch):
    monkeypatch.setattr(win_mod, "_launch_command", lambda: '"C:\\app.exe"')
    backend.enable_login_item()
    values = fake_winreg._store[(fake_winreg.HKEY_CURRENT_USER, win_mod.RUN_KEY)]
    data, vtype = values["DevNotifier"]
    assert data == '"C:\\app.exe"'
    assert vtype == fake_winreg.REG_SZ


def test_disable_login_item_idempotent_when_absent(backend, fake_winreg):
    # Nothing registered yet; disabling should still report success.
    assert backend.disable_login_item() is True


def test_enable_login_item_returns_false_on_oserror(backend, fake_winreg,
                                                    monkeypatch):
    def boom(*a, **k):
        raise OSError("registry write denied")

    monkeypatch.setattr(fake_winreg, "CreateKey", boom)
    assert backend.enable_login_item() is False


def test_disable_login_item_returns_false_on_oserror(backend, fake_winreg,
                                                     monkeypatch):
    # A non-FileNotFound OSError from DeleteValue -> failure (returns False).
    backend.enable_login_item()

    def boom(*a, **k):
        raise OSError("access denied")

    monkeypatch.setattr(fake_winreg, "DeleteValue", boom)
    assert backend.disable_login_item() is False


def test_login_item_enabled_false_on_oserror(backend, fake_winreg, monkeypatch):
    def boom(*a, **k):
        raise OSError("cannot open key")

    monkeypatch.setattr(fake_winreg, "OpenKey", boom)
    assert backend.login_item_enabled() is False


# ---------------------------------------------------------------------------
# notifications (winotify)
# ---------------------------------------------------------------------------

def test_notify_builds_toast_with_open_action(backend, fake_winotify):
    ok = backend.notify(title="Jira", subtitle="ACME-1", message="updated",
                        data={"url": "https://x"}, sound=True)
    assert ok is True
    toast = fake_winotify._shown[-1]
    assert toast.title == "Jira"
    assert "ACME-1" in toast.msg and "updated" in toast.msg
    assert toast.actions == [{"label": "Open", "launch": "https://x"}]
    assert toast.shown is True


def test_notify_without_subtitle_uses_message_only(backend, fake_winotify):
    backend.notify(title="t", message="body only")
    toast = fake_winotify._shown[-1]
    assert toast.msg == "body only"


def test_notify_without_url_has_no_action(backend, fake_winotify):
    backend.notify(title="t", subtitle="s", message="m")
    toast = fake_winotify._shown[-1]
    assert toast.actions == []


def test_notify_passes_icon(backend, fake_winotify):
    backend.notify(title="t", message="m", icon="C:\\icon.ico")
    toast = fake_winotify._shown[-1]
    assert toast.icon == "C:\\icon.ico"


def test_notify_swallows_errors(backend, fake_winotify, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("toast subsystem down")

    monkeypatch.setattr(fake_winotify, "Notification", boom)
    # A broken toast must never raise (would crash the polling worker).
    assert backend.notify(title="t", message="m") is False


# ---------------------------------------------------------------------------
# tray UI: setup / icon / title / menu (pystray + PIL)
# ---------------------------------------------------------------------------

def test_setup_creates_icon(backend, fake_pystray, fake_pil):
    backend.setup(name="DevNotifier", icon="/tmp/orange.png")
    assert backend._icon is not None
    assert backend._icon.name == "DevNotifier"
    # The PIL image was loaded from the icon path.
    assert backend._icon.icon.path == "/tmp/orange.png"


def test_setup_without_icon(backend, fake_pystray):
    backend.setup(name="DevNotifier", icon=None)
    assert backend._icon.icon is None


def test_load_image_bad_path_returns_none(win_mod, monkeypatch):
    # PIL.Image.open raising must not propagate.
    import types as _types
    pil_img = _types.ModuleType("PIL.Image")

    def boom(path):
        raise OSError("bad image")

    pil_img.open = boom
    pil = _types.ModuleType("PIL")
    pil.Image = pil_img
    import sys
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", pil_img)
    assert win_mod.WindowsBackend._load_image("/tmp/x.png") is None


def test_run_and_quit(backend, fake_pystray, fake_pil):
    backend.setup(name="D", icon=None)
    backend.run()
    assert backend._icon.ran is True
    backend.quit()
    assert backend._icon.stopped is True


def test_quit_before_setup_is_safe(backend):
    # No icon yet -> quit is a no-op, not a crash.
    backend.quit()


def test_set_icon_updates_running_icon(backend, fake_pystray, fake_pil):
    backend.setup(name="D", icon="/tmp/a.png")
    backend.set_icon("/tmp/b.png")
    assert backend._icon.icon.path == "/tmp/b.png"


def test_set_title_updates_running_icon(backend, fake_pystray, fake_pil):
    backend.setup(name="D", icon=None)
    backend.set_title("busy")
    assert backend._icon.title == "busy"


def test_set_menu_translates_items(backend, fake_pystray, fake_pil):
    from platform_backend.base import MenuItem
    backend.setup(name="D", icon=None)

    clicked = {}
    parent = MenuItem("Parent")
    child = MenuItem("Child", callback=lambda sender: clicked.setdefault("hit", sender))
    parent.add(child)
    checked = MenuItem("On", callback=lambda s: None)
    checked.state = 1
    items = [MenuItem("Top", callback=lambda s: None), MenuItem.sep(), parent, checked]

    backend.set_menu(items)
    menu = backend._icon.menu
    # 4 entries: item, separator, submenu-parent, checked item.
    assert len(menu.items) == 4
    assert menu.items[1] is fake_pystray.Menu.SEPARATOR
    # The submenu parent wraps a nested Menu with the child.
    submenu_item = menu.items[2]
    assert submenu_item.title == "Parent"
    # Checked item exposes a checked predicate.
    assert menu.items[3].checked is not None
    assert menu.items[3].checked(None) is True


def test_menu_callback_adapts_signature(backend, fake_pystray, fake_pil):
    from platform_backend.base import MenuItem
    backend.setup(name="D", icon=None)
    got = {}
    item = MenuItem("X", callback=lambda sender: got.setdefault("sender", sender))
    backend.set_menu([item])
    entry = backend._icon.menu.items[0]
    # pystray calls action(icon, item); the adapter forwards the neutral item.
    entry.action("ICON", "PYSTRAY_ITEM")
    assert got["sender"] is item


def test_add_timer_returns_started_timer(backend):
    fired = {"n": 0}
    t = backend.add_timer(lambda _: fired.__setitem__("n", fired["n"] + 1), 999)
    assert t is not None
    t.stop()  # cancel so nothing fires during the test


def test_repeating_timer_fires_and_rearms(win_mod, monkeypatch):
    # Drive _RepeatingTimer without real threads: capture the scheduled fn and
    # invoke it manually, asserting it calls fn(None) and re-arms.
    scheduled = []

    class FakeThreadingTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn
            self.daemon = False
            self.cancelled = False

        def start(self):
            scheduled.append(self)

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(win_mod.threading, "Timer", FakeThreadingTimer)

    calls = []
    t = win_mod._RepeatingTimer(lambda arg: calls.append(arg), 5)
    t.start()
    assert len(scheduled) == 1  # armed once

    # Fire the scheduled callback -> fn(None) runs and the timer re-arms.
    scheduled[0].fn()
    assert calls == [None]
    assert len(scheduled) == 2  # re-armed

    # After stop(), a pending fire does nothing and does not re-arm.
    t.stop()
    scheduled[1].fn()
    assert calls == [None]  # fn not called again
    assert len(scheduled) == 2


def test_repeating_timer_stop_before_fire(win_mod, monkeypatch):
    monkeypatch.setattr(win_mod.threading, "Timer",
                        lambda *a, **k: type("T", (), {
                            "daemon": False, "start": lambda self: None,
                            "cancel": lambda self: None})())
    t = win_mod._RepeatingTimer(lambda _: None, 5)
    t.start()
    t.stop()  # cancels cleanly
