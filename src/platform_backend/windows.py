"""Windows platform backend.

Implements the full :class:`~platform_backend.base.TrayBackend` interface for
Windows:

- Tray icon + menu via ``pystray`` (with ``Pillow`` for the icon image); the
  app's toolkit-neutral :class:`~platform_backend.base.MenuItem`s are translated
  into ``pystray`` menu items, including submenus and checkmarks.
- Native toast notifications via ``winotify``; a clickable "Open" action carries
  the URL so clicking the toast opens the relevant page.
- ``open_url`` uses ``os.startfile``.
- Start-at-login is a value under the per-user ``Run`` registry key
  (``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``), managed via the
  stdlib ``winreg`` module — the Windows analogue of the macOS LaunchAgent.
- Repeating timers use ``threading.Timer`` re-armed on each fire; unlike AppKit
  there is no main-run-loop affinity, so ``run_on_main`` applies results inline.

All Windows-only imports (``winreg``, ``winotify``, ``pystray``, ``PIL``,
``os.startfile``) are done lazily so this module is importable — and
unit-testable via stubs — on macOS/Linux CI.

@author SteveZou
"""
import os
import sys
import threading
from pathlib import Path

from platform_backend.base import MenuItem, TrayBackend, Timer

# Matches the macOS LaunchAgent label so the two platforms stay recognizable.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "DevNotifier"


def _launch_command() -> str:
    """Command string Windows runs at login to (re)start the app.

    Frozen (PyInstaller ``.exe``): just the executable path, quoted.
    From source: the current interpreter running ``launcher.py``, both quoted.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    launcher = Path(__file__).resolve().parent.parent.parent / "launcher.py"
    return f'"{sys.executable}" "{launcher}"'


class _RepeatingTimer(Timer):
    """A repeating timer built on ``threading.Timer``, re-armed on each fire.

    ``fn`` is called with a single positional ``None`` to match the callback
    signature the app's timer handlers expect.
    """

    def __init__(self, fn, interval_s):
        self._fn = fn
        self._interval = interval_s
        self._timer = None
        self._stopped = False

    def _run(self):
        if self._stopped:
            return
        try:
            self._fn(None)
        finally:
            if not self._stopped:
                self._arm()

    def _arm(self):
        self._timer = threading.Timer(self._interval, self._run)
        self._timer.daemon = True
        self._timer.start()

    def start(self):
        self._stopped = False
        self._arm()

    def stop(self):
        self._stopped = True
        if self._timer is not None:
            self._timer.cancel()


class WindowsBackend(TrayBackend):
    """Backend for Windows (win32)."""

    def __init__(self):
        self._icon = None          # pystray.Icon
        self._name = "DevNotifier"
        self._icon_path = None
        self._title = None

    # -- lifecycle ----------------------------------------------------------
    def setup(self, name, icon):
        import pystray

        self._name = name or "DevNotifier"
        self._icon_path = icon
        self._icon = pystray.Icon(self._name, icon=self._load_image(icon),
                                  title=self._name)

    def run(self):
        self._icon.run()

    def quit(self):
        if self._icon is not None:
            self._icon.stop()

    def run_on_main(self, fn):
        # No main-run-loop affinity for pystray/winotify; apply the result
        # directly. Signature matches the macOS backend: fn is called with None.
        fn(None)

    # -- tray appearance / menu --------------------------------------------
    @staticmethod
    def _load_image(path):
        """Load an icon file into a PIL image, or None if unavailable."""
        if not path:
            return None
        try:
            from PIL import Image

            return Image.open(path)
        except Exception:  # noqa: BLE001 - a missing/bad icon must not crash
            return None

    def set_icon(self, path):
        self._icon_path = path
        if self._icon is not None:
            img = self._load_image(path)
            if img is not None:
                self._icon.icon = img

    def set_title(self, title):
        self._title = title
        if self._icon is not None and title:
            self._icon.title = title

    def _to_pystray(self, item: MenuItem):
        """Translate a neutral MenuItem into a pystray.MenuItem (recursively)."""
        import pystray

        if item.separator:
            return pystray.Menu.SEPARATOR

        if item.children:
            submenu = pystray.Menu(*[self._to_pystray(c) for c in item.children])
            return pystray.MenuItem(item.title, submenu)

        cb = item.callback
        # pystray invokes callbacks as fn(icon, item); the app's handlers take a
        # single "sender" argument, so adapt by passing the neutral item through.
        action = (lambda icon, _it, _cb=cb, _item=item: _cb(_item)) if cb else None
        return pystray.MenuItem(
            item.title, action,
            checked=(lambda _it, _s=item.state: bool(_s)) if item.state else None,
            enabled=cb is not None,
        )

    def set_menu(self, items):
        import pystray

        menu = pystray.Menu(*[self._to_pystray(i) for i in items])
        if self._icon is not None:
            self._icon.menu = menu

    def add_timer(self, fn, interval_s):
        t = _RepeatingTimer(fn, interval_s)
        t.start()
        return t

    # -- notifications ------------------------------------------------------
    def notify(self, title="", subtitle="", message="", data=None, sound=False,
               icon=None, action_button=None):
        """Show a Windows toast; a clickable "Open" action carries the URL.

        ``action_button`` is accepted for cross-backend signature parity; on
        Windows the "Open" action is derived from ``data['url']`` below, so the
        parameter is not otherwise needed.

        Best-effort: never raises so a failing notification backend cannot crash
        a worker thread (matching the macOS app's swallow-on-error behaviour).
        """
        try:
            from winotify import Notification

            url = (data or {}).get("url")
            # winotify has no subtitle; fold it into the body for parity with
            # the macOS title/subtitle/message layout.
            body = message if not subtitle else f"{subtitle}\n{message}"
            kwargs = {"app_id": "Dev Notifier", "title": title, "msg": body}
            if icon:
                kwargs["icon"] = icon
            toast = Notification(**kwargs)
            if url:
                toast.add_actions(label="Open", launch=url)
            toast.show()
            return True
        except Exception:  # noqa: BLE001 - a broken toast must not crash polling
            return False

    # -- system integration -------------------------------------------------
    def open_url(self, url):
        # os.startfile exists only on Windows; imported via os at call time.
        os.startfile(url)  # noqa: S606 - opening a user/action URL by design

    # -- start at login (HKCU Run registry value) ---------------------------
    def login_item_enabled(self):
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return True
        except OSError:
            return False

    def enable_login_item(self):
        import winreg

        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(
                    key, RUN_VALUE_NAME, 0, winreg.REG_SZ, _launch_command()
                )
            return True
        except OSError:
            return False

    def disable_login_item(self):
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            return True
        except FileNotFoundError:
            # Value already absent — treat as success (idempotent disable).
            return True
        except OSError:
            return False
