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

CACHE_DIR = Path.home() / "Library" / "Caches" / "dev-notifier"


# ---------------------------------------------------------------------------
# version handling
# ---------------------------------------------------------------------------

def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle (a real installed app)."""
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    """Version of the running app.

    Frozen: read ``CFBundleShortVersionString`` from the bundle's Info.plist.
    Source: the module ``__version__`` fallback.
    """
    if is_frozen():
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
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
        return resp.read()


def fetch_latest_release() -> dict:
    """Query the latest release. Returns a normalized dict or raises on failure.

    Returned dict::

        {
          "version": "1.4.0",          # tag without leading 'v'
          "tag": "v1.4.0",
          "html_url": "https://.../releases/tag/v1.4.0",
          "dmg_url": "https://.../DevNotifier-1.4.0.dmg" | None,
          "dmg_name": "DevNotifier-1.4.0.dmg" | None,
          "sha256_url": "https://.../SHA256SUMS.txt" | None,
        }
    """
    raw = _http_get(RELEASES_API)
    data = json.loads(raw.decode("utf-8"))
    tag = data.get("tag_name", "") or ""
    version = tag.lstrip("vV")
    dmg_url = dmg_name = sha_url = None
    for asset in data.get("assets", []) or []:
        name = asset.get("name", "") or ""
        url = asset.get("browser_download_url")
        if _DMG_NAME_RE.search(name):
            dmg_url, dmg_name = url, name
        elif name == "SHA256SUMS.txt":
            sha_url = url
    return {
        "version": version,
        "tag": tag,
        "html_url": data.get("html_url") or RELEASES_PAGE,
        "dmg_url": dmg_url,
        "dmg_name": dmg_name,
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


def download_and_open(info: dict, log=None) -> dict:
    """Download the release DMG, verify SHA-256, then ``open`` it.

    Blocking — call from a worker thread. Returns::

        {"ok": bool, "path": str | None, "error": str | None}

    On success macOS opens the DMG volume; the user drags the new app into
    /Applications (standard flow, avoids Gatekeeper issues from silent replace).
    """
    import hashlib
    import subprocess

    def _log(m):
        if log:
            log(m)

    dmg_url = info.get("dmg_url")
    dmg_name = info.get("dmg_name") or "DevNotifier-latest.dmg"
    if not dmg_url:
        return {"ok": False, "path": None,
                "error": "No DMG asset found in the latest release."}

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = CACHE_DIR / dmg_name

        _log(f"UPDATE downloading {dmg_url}")
        ctx = ssl.create_default_context()
        req = urllib.request.Request(dmg_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT * 4, context=ctx) as resp, \
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
                want = _parse_sha256sums(sums, dmg_name)
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

        subprocess.run(["open", str(dest)], check=False)
        return {"ok": True, "path": str(dest), "error": None}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            OSError, ValueError) as e:
        _log(f"UPDATE ERROR download failed: {e}")
        return {"ok": False, "path": None, "error": str(e)}
