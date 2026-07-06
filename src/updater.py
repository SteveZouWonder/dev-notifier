"""Self-update checks against GitHub Releases.

This project ships as an unsigned, ad-hoc-signed ``.app`` inside a ``.dmg`` on
GitHub Releases. Fully silent, in-place replacement of an unsigned app is risky
on macOS (Gatekeeper / quarantine), so instead we:

  1. Read the running app's version from ``Info.plist`` (frozen) or a fallback
     ``__version__`` from source.
  2. Query the GitHub Releases API for the newest ``vX.Y.Z`` release.
  3. If newer, surface it in the menu bar and via a clickable notification.
  4. On the user's request, download the release DMG to a cache dir, verify its
     SHA-256 against the release's ``SHA256SUMS.txt``, then ``open`` the DMG so
     the user drags the new app into /Applications (the usual first-run flow).

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

# Fallback version when running from source (not a PyInstaller bundle). Keep in
# sync with the latest tag; from-source runs skip update prompts anyway.
__version__ = "1.3.0"

_USER_AGENT = f"{GITHUB_REPO}-updater"
_HTTP_TIMEOUT = 15  # seconds
_DMG_NAME_RE = re.compile(r"DevNotifier-.*\.dmg$", re.IGNORECASE)
# Windows installer asset (published alongside the macOS DMG on the same
# release). Matches e.g. DevNotifier-1.4.0-setup.exe or DevNotifier-1.4.0.exe.
_EXE_NAME_RE = re.compile(r"DevNotifier-.*\.exe$", re.IGNORECASE)

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


def current_version() -> str:
    """Version of the running app.

    Frozen on macOS: read ``CFBundleShortVersionString`` from the ``.app``
    bundle's Info.plist. Frozen on Windows (and any other case): fall back to
    the module ``__version__``, which the build keeps in sync with the release
    tag. Source runs also use ``__version__`` (and skip update prompts anyway).
    """
    if is_frozen() and sys.platform != "win32":
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
    return __version__


def parse_version(v: str):
    """Parse ``[v]X.Y.Z[-suffix]`` into a comparable tuple of the numeric core.

    Pre-release / build suffixes are ignored for comparison (a release DMG is
    only ever published for a full ``vX.Y.Z``). Returns ``None`` if unparseable.
    """
    if not v:
        return None
    v = v.strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


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
    for asset in data.get("assets", []) or []:
        name = asset.get("name", "") or ""
        url = asset.get("browser_download_url")
        if _DMG_NAME_RE.search(name):
            dmg_url, dmg_name = url, name
        if installer_re and installer_re.search(name):
            installer_url, installer_name = url, name
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

        # Verify against SHA256SUMS.txt when available (best effort).
        want = None
        if info.get("sha256_url"):
            try:
                sums = _http_get(info["sha256_url"], accept="text/plain").decode("utf-8")
                want = _parse_sha256sums(sums, asset_name)
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, OSError, ValueError) as e:
                _log(f"UPDATE WARN could not fetch checksums: {e}")
        if want and want != got:
            try:
                dest.unlink()
            except OSError:
                pass
            return {"ok": False, "path": None,
                    "error": "Checksum mismatch — download discarded."}

        _open_installer(str(dest))
        return {"ok": True, "path": str(dest), "error": None}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            OSError, ValueError) as e:
        _log(f"UPDATE ERROR download failed: {e}")
        return {"ok": False, "path": None, "error": str(e)}
