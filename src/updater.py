"""Self-update checks against GitHub Releases.

This project ships as an unsigned, ad-hoc-signed ``.app`` inside a ``.dmg`` on
GitHub Releases. Fully silent, in-place replacement of an unsigned app is risky
on macOS (Gatekeeper / quarantine), so instead we:

  1. Read the running app's version from the ``APP_VERSION`` stamp the build
     bundles (frozen, all platforms), else ``Info.plist`` (macOS), else the
     fallback ``__version__`` (source runs).
  2. Query the GitHub Releases API for the newest ``vX.Y.Z`` release.
  3. If newer, surface it in the menu bar and via a clickable notification.
  4. On the user's request, download the platform's installer to a cache dir,
     verify its SHA-256 against the release's ``SHA256SUMS.txt``, then open it:
     macOS ``open``s the DMG so the user drags the new app into /Applications;
     Windows launches the Inno Setup ``-setup.exe``, which closes the running
     app, replaces it in place and relaunches it.

All network + disk work here is blocking and MUST be called from a worker
thread; the caller hands UI results back to the main thread. Nothing in this
module touches AppKit/rumps directly.

No third-party dependencies (stdlib ``urllib`` only), so the PyInstaller spec
needs no changes.

@author SteveZou
"""
import json
import os
import plistlib
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

import paths as _paths

# GitHub repo that publishes the releases. Kept here (not in user config) so a
# user's local config cannot point the updater at an arbitrary host.
GITHUB_OWNER = "SteveZouWonder"
GITHUB_REPO = "dev-notifier"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Fallback version when running from source (not a PyInstaller bundle). Frozen
# builds do NOT rely on this: the PyInstaller specs stamp the release version
# into the bundle (``APP_VERSION`` data file on both platforms, plus Info.plist
# on macOS), and ``current_version()`` reads that first. From-source runs skip
# update prompts anyway.
__version__ = "1.5.8"

# Name of the version stamp file the PyInstaller specs bundle next to the app's
# data (``sys._MEIPASS/APP_VERSION``). Contents: the bare version, e.g. 1.5.9.
VERSION_STAMP_NAME = "APP_VERSION"

_USER_AGENT = f"{GITHUB_REPO}-updater"
_HTTP_TIMEOUT = 15  # seconds
_DMG_NAME_RE = re.compile(r"DevNotifier-.*\.dmg$", re.IGNORECASE)
# Windows assets (published alongside the macOS DMG on the same release):
#   DevNotifier-<ver>-setup.exe     Inno Setup installer  (preferred)
#   DevNotifier-<ver>-portable.exe  bare one-file exe     (fallback)
# The updater launches the installer, which replaces the installed copy; the
# portable exe is only used when no installer asset exists on the release.
_EXE_NAME_RE = re.compile(r"DevNotifier-.*\.exe$", re.IGNORECASE)
_SETUP_EXE_RE = re.compile(r"DevNotifier-.*-setup\.exe$", re.IGNORECASE)

# Cache dir for downloaded installers. Resolved via the cross-platform paths
# helper; on macOS this is exactly the historical ~/Library/Caches/dev-notifier
# location, so existing behaviour and tests are unchanged.
CACHE_DIR = _paths.cache_dir()


def _ssl_context() -> ssl.SSLContext:
    """Return an SSL context that can verify public CAs.

    The stock macOS system Python (and a PyInstaller bundle) does not read the
    keychain, so the default CA store often cannot verify ``api.github.com``'s
    certificate chain (CERTIFICATE_VERIFY_FAILED), which made "Check for
    updates" fail with "Couldn't check". Prefer ``certifi``'s bundle when
    available; fall back to the default context otherwise.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi missing or unreadable bundle
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


# ---------------------------------------------------------------------------
# version handling
# ---------------------------------------------------------------------------

def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle (a real installed app)."""
    return bool(getattr(sys, "frozen", False))


