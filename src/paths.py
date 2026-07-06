"""Cross-platform application directories.

Centralizes where the app keeps its config, state, log and cache so the rest of
the code never hardcodes an OS-specific path. This is the foundation for
supporting platforms beyond macOS (see docs/windows-support-plan.md).

Design goals:

- **macOS behaviour is unchanged.** The config directory stays at
  ``~/.config/dev-notifier`` and the cache at ``~/Library/Caches/dev-notifier``,
  exactly matching the paths the app has always used, so existing installs keep
  reading/writing the same files.
- **Standard-library only.** No third-party dependency is introduced at this
  stage; paths are derived from ``sys.platform`` and environment variables that
  are always available.

Per-platform layout::

    macOS    config/state/log -> ~/.config/dev-notifier
             cache            -> ~/Library/Caches/dev-notifier
    Windows  config/state/log -> %APPDATA%/dev-notifier
             cache            -> %LOCALAPPDATA%/dev-notifier/Cache
    Linux    config/state/log -> $XDG_CONFIG_HOME/dev-notifier (~/.config/...)
             cache            -> $XDG_CACHE_HOME/dev-notifier (~/.cache/...)

@author SteveZou
"""
import os
import sys
from pathlib import Path

APP_NAME = "dev-notifier"


def _home() -> Path:
    return Path.home()


def config_dir() -> Path:
    """Directory for config, state and log files.

    macOS keeps the historical ``~/.config/dev-notifier`` location so existing
    user configs are found unchanged.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else _home() / "AppData" / "Roaming"
        return root / APP_NAME
    if sys.platform == "darwin":
        return _home() / ".config" / APP_NAME
    # Linux / other POSIX: honour XDG_CONFIG_HOME.
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else _home() / ".config"
    return root / APP_NAME


def cache_dir() -> Path:
    """Directory for downloaded artifacts (e.g. update installers)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else _home() / "AppData" / "Local"
        return root / APP_NAME / "Cache"
    if sys.platform == "darwin":
        return _home() / "Library" / "Caches" / APP_NAME
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else _home() / ".cache"
    return root / APP_NAME
