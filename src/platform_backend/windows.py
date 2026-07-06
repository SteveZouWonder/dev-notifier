"""Windows platform backend.

Implements the :class:`~platform_backend.base.TrayBackend` system-integration
surface for Windows using the standard library plus lightweight, Windows-only
third-party packages:

- ``open_url`` uses ``os.startfile`` (the OS "open with default handler" call).
- Start-at-login is a value under the per-user ``Run`` registry key
  (``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``), managed via the
  stdlib ``winreg`` module. This is reversible and touches only the current
  user's hive — the Windows analogue of the macOS LaunchAgent.
- Notifications use ``winotify``; a clickable "Open" action carries the URL so
  clicking the toast opens the relevant page (matching the macOS click-to-open
  behaviour).
- ``run_on_main`` runs the callback inline. Unlike AppKit, Windows toast/tray
  work here has no main-run-loop affinity, so worker threads can apply results
  directly. (When the tray UI is moved behind this backend, this will be
  revisited to marshal onto the pystray thread.)

All Windows-only imports (``winreg``, ``winotify``, ``os.startfile``) are done
lazily inside methods so this module is importable — and unit-testable via
stubs — on macOS/Linux CI.

@author SteveZou
"""
import os
import sys
from pathlib import Path

from platform_backend.base import TrayBackend

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


class WindowsBackend(TrayBackend):
    """Backend for Windows (win32)."""

    def run_on_main(self, fn):
        # No main-run-loop affinity for the current (notification-only) surface;
        # apply the result directly. Signature matches the macOS backend: fn is
        # called with a single positional None.
        fn(None)

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

    # -- notifications ------------------------------------------------------
    def notify(self, title, subtitle="", message="", url=None, sound=False,
               icon=None):
        """Show a Windows toast; a clickable "Open" action carries ``url``.

        Best-effort: never raises so a failing notification backend cannot crash
        a worker thread (matching the macOS app's swallow-on-error behaviour).
        """
        try:
            from winotify import Notification

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
