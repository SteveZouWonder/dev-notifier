# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller config for dev-notifier (macOS menu-bar app).

Build (from repo root):
    APP_VERSION=1.0.0 pyinstaller packaging/dev-notifier.spec --noconfirm
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = Path.cwd()
APP_VERSION = os.environ.get("APP_VERSION", "0.0.0")

datas = []
_menubar = PROJECT_ROOT / "assets" / "menubar"
if _menubar.exists():
    # Bundle the themed menu-bar PNGs so the app can switch icons at runtime.
    datas.append((str(_menubar), "assets/menubar"))
_icon = PROJECT_ROOT / "assets" / "icon.icns"
app_icon = str(_icon) if _icon.exists() else None

# Bundle certifi's CA bundle so Jira (and other https) TLS verification works
# in the packaged app; the system Python's default store cannot verify some
# public certificate chains (CERTIFICATE_VERIFY_FAILED).
datas += collect_data_files("certifi")

hiddenimports = collect_submodules("rumps")
# notifier_app imports PyObjCTools.AppHelper to marshal worker-thread results
# back onto the main run loop; ensure PyInstaller bundles it explicitly.
hiddenimports += ["PyObjCTools", "PyObjCTools.AppHelper"]

a = Analysis(
    [str(PROJECT_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "jupyter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DevNotifier",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # GUI/agent app, no console window
    icon=app_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="DevNotifier",
)

app = BUNDLE(
    coll,
    name="DevNotifier.app",
    icon=app_icon,
    bundle_identifier="ai.stevezou.devnotifier",
    info_plist={
        "CFBundleName": "DevNotifier",
        "CFBundleDisplayName": "Dev Notifier",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
        # Menu-bar agent: no Dock icon, no main window.
        "LSUIElement": True,
    },
)
