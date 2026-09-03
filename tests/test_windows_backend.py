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
    monkeypatch.setattr(win_mod, "_installed_exe", lambda: None)
    cmd = win_mod._launch_command()
    assert cmd == '"C:\\Program Files\\DevNotifier\\DevNotifier.exe"'
    assert "launcher.py" not in cmd


def test_launch_command_frozen_prefers_installed_exe(win_mod, monkeypatch):
    # A portable exe run from Downloads must not pin start-at-login to itself
    # when the installer has registered a proper install location.
    monkeypatch.setattr(win_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(win_mod.sys, "executable",
                        r"C:\Users\me\Downloads\DevNotifier-1.5.8-portable.exe",
                        raising=False)
    monkeypatch.setattr(win_mod, "_installed_exe",
                        lambda: r"C:\Users\me\AppData\Local\Programs\DevNotifier\DevNotifier.exe")
    cmd = win_mod._launch_command()
    assert cmd == '"C:\\Users\\me\\AppData\\Local\\Programs\\DevNotifier\\DevNotifier.exe"'


# ---------------------------------------------------------------------------
# installed-exe lookup (Inno Setup uninstall key)
# ---------------------------------------------------------------------------

def test_installed_exe_none_without_winreg(win_mod, monkeypatch):
    # No winreg module at all (macOS/Linux) -> not installed.
    import builtins
    real_import = builtins.__import__

    def no_winreg(name, *a, **k):
        if name == "winreg":
            raise ImportError("no winreg")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_winreg)
    assert win_mod._installed_exe() is None


def test_installed_exe_none_when_key_absent(win_mod, fake_winreg):
    assert win_mod._installed_exe() is None


def test_installed_exe_reads_hkcu_install_location(win_mod, fake_winreg, tmp_path):
    install_dir = tmp_path / "DevNotifier"
    install_dir.mkdir()
    exe = install_dir / win_mod.APP_EXE_NAME
    exe.write_bytes(b"MZ")
    fake_winreg._store[(fake_winreg.HKEY_CURRENT_USER, win_mod.UNINSTALL_KEY)] = {
        "InstallLocation": (str(install_dir), fake_winreg.REG_SZ),
    }
    assert win_mod._installed_exe() == str(exe)


def test_installed_exe_falls_back_to_hklm(win_mod, fake_winreg, tmp_path):
    install_dir = tmp_path / "DevNotifier"
    install_dir.mkdir()
    exe = install_dir / win_mod.APP_EXE_NAME
    exe.write_bytes(b"MZ")
    # HKCU has an empty location; HKLM (all-users install) has the real one.
    fake_winreg._store[(fake_winreg.HKEY_CURRENT_USER, win_mod.UNINSTALL_KEY)] = {
        "InstallLocation": ("", fake_winreg.REG_SZ),
    }
    fake_winreg._store[(fake_winreg.HKEY_LOCAL_MACHINE, win_mod.UNINSTALL_KEY)] = {
        "InstallLocation": (str(install_dir), fake_winreg.REG_SZ),
    }
    assert win_mod._installed_exe() == str(exe)


def test_installed_exe_none_when_recorded_exe_missing(win_mod, fake_winreg, tmp_path):
    # Registry says installed, but the exe was deleted -> None (fall back to
    # the running executable rather than a dead path).
    fake_winreg._store[(fake_winreg.HKEY_CURRENT_USER, win_mod.UNINSTALL_KEY)] = {
        "InstallLocation": (str(tmp_path / "gone"), fake_winreg.REG_SZ),
    }
    assert win_mod._installed_exe() is None


def test_installer_app_id_matches_iss_script(win_mod):
    # The uninstall key is derived from the Inno Setup AppId; keep them in sync.
    from pathlib import Path
    iss = (Path(__file__).resolve().parent.parent / "packaging"
           / "windows_installer.iss").read_text(encoding="utf-8")
    # Inno escapes a leading brace as "{{"; the registry key uses a single one.
    assert f"AppId={{{win_mod.INSTALLER_APP_ID}" in iss
    assert f'#define RunValueName "{win_mod.RUN_VALUE_NAME}"' in iss


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


def test_menu_callbacks_satisfy_pystray_arity(win_mod):
    """Real pystray raises ValueError unless ``__code__.co_argcount`` is 0, 1
    or 2 — parameters with defaults count. The previous
    ``lambda icon, it, _cb=cb, _item=item`` had four and crashed every menu
    build at startup on Windows."""
    cb = lambda sender: None  # noqa: E731
    action = win_mod._make_action(cb, object())
    assert action.__code__.co_argcount == 2
    checked = win_mod._make_checked(1)
    assert checked.__code__.co_argcount == 1
    assert checked(None) is True
    assert win_mod._make_checked(0)(None) is False


def test_fake_pystray_rejects_over_arity_actions(fake_pystray):
    """Guard the guard: the stub must reject what real pystray rejects."""
    import pytest as _pytest
    with _pytest.raises(ValueError):
        fake_pystray.MenuItem("x", lambda icon, it, a=1, b=2: None)
    fake_pystray.MenuItem("ok", lambda icon, it: None)  # 2 args fine
    fake_pystray.MenuItem("sub", fake_pystray.Menu())    # submenu fine


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


class _FakeThreadingTimer:
    scheduled = []

    def __init__(self, interval, fn):
        self.interval = interval
        self.fn = fn
        self.daemon = False
        self.cancelled = False

    def start(self):
        self.scheduled.append(self)

    def cancel(self):
        self.cancelled = True


