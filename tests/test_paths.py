"""Tests for src/paths.py — cross-platform application directories.

These verify each platform's layout by patching ``sys.platform`` and the
relevant environment variables, without touching the real machine. The macOS
paths must exactly match the historical locations so existing installs keep
finding their config.

@author SteveZou
"""
import importlib

import pytest


@pytest.fixture
def paths_mod(temp_home):
    import paths as paths_mod
    importlib.reload(paths_mod)
    return paths_mod


# ---------------------------------------------------------------------------
# macOS (unchanged historical paths)
# ---------------------------------------------------------------------------

def test_config_dir_macos(paths_mod, monkeypatch, temp_home):
    monkeypatch.setattr(paths_mod.sys, "platform", "darwin")
    assert paths_mod.config_dir() == temp_home / ".config" / "dev-notifier"


def test_cache_dir_macos(paths_mod, monkeypatch, temp_home):
    monkeypatch.setattr(paths_mod.sys, "platform", "darwin")
    assert paths_mod.cache_dir() == \
        temp_home / "Library" / "Caches" / "dev-notifier"


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def test_config_dir_windows_uses_appdata(paths_mod, monkeypatch):
    monkeypatch.setattr(paths_mod.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:\\Users\\dev\\AppData\\Roaming")
    result = paths_mod.config_dir()
    assert result.name == "dev-notifier"
    assert "Roaming" in str(result)


def test_config_dir_windows_fallback_without_appdata(paths_mod, monkeypatch,
                                                     temp_home):
    monkeypatch.setattr(paths_mod.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    result = paths_mod.config_dir()
    assert result == temp_home / "AppData" / "Roaming" / "dev-notifier"


def test_cache_dir_windows_uses_localappdata(paths_mod, monkeypatch):
    monkeypatch.setattr(paths_mod.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\dev\\AppData\\Local")
    result = paths_mod.cache_dir()
    assert result.name == "Cache"
    assert result.parent.name == "dev-notifier"


def test_cache_dir_windows_fallback_without_localappdata(paths_mod, monkeypatch,
                                                         temp_home):
    monkeypatch.setattr(paths_mod.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    result = paths_mod.cache_dir()
    assert result == \
        temp_home / "AppData" / "Local" / "dev-notifier" / "Cache"


# ---------------------------------------------------------------------------
# Linux / XDG
# ---------------------------------------------------------------------------

def test_config_dir_linux_honours_xdg(paths_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(paths_mod.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdgcfg"))
    result = paths_mod.config_dir()
    assert result == tmp_path / "xdgcfg" / "dev-notifier"


def test_config_dir_linux_default(paths_mod, monkeypatch, temp_home):
    monkeypatch.setattr(paths_mod.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    result = paths_mod.config_dir()
    assert result == temp_home / ".config" / "dev-notifier"


def test_cache_dir_linux_honours_xdg(paths_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(paths_mod.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdgcache"))
    result = paths_mod.cache_dir()
    assert result == tmp_path / "xdgcache" / "dev-notifier"


def test_cache_dir_linux_default(paths_mod, monkeypatch, temp_home):
    monkeypatch.setattr(paths_mod.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    result = paths_mod.cache_dir()
    assert result == temp_home / ".cache" / "dev-notifier"
