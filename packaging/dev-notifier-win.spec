# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller config for dev-notifier (Windows tray app).

Produces a single-file, windowed (no console) ``DevNotifier.exe``. The name is
normalized to ``DevNotifier-<version>.exe`` by packaging/windows_package.ps1 so
the in-app updater's asset matcher (``DevNotifier-*.exe``) finds it.

Build (from repo root):
    set APP_VERSION=1.0.0
    pyinstaller packaging/dev-notifier-win.spec --noconfirm
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = Path.cwd()
APP_VERSION = os.environ.get("APP_VERSION", "0.0.0")

datas = []
_menubar = PROJECT_ROOT / "assets" / "menubar"
if _menubar.exists():
    # Bundle the themed tray PNGs so the app can switch icons at runtime.
    datas.append((str(_menubar), "assets/menubar"))

# Windows .exe/tray icon: prefer a dedicated .ico, fall back to the app PNG.
_ico = PROJECT_ROOT / "assets" / "icon.ico"
_png = PROJECT_ROOT / "assets" / "app-icon.png"
if _ico.exists():
    app_icon = str(_ico)
elif _png.exists():
    app_icon = str(_png)  # PyInstaller converts PNG->ICO via Pillow when present
else:
    app_icon = None

# Bundle certifi's CA bundle so Jira/GitHub/PagerDuty TLS verification works in
# the packaged app (the frozen interpreter cannot read the OS cert store for
# some public chains -> CERTIFICATE_VERIFY_FAILED).
datas += collect_data_files("certifi")

# Bundle the Windows GUI stack: winotify (toasts), pystray (tray icon/menu),
# and PIL (icon images) including their submodules.
hiddenimports = collect_submodules("winotify")
hiddenimports += collect_submodules("pystray")
hiddenimports += ["PIL", "PIL.Image"]

a = Analysis(
    [str(PROJECT_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # rumps/PyObjC are macOS-only; never pull them into a Windows build.
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "jupyter",
              "rumps", "PyObjCTools", "AppKit", "Foundation", "objc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# One-file build: a single self-contained DevNotifier.exe.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="DevNotifier",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # windowed tray app, no console window
    icon=app_icon,
    version=None,
)
