"""Platform backend selection.

``get_backend()`` returns the concrete :class:`~platform_backend.base.TrayBackend`
for the current OS, chosen by ``sys.platform``. This is the single dispatch
point the app uses; adding a new platform means adding a branch here plus a new
backend module (see docs/windows-support-plan.md).

The concrete backend module is imported lazily inside the selected branch so
that importing this package never pulls in another platform's toolkit (e.g.
importing ``pystray`` on macOS, or ``rumps`` on Windows).

@author SteveZou
"""
import sys

from platform_backend.base import MenuItem, TrayBackend

__all__ = ["MenuItem", "TrayBackend", "get_backend"]


def get_backend(platform: str = None) -> TrayBackend:
    """Return the backend for ``platform`` (defaults to ``sys.platform``).

    Raises ``NotImplementedError`` for platforms without a backend yet, so an
    unsupported OS fails loudly instead of silently misbehaving.
    """
    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        from platform_backend.macos import MacOSBackend

        return MacOSBackend()
    if plat == "win32":
        from platform_backend.windows import WindowsBackend

        return WindowsBackend()
    raise NotImplementedError(f"No tray backend for platform: {plat!r}")