def _bundled_version_stamp():
    """Version stamped into the PyInstaller bundle at build time, or ``None``.

    Both specs write ``APP_VERSION`` (the release tag without ``v``) into a data
    file bundled at the root of the frozen app (``sys._MEIPASS``). This is the
    only version source on Windows, where there is no Info.plist; previously
    Windows builds fell back to the stale module ``__version__`` and therefore
    always believed an update was available.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    try:
        v = (Path(base) / VERSION_STAMP_NAME).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return v if parse_version(v) else None


def _info_plist_version():
    """``CFBundleShortVersionString`` of the enclosing ``.app``, or ``None``."""
    exe = Path(sys.executable)  # .../DevNotifier.app/Contents/MacOS/DevNotifier
    for parent in exe.parents:
        if parent.suffix == ".app":
            plist = parent / "Contents" / "Info.plist"
            try:
                with plist.open("rb") as f:
                    data = plistlib.load(f)
                v = data.get("CFBundleShortVersionString")
                if v:
                    return str(v)
            except (OSError, plistlib.InvalidFileException, ValueError):
                pass
            break
    return None


def current_version() -> str:
    """Version of the running app.

    Frozen: prefer the ``APP_VERSION`` stamp the build bundles (all
    platforms); on macOS fall back to ``CFBundleShortVersionString`` from the
    ``.app`` bundle's Info.plist. Source runs (and a frozen build with no stamp,
    e.g. a hand-rolled ``pyinstaller`` without ``APP_VERSION``) use the module
    ``__version__``.
    """
    if is_frozen():
        v = _bundled_version_stamp()
        if v:
            return v
        if sys.platform != "win32":
            v = _info_plist_version()
            if v:
                return v
    return __version__


def parse_version(v: str):
    """Parse ``[v]X.Y.Z[-suffix]`` into a comparable tuple.

    The result is ``(X, Y, Z, final, suffix)`` where ``final`` is 1 for a plain
    ``X.Y.Z`` and 0 for a pre-release (``2.0.0-beta``, ``2.0.0-rc.1``), so a
    pre-release sorts *below* its final version: someone running ``2.0.0-beta``
    is offered ``2.0.0``, while ``2.0.0`` users are never offered
    ``2.0.0-beta``. Pre-releases of the same core compare by suffix string
    (``beta`` < ``rc.1``), which matches the usual naming. Returns ``None`` if
    unparseable.
    """
    if not v:
        return None
    v = v.strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z.]+))?$", v)
    if not m:
        return None
    x, y, z, suffix = m.groups()
    return (int(x), int(y), int(z), 0 if suffix else 1, suffix or "")


def is_newer(latest: str, current: str) -> bool:
    """True if ``latest`` is a strictly newer version than ``current``."""
    lp, cp = parse_version(latest), parse_version(current)
    if lp is None or cp is None:
        return False
    return lp > cp


# ---------------------------------------------------------------------------
# GitHub Releases API
# ---------------------------------------------------------------------------

def _http_get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": accept},
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=_SSL_CTX) as resp:
        return resp.read()


def _installer_regex(platform: str = None):
    """Asset-name pattern for the current platform's installer.

    macOS -> the ``.dmg``; Windows -> the ``.exe``. Other platforms have no
    installer asset yet, so ``None`` is returned and the updater simply points
    the user at the Releases page.
    """
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        return _EXE_NAME_RE
    if plat == "darwin":
        return _DMG_NAME_RE
    return None


def fetch_latest_release() -> dict:
    """Query the latest release. Returns a normalized dict or raises on failure.

    Returned dict::

        {
          "version": "1.4.0",          # tag without leading 'v'
          "tag": "v1.4.0",
          "html_url": "https://.../releases/tag/v1.4.0",
          "dmg_url": "https://.../DevNotifier-1.4.0.dmg" | None,   # macOS asset
          "dmg_name": "DevNotifier-1.4.0.dmg" | None,
          "installer_url": "https://.../<platform asset>" | None,  # this OS
          "installer_name": "<platform asset name>" | None,
          "sha256_url": "https://.../SHA256SUMS.txt" | None,
        }

    ``dmg_url``/``dmg_name`` are always the macOS asset (kept for
    compatibility); ``installer_url``/``installer_name`` are the asset for the
    *current* platform, which is what the downloader uses.
    """
    raw = _http_get(RELEASES_API)
    data = json.loads(raw.decode("utf-8"))
    tag = data.get("tag_name", "") or ""
    version = tag.lstrip("vV")
    dmg_url = dmg_name = sha_url = None
    installer_url = installer_name = None
    installer_re = _installer_regex()
    # On Windows a release may carry both the Inno Setup installer
    # (``-setup.exe``) and the portable one-file exe. Always prefer the
    # installer: launching the portable exe just starts a second copy of the
    # app and never replaces the installed one.
    preferred = False
    for asset in data.get("assets", []) or []:
        name = asset.get("name", "") or ""
        url = asset.get("browser_download_url")
        if _DMG_NAME_RE.search(name):
            dmg_url, dmg_name = url, name
        if installer_re and installer_re.search(name):
            is_setup = bool(_SETUP_EXE_RE.search(name))
            if is_setup or not preferred:
                installer_url, installer_name = url, name
                preferred = preferred or is_setup
        if name == "SHA256SUMS.txt":
            sha_url = url
    return {
        "version": version,
        "tag": tag,
        "html_url": data.get("html_url") or RELEASES_PAGE,
        "dmg_url": dmg_url,
        "dmg_name": dmg_name,
        "installer_url": installer_url,
        "installer_name": installer_name,
        "sha256_url": sha_url,
    }


def check_for_update(cfg: dict) -> dict:
    """Blocking update check. Safe to call only from a worker thread.

    Never raises: on any error returns ``{"available": False, "error": ...}`` so
    the caller can silently ignore transient network failures.

    Result dict::

        {
          "available": bool,      # a newer, non-skipped release exists
          "current": "1.3.0",
          "latest": "1.4.0" | "",
          "html_url": str,
          "dmg_url": str | None,
          "dmg_name": str | None,
          "sha256_url": str | None,
          "from_source": bool,    # running from source (no real install)
          "error": str | None,
        }
    """
    cur = current_version()
    base = {
        "available": False,
        "current": cur,
        "latest": "",
        "html_url": RELEASES_PAGE,
        "dmg_url": None,
        "dmg_name": None,
        "installer_url": None,
        "installer_name": None,
        "sha256_url": None,
        "from_source": not is_frozen(),
        "error": None,
    }
    # From source there is no installed app to replace; skip the check quietly.
    if not is_frozen():
        return base
    if not cfg.get("update", {}).get("enabled", True):
        return base
    try:
        rel = fetch_latest_release()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, OSError) as e:
        base["error"] = str(e)
        return base

    base.update({
        "latest": rel["version"],
        "html_url": rel["html_url"],
        "dmg_url": rel["dmg_url"],
        "dmg_name": rel["dmg_name"],
        "installer_url": rel.get("installer_url"),
        "installer_name": rel.get("installer_name"),
        "sha256_url": rel["sha256_url"],
    })
    skipped = cfg.get("update", {}).get("skipped_version", "")
    if is_newer(rel["version"], cur) and rel["version"] != skipped:
        base["available"] = True
    return base


# ---------------------------------------------------------------------------
# download + verify + open
# ---------------------------------------------------------------------------

def _parse_sha256sums(text: str, dmg_name: str):
    """Extract the SHA-256 for ``dmg_name`` from a ``shasum -a 256`` listing.

    Lines look like ``<hex>  ./DevNotifier-1.4.0.dmg``. Match by basename.
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1]
        if os.path.basename(name) == dmg_name:
            return digest.lower()
    return None