def test_repeating_timer_fires_and_rearms(win_mod, monkeypatch):
    # Drive _RepeatingTimer without real threads: capture the scheduled fn and
    # invoke it manually, asserting it passes the timer itself as the sender
    # (so one-shot handlers can call ``sender.stop()`` like on macOS) and
    # re-arms.
    _FakeThreadingTimer.scheduled = scheduled = []
    monkeypatch.setattr(win_mod.threading, "Timer", _FakeThreadingTimer)

    calls = []
    t = win_mod._RepeatingTimer(lambda arg: calls.append(arg), 5)
    t.start()
    assert len(scheduled) == 1  # armed once

    # Fire the scheduled callback -> fn(timer) runs and the timer re-arms.
    scheduled[0].fn()
    assert calls == [t]
    assert len(scheduled) == 2  # re-armed

    # After stop(), a pending fire does nothing and does not re-arm.
    t.stop()
    scheduled[1].fn()
    assert calls == [t]  # fn not called again
    assert len(scheduled) == 2


def test_repeating_timer_one_shot_handler_can_stop_itself(win_mod, monkeypatch):
    """The app's startup handlers call ``sender.stop()``; on Windows this used
    to raise (sender was None) on every fire and loop forever."""
    _FakeThreadingTimer.scheduled = scheduled = []
    monkeypatch.setattr(win_mod.threading, "Timer", _FakeThreadingTimer)
    fired = []

    def one_shot(sender):
        sender.stop()
        fired.append(1)

    t = win_mod._RepeatingTimer(one_shot, 0.5)
    t.start()
    scheduled[0].fn()
    assert fired == [1]
    assert len(scheduled) == 1  # stopped inside the handler -> not re-armed


def test_repeating_timer_survives_handler_exception(win_mod, monkeypatch):
    _FakeThreadingTimer.scheduled = scheduled = []
    monkeypatch.setattr(win_mod.threading, "Timer", _FakeThreadingTimer)

    def boom(_):
        raise RuntimeError("tick failed")

    t = win_mod._RepeatingTimer(boom, 5)
    t.start()
    scheduled[0].fn()  # must not raise
    assert len(scheduled) == 2  # still re-armed
    t.stop()


def test_repeating_timer_stop_before_fire(win_mod, monkeypatch):
    monkeypatch.setattr(win_mod.threading, "Timer",
                        lambda *a, **k: type("T", (), {
                            "daemon": False, "start": lambda self: None,
                            "cancel": lambda self: None})())
    t = win_mod._RepeatingTimer(lambda _: None, 5)
    t.start()
    t.stop()  # cancels cleanly


# ---------------------------------------------------------------------------
# Smoke test against the REAL pystray classes (runs wherever pystray imports:
# the Windows CI job and any dev box with it installed; skipped elsewhere).
# Every other test here uses the fake_pystray stub, which is only as strict as
# we remember to make it — this one builds the app's complete menu through
# pystray's own MenuItem/Menu validation, which is exactly where 2.0.0-beta
# crashed at startup on Windows.
# ---------------------------------------------------------------------------

def test_real_pystray_accepts_full_app_menu(temp_home, monkeypatch):
    pystray = pytest.importorskip("pystray")
    import importlib
    import config as config_mod
    importlib.reload(config_mod)
    import notifier_app
    importlib.reload(notifier_app)
    from conftest import FakeBackend
    import platform_backend.windows as win_mod

    # Build the menu the app really shows (all submenus, checkmarks, disabled
    # lines, Recent entries, PagerDuty levels) with the recording backend...
    app = notifier_app.NotifierApp(backend=FakeBackend())
    app.dep_status = {"ok": True, "problems": [], "pending": False,
                      "jira_ok": True, "jira": {"enabled": True},
                      "github_ok": True, "pagerduty_ok": True,
                      "gh": {"installed": True, "authed": True}}
    app.cfg["pagerduty"] = {"enabled": True}
    app.pd_status = {"on_call": True, "until": None, "schedule": "Ops",
                     "url": "https://pd/ep/P", "level": 1,
                     "shifts": [{"level": 1, "name": "Ops", "until": None,
                                 "url": "https://pd/ep/P", "direct": True},
                                {"level": 3, "name": "FE-ep", "until": None,
                                 "url": "https://pd/ep/Q", "direct": True}],
                     "next_start": None,
                     "active_incidents": [{"number": 1, "status": "triggered",
                                           "urgency": "high", "title": "Disk",
                                           "url": "https://pd/1"}]}
    app.recent = [{"id": 1, "label": "ACME-1 · Done — fixed", "url": "https://j/1"}]
    app.update_info = {"available": True, "latest": "9.9.9", "html_url": "u"}
    app.cfg_problems = ["unknown setting 'jira.supress_self'"]
    app._build_menu()

    # ... then translate every item with the real pystray classes. A callback
    # with the wrong arity raises ValueError right here, like it did at launch.
    backend = win_mod.WindowsBackend()
    translated = [backend._to_pystray(i) for i in app.menu]
    menu = pystray.Menu(*translated)

    def walk(m):
        for it in m.items:
            if it is pystray.Menu.SEPARATOR:
                continue
            yield it
            if it.submenu is not None:
                yield from walk(it.submenu)

    items = list(walk(menu))
    assert len(items) > 20
    # Properties resolve without error and callbacks fire back into the app.
    for it in items:
        it.text, it.checked, it.enabled, it.visible
    theme_menu = next(it for it in items if it.text == "Theme").submenu
    green = next(it for it in theme_menu.items if it.text == "Green")
    green(icon=None)  # pystray calls action(icon, item) via __call__
    assert app.cfg["theme"] == "Green"
    on_call_row = next(it for it in items if it.text.startswith("Fallback on-call"))
    on_call_row(icon=None)
    assert app.backend.opened_urls[-1] == "https://pd/ep/Q"
