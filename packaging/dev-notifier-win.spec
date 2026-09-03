# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller config for dev-notifier (Windows tray app).

Produces a single-file, windowed (no console) ``dist/DevNotifier.exe`` that
carries the release version in three places:

- ``sys._MEIPASS/APP_VERSION``  data file read by ``updater.current_version()``
  (this is what keeps the in-app updater from believing it is forever out of
  date);
- a Windows VERSIONINFO resource (Properties -> Details, SmartScreen metadata);
- ``build/app-icon.ico`` generated from ``assets/app-icon.png`` for the exe,
  the tray and the Inno Setup installer.

packaging/windows_package.ps1 then renames the exe to
``DevNotifier-<version>-portable.exe`` and wraps it in an Inno Setup installer
(``DevNotifier-<version>-setup.exe``), which is the asset the updater prefers.

Build (from repo root):
    set APP_VERSION=1.0.0
    pyinstaller packaging/dev-notifier-win.spec --noconfirm
"""
import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = Path.cwd()
APP_VERSION = os.environ.get("APP_VERSION", "0.0.0")
APP_NAME = "DevNotifier"
APP_DISPLAY_NAME = "Dev Notifier"
APP_AUTHOR = "SteveZou"

BUILD_DIR = PROJECT_ROOT / "build"
BUILD_DIR.mkdir(parents=True, exist_ok=True)


def _numeric_version(v: str):
    """``1.5.9`` / ``1.5.9-rc.1`` -> ``(1, 5, 9, 0)`` for the VERSIONINFO block."""
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", v or "")
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(g) for g in m.groups()) + (0,)


# --- version stamp (read at runtime by updater.current_version) -------------
_stamp = BUILD_DIR / "APP_VERSION"
_stamp.write_text(APP_VERSION + "\n", encoding="utf-8")

# --- Windows VERSIONINFO resource ------------------------------------------
_nums = _numeric_version(APP_VERSION)
_version_file = BUILD_DIR / "win_version_info.txt"
_version_file.write_text(
    f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_nums!r},
    prodvers={_nums!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', {APP_AUTHOR!r}),
        StringStruct('FileDescription', {APP_DISPLAY_NAME!r}),
        StringStruct('FileVersion', {APP_VERSION!r}),
        StringStruct('InternalName', {APP_NAME!r}),
        StringStruct('OriginalFilename', {APP_NAME + '.exe'!r}),
        StringStruct('ProductName', {APP_DISPLAY_NAME!r}),
        StringStruct('ProductVersion', {APP_VERSION!r})
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)

# --- icon: prefer a committed .ico, else render one from the PNG -----------
_ico = PROJECT_ROOT / "assets" / "icon.ico"
_png = PROJECT_ROOT / "assets" / "app-icon.png"
app_icon = None
if _ico.exists():
    app_icon = str(_ico)
elif _png.exists():
    _generated_ico = BUILD_DIR / "app-icon.ico"
    try:
        from PIL import Image

        with Image.open(_png) as im:
            im.save(
                _generated_ico, format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                       (128, 128), (256, 256)],
            )
        app_icon = str(_generated_ico)
    except Exception as e:  # noqa: BLE001 - icon is cosmetic; never fail the build
        print(f"WARNING: could not render {_generated_ico}: {e}; using PNG")
        app_icon = str(_png)  # PyInstaller converts PNG->ICO via Pillow

datas = [(str(_stamp), ".")]
_menubar = PROJECT_ROOT / "assets" / "menubar"
if _menubar.exists():
    # Bundle the themed tray PNGs so the app can switch icons at runtime.
    datas.append((str(_menubar), "assets/menubar"))

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
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # windowed tray app, no console window
    icon=app_icon,
    version=str(_version_file),
)