def _open_installer(path: str) -> None:
    """Open the downloaded installer with the OS default handler.

    Windows uses ``os.startfile`` (launches the .exe installer); macOS shells
    out to ``open`` (mounts the DMG volume). Kept tiny and side-effecting so the
    download logic stays platform-neutral.
    """
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - launching our own verified installer
    else:
        import subprocess

        subprocess.run(["open", path], check=False)


def install_instructions(platform: str = None):
    """``(subtitle, message)`` describing what to do after the download opens.

    The wording differs by platform: Windows launches a real installer that
    replaces and relaunches the app, whereas macOS only mounts the DMG and the
    user must quit, drag-replace and relaunch by hand (and may hit Gatekeeper
    again, because the build is not Developer-ID signed).
    """
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        return ("Installer opened",
                "Follow the installer — it replaces the old version and "
                "relaunches Dev Notifier.")
    return ("Disk image opened",
            "Quit Dev Notifier, drag the new app into Applications "
            "(Replace), then relaunch. If macOS says the app is damaged, "
            "run: xattr -dr com.apple.quarantine /Applications/DevNotifier.app")


def download_action_label(platform: str = None) -> str:
    """Menu label for the update action, honest about what it does."""
    plat = platform if platform is not None else sys.platform
    return "Download & Install" if plat == "win32" else "Download update…"


