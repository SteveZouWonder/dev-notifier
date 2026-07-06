"""Platform backend interface and neutral menu data structures.

A *backend* encapsulates every OS-specific integration the app needs: the tray
icon + menu, native notifications, timers, opening URLs, and the start-at-login
toggle. ``notifier_app`` talks to this interface instead of any single GUI
toolkit, so a new platform is added by implementing a new backend rather than
editing the app logic.

This module is toolkit-agnostic and imports nothing platform-specific, so it is
safe to import on any OS (including Linux CI).

Rollout note (see docs/windows-support-plan.md): P1 introduces this interface
and the macOS backend for the *system-integration* pieces (open URL, login
item, paths). The tray/menu UI is still driven by ``rumps`` directly in
``notifier_app`` and will move behind ``set_menu``/``notify`` in P2, alongside
the Windows backend.

@author SteveZou
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class MenuItem:
    """A toolkit-neutral menu entry.

    Backends translate this into their native representation (``rumps.MenuItem``
    on macOS, ``pystray.MenuItem`` on Windows). ``callback`` of ``None`` marks a
    non-interactive (disabled) line. ``separator`` renders a divider and ignores
    the other fields. Nested ``children`` produce a submenu.
    """

    title: str = ""
    callback: Optional[Callable] = None
    checked: bool = False
    icon: Optional[str] = None
    separator: bool = False
    children: List["MenuItem"] = field(default_factory=list)

    @classmethod
    def sep(cls) -> "MenuItem":
        """Convenience constructor for a divider line."""
        return cls(separator=True)


class TrayBackend(ABC):
    """Abstract tray/menu-bar backend.

    Concrete backends must keep all GUI-toolkit calls on the platform's main
    thread; worker threads hand results back via :meth:`run_on_main`.
    """

    # -- lifecycle ----------------------------------------------------------
    @abstractmethod
    def run_on_main(self, fn: Callable) -> None:
        """Schedule ``fn`` to run on the UI/main thread.

        Worker threads use this to apply results safely. ``fn`` is called with a
        single positional argument (``None``) to match the existing app
        callbacks.
        """

    # -- system integration -------------------------------------------------
    @abstractmethod
    def open_url(self, url: str) -> None:
        """Open ``url`` (or a local file path) with the OS default handler."""

    @abstractmethod
    def login_item_enabled(self) -> bool:
        """True if the app is registered to start at login."""

    @abstractmethod
    def enable_login_item(self) -> bool:
        """Register the app to start at login. Returns success."""

    @abstractmethod
    def disable_login_item(self) -> bool:
        """Unregister the app from starting at login. Returns success."""
