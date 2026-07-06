"""Platform backend interface and neutral menu data structures.

A *backend* encapsulates every OS-specific integration the app needs: the tray
icon + menu, native notifications, timers, opening URLs, and the start-at-login
toggle. ``notifier_app`` talks to this interface instead of any single GUI
toolkit, so a new platform is added by implementing a new backend rather than
editing the app logic.

This module is toolkit-agnostic and imports nothing platform-specific, so it is
safe to import on any OS (including Linux CI).

@author SteveZou
"""
from abc import ABC, abstractmethod
from typing import Callable, List, Optional


class MenuItem:
    """A toolkit-neutral menu entry.

    Backends translate this into their native representation (``rumps.MenuItem``
    on macOS, ``pystray.MenuItem`` on Windows). ``callback`` of ``None`` marks a
    non-interactive (disabled) line; a ``separator`` renders a divider and
    ignores the other fields; nested children (added via :meth:`add`) produce a
    submenu.

    The attribute surface (``title``, ``callback``, ``state``, ``_children``,
    ``add``, ``set_icon``) deliberately mirrors the small slice of the rumps
    ``MenuItem`` API the app used, so menu-building code and its tests stay
    toolkit-agnostic. Arbitrary tag attributes (e.g. ``theme_name``,
    ``entry_id``) can be attached freely, as the app does to carry click
    context.
    """

    def __init__(self, title="", callback=None, separator=False, icon=None):
        self.title = title
        self.callback = callback
        self.separator = separator
        self.icon = icon
        # ``state`` mirrors rumps' checkmark flag (0/1). Kept as an int so the
        # existing ``item.state = 1`` / ``state == 1`` idioms work unchanged.
        self.state = 0
        self._children: List["MenuItem"] = []

    @property
    def children(self):
        return self._children

    def add(self, item: "MenuItem") -> None:
        """Append a child item (produces a submenu)."""
        self._children.append(item)

    def set_icon(self, path, dimensions=None, template=False):
        """Attach an icon path (backends decide how/whether to render it)."""
        self.icon = path

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
    def setup(self, name: str, icon: Optional[str]) -> None:
        """Create the underlying tray/app with an initial name and icon."""

    @abstractmethod
    def run(self) -> None:
        """Start the platform event loop (blocking)."""

    @abstractmethod
    def quit(self) -> None:
        """Terminate the app / stop the event loop."""

    @abstractmethod
    def run_on_main(self, fn: Callable) -> None:
        """Schedule ``fn`` to run on the UI/main thread.

        Worker threads use this to apply results safely. ``fn`` is called with a
        single positional argument (``None``) to match the existing app
        callbacks.
        """

    # -- tray appearance / menu --------------------------------------------
    @abstractmethod
    def set_icon(self, path: Optional[str]) -> None:
        """Set the tray icon to the image at ``path`` (or clear it)."""

    @abstractmethod
    def set_title(self, title: Optional[str]) -> None:
        """Set the tray title/tooltip text (fallback when no icon)."""

    @abstractmethod
    def set_menu(self, items: List[MenuItem]) -> None:
        """Replace the tray menu with ``items`` (neutral MenuItems)."""

    @abstractmethod
    def add_timer(self, fn: Callable, interval_s: float) -> "Timer":
        """Create and start a repeating timer calling ``fn`` every interval."""

    # -- notifications ------------------------------------------------------
    @abstractmethod
    def notify(self, title="", subtitle="", message="", data=None, sound=False,
               icon=None) -> None:
        """Show a native notification. ``data`` may carry a ``url`` to open."""

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


class Timer:
    """Minimal repeating-timer handle returned by :meth:`TrayBackend.add_timer`.

    Backends subclass or duck-type this; the app only calls ``start``/``stop``.
    """

    def start(self):  # pragma: no cover - overridden by concrete backends
        raise NotImplementedError

    def stop(self):  # pragma: no cover - overridden by concrete backends
        raise NotImplementedError