def download_and_open(info: dict, log=None) -> dict:
    """Download the release installer, verify SHA-256, then open it.

    Blocking — call from a worker thread. Returns::

        {"ok": bool, "path": str | None, "error": str | None}

    The asset is the current platform's installer: on macOS the DMG (the user
    drags the new app into /Applications — avoids Gatekeeper issues from a
    silent replace); on Windows the ``.exe`` setup, which is launched directly.

    ``info`` may provide ``installer_url``/``installer_name`` (preferred,
    per-platform) and/or the legacy ``dmg_url``/``dmg_name`` (macOS); the former
    wins when present.
    """
    import hashlib

    def _log(m):
        if log:
            log(m)

    asset_url = info.get("installer_url") or info.get("dmg_url")
    asset_name = (info.get("installer_name") or info.get("dmg_name")
                  or "DevNotifier-latest")
    if not asset_url:
        return {"ok": False, "path": None,
                "error": "No installer asset found in the latest release."}

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = CACHE_DIR / asset_name

        _log(f"UPDATE downloading {asset_url}")
        req = urllib.request.Request(asset_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT * 4, context=_SSL_CTX) as resp, \
                dest.open("wb") as f:
            hasher = hashlib.sha256()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)
        got = hasher.hexdigest().lower()
        _log(f"UPDATE downloaded {dest} sha256={got}")

        # Verify against the release's SHA256SUMS.txt. This is *mandatory*: the
        # app is not Developer-ID signed, so the checksum is the only integrity
        # check the download gets. If the sums file is missing, unreachable or
        # does not list the asset, the download is discarded and the user is
        # pointed at the Releases page rather than silently opened unverified.
        want = None
        why = "checksum file not published for this release"
        if info.get("sha256_url"):
            try:
                sums = _http_get(info["sha256_url"], accept="text/plain").decode("utf-8")
                want = _parse_sha256sums(sums, asset_name)
                if not want:
                    why = f"{asset_name} is not listed in SHA256SUMS.txt"
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, OSError, ValueError) as e:
                _log(f"UPDATE WARN could not fetch checksums: {e}")
                why = f"could not fetch checksums ({e})"
        if not want or want != got:
            try:
                dest.unlink()
            except OSError:
                pass
            if want:
                error = "Checksum mismatch — download discarded."
            else:
                error = (f"Could not verify the download ({why}) — discarded. "
                         f"Download it from the Releases page instead.")
            _log(f"UPDATE ERROR {error}")
            return {"ok": False, "path": None, "error": error}

        _open_installer(str(dest))
        return {"ok": True, "path": str(dest), "error": None}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            OSError, ValueError) as e:
        _log(f"UPDATE ERROR download failed: {e}")
        return {"ok": False, "path": None, "error": str(e)}
