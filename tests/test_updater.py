"""Tests for src/updater.py — version parsing, release fetching, and checksum
handling. Network (urllib) is mocked; no real GitHub calls occur.

@author SteveZou
"""
import importlib
import json

import pytest


@pytest.fixture
def updater_mod(temp_home):
    import updater as updater_mod
    importlib.reload(updater_mod)
    return updater_mod


# ---------------------------------------------------------------------------
# version parsing / comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("V10.0.1", (10, 0, 1)),
    ("1.4.0-beta.1", (1, 4, 0)),  # suffix ignored
])
def test_parse_version_valid(updater_mod, raw, expected):
    assert updater_mod.parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1.2", "vX.Y.Z"])
def test_parse_version_invalid(updater_mod, raw):
    assert updater_mod.parse_version(raw) is None


@pytest.mark.parametrize("latest,current,expected", [
    ("1.4.0", "1.3.0", True),
    ("1.3.0", "1.3.0", False),
    ("1.2.9", "1.3.0", False),
    ("2.0.0", "1.9.9", True),
    ("bad", "1.0.0", False),
])
def test_is_newer(updater_mod, latest, current, expected):
    assert updater_mod.is_newer(latest, current) is expected


def test_current_version_from_source(updater_mod, monkeypatch):
    monkeypatch.setattr(updater_mod, "is_frozen", lambda: False)
    assert updater_mod.current_version() == updater_mod.__version__


def test_is_frozen_reflects_sys_flag(updater_mod, monkeypatch):
    monkeypatch.setattr(updater_mod.sys, "frozen", True, raising=False)
    assert updater_mod.is_frozen() is True
    monkeypatch.setattr(updater_mod.sys, "frozen", False, raising=False)
    assert updater_mod.is_frozen() is False


def test_current_version_frozen_reads_info_plist(updater_mod, monkeypatch, tmp_path):
    # Build a fake .app bundle with an Info.plist and point sys.executable at it.
    import plistlib
    app = tmp_path / "DevNotifier.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    plist = app / "Contents" / "Info.plist"
    with plist.open("wb") as f:
        plistlib.dump({"CFBundleShortVersionString": "9.9.9"}, f)

    monkeypatch.setattr(updater_mod, "is_frozen", lambda: True)
    monkeypatch.setattr(updater_mod.sys, "executable",
                        str(macos / "DevNotifier"), raising=False)
    assert updater_mod.current_version() == "9.9.9"


def test_current_version_frozen_bad_plist_falls_back(updater_mod, monkeypatch, tmp_path):
    app = tmp_path / "DevNotifier.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("not a plist", encoding="utf-8")

    monkeypatch.setattr(updater_mod, "is_frozen", lambda: True)
    monkeypatch.setattr(updater_mod.sys, "executable",
                        str(macos / "DevNotifier"), raising=False)
    # Unreadable plist -> fall back to the module __version__.
    assert updater_mod.current_version() == updater_mod.__version__


# ---------------------------------------------------------------------------
# _http_get
# ---------------------------------------------------------------------------

def test_http_get_returns_body(updater_mod, monkeypatch):
    class FakeResp:
        def read(self):
            return b"payload"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater_mod.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp())
    assert updater_mod._http_get("https://x") == b"payload"


# ---------------------------------------------------------------------------
# fetch_latest_release
# ---------------------------------------------------------------------------

def _release_payload():
    return {
        "tag_name": "v1.4.0",
        "html_url": "https://github.com/x/y/releases/tag/v1.4.0",
        "assets": [
            {"name": "DevNotifier-1.4.0.dmg",
             "browser_download_url": "https://x/DevNotifier-1.4.0.dmg"},
            {"name": "SHA256SUMS.txt",
             "browser_download_url": "https://x/SHA256SUMS.txt"},
        ],
    }


def test_fetch_latest_release_normalizes(updater_mod, monkeypatch):
    monkeypatch.setattr(updater_mod, "_http_get",
                        lambda *a, **k: json.dumps(_release_payload()).encode())
    rel = updater_mod.fetch_latest_release()
    assert rel["version"] == "1.4.0"
    assert rel["tag"] == "v1.4.0"
    assert rel["dmg_name"] == "DevNotifier-1.4.0.dmg"
    assert rel["dmg_url"] == "https://x/DevNotifier-1.4.0.dmg"
    assert rel["sha256_url"] == "https://x/SHA256SUMS.txt"


def test_fetch_latest_release_no_assets(updater_mod, monkeypatch):
    payload = {"tag_name": "v1.0.0", "html_url": "u", "assets": []}
    monkeypatch.setattr(updater_mod, "_http_get",
                        lambda *a, **k: json.dumps(payload).encode())
    rel = updater_mod.fetch_latest_release()
    assert rel["dmg_url"] is None
    assert rel["sha256_url"] is None


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------

def test_check_for_update_from_source_short_circuits(updater_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(updater_mod, "is_frozen", lambda: False)
    result = updater_mod.check_for_update(sample_cfg)
    assert result["available"] is False
    assert result["from_source"] is True


def test_check_for_update_disabled(updater_mod, monkeypatch):
    monkeypatch.setattr(updater_mod, "is_frozen", lambda: True)
    result = updater_mod.check_for_update({"update": {"enabled": False}})
    assert result["available"] is False


def test_check_for_update_available(updater_mod, monkeypatch, sample_cfg):
    monkeypatch.setattr(updater_mod, "is_frozen", lambda: True)
    monkeypatch.setattr(updater_mod, "current_version", lambda: "1.3.0")
    monkeypatch.setattr(updater_mod, "fetch_latest_release", lambda: {
        "version": "1.4.0", "tag": "v1.4.0", "html_url": "u",
        "dmg_url": "d", "dmg_name": "n", "sha256_url": "s"})
    result = updater_mod.check_for_update(sample_cfg)
    assert result["available"] is True
    assert result["latest"] == "1.4.0"


def test_check_for_update_respects_skipped_version(updater_mod, monkeypatch, sample_cfg):
    sample_cfg["update"]["skipped_version"] = "1.4.0"
    monkeypatch.setattr(updater_mod, "is_frozen", lambda: True)
    monkeypatch.setattr(updater_mod, "current_version", lambda: "1.3.0")
    monkeypatch.setattr(updater_mod, "fetch_latest_release", lambda: {
        "version": "1.4.0", "tag": "v1.4.0", "html_url": "u",
        "dmg_url": "d", "dmg_name": "n", "sha256_url": "s"})
    result = updater_mod.check_for_update(sample_cfg)
    assert result["available"] is False  # skipped


def test_check_for_update_network_error(updater_mod, monkeypatch, sample_cfg):
    import urllib.error
    monkeypatch.setattr(updater_mod, "is_frozen", lambda: True)

    def boom():
        raise urllib.error.URLError("down")

    monkeypatch.setattr(updater_mod, "fetch_latest_release", boom)
    result = updater_mod.check_for_update(sample_cfg)
    assert result["available"] is False
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# SHA256SUMS parsing
# ---------------------------------------------------------------------------

def test_parse_sha256sums_matches_basename(updater_mod):
    text = (
        "abc123  ./DevNotifier-1.4.0.dmg\n"
        "def456  ./other.dmg\n"
    )
    assert updater_mod._parse_sha256sums(text, "DevNotifier-1.4.0.dmg") == "abc123"


def test_parse_sha256sums_no_match(updater_mod):
    text = "abc123  ./other.dmg\n"
    assert updater_mod._parse_sha256sums(text, "DevNotifier-1.4.0.dmg") is None


def test_parse_sha256sums_ignores_short_lines(updater_mod):
    text = "garbage\nabc123  DevNotifier-1.4.0.dmg\n"
    assert updater_mod._parse_sha256sums(text, "DevNotifier-1.4.0.dmg") == "abc123"


# ---------------------------------------------------------------------------
# download_and_open
# ---------------------------------------------------------------------------

class _FakeDownloadResp:
    """Fake urlopen response streaming ``chunks`` of bytes."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, size=-1):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_download_and_open_no_dmg_url(updater_mod):
    result = updater_mod.download_and_open({"dmg_url": None})
    assert result["ok"] is False
    assert "No DMG asset" in result["error"]


def test_download_and_open_success_no_checksum(updater_mod, monkeypatch, temp_home):
    payload = b"dmg-bytes"
    monkeypatch.setattr(updater_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeDownloadResp([payload]))
    opened = {}
    import subprocess

    def fake_run(args, check=False):
        opened["args"] = args

    monkeypatch.setattr(subprocess, "run", fake_run)

    info = {"dmg_url": "https://x/DevNotifier-1.4.0.dmg",
            "dmg_name": "DevNotifier-1.4.0.dmg", "sha256_url": None}
    logs = []
    result = updater_mod.download_and_open(info, log=logs.append)

    assert result["ok"] is True
    assert result["path"].endswith("DevNotifier-1.4.0.dmg")
    # The DMG is opened after a successful download.
    assert opened["args"][0] == "open"
    assert any("downloaded" in m for m in logs)


def test_download_and_open_checksum_match(updater_mod, monkeypatch, temp_home):
    payload = b"verified-bytes"
    digest = _sha256(payload)
    monkeypatch.setattr(updater_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeDownloadResp([payload]))
    # _http_get fetches the SHA256SUMS file.
    monkeypatch.setattr(updater_mod, "_http_get",
                        lambda url, accept="": f"{digest}  DevNotifier-1.4.0.dmg\n".encode())
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)

    info = {"dmg_url": "https://x/DevNotifier-1.4.0.dmg",
            "dmg_name": "DevNotifier-1.4.0.dmg",
            "sha256_url": "https://x/SHA256SUMS.txt"}
    result = updater_mod.download_and_open(info)
    assert result["ok"] is True


def test_download_and_open_checksum_mismatch_discards(updater_mod, monkeypatch, temp_home):
    payload = b"tampered"
    monkeypatch.setattr(updater_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeDownloadResp([payload]))
    monkeypatch.setattr(updater_mod, "_http_get",
                        lambda url, accept="": b"deadbeef  DevNotifier-1.4.0.dmg\n")
    import subprocess
    called = {"run": False}
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: called.__setitem__("run", True))

    info = {"dmg_url": "https://x/DevNotifier-1.4.0.dmg",
            "dmg_name": "DevNotifier-1.4.0.dmg",
            "sha256_url": "https://x/SHA256SUMS.txt"}
    result = updater_mod.download_and_open(info)

    assert result["ok"] is False
    assert "Checksum mismatch" in result["error"]
    # Mismatch -> the DMG was discarded and never opened.
    assert called["run"] is False
    assert not (updater_mod.CACHE_DIR / "DevNotifier-1.4.0.dmg").exists()


def test_download_and_open_checksum_fetch_error_is_best_effort(updater_mod, monkeypatch, temp_home):
    import urllib.error
    payload = b"bytes"
    monkeypatch.setattr(updater_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeDownloadResp([payload]))

    def boom(url, accept=""):
        raise urllib.error.URLError("checksum server down")

    monkeypatch.setattr(updater_mod, "_http_get", boom)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)

    info = {"dmg_url": "https://x/DevNotifier-1.4.0.dmg",
            "dmg_name": "DevNotifier-1.4.0.dmg",
            "sha256_url": "https://x/SHA256SUMS.txt"}
    logs = []
    result = updater_mod.download_and_open(info, log=logs.append)
    # Checksum unavailable -> proceed anyway (best effort) and succeed.
    assert result["ok"] is True
    assert any("could not fetch checksums" in m for m in logs)


def test_download_and_open_mismatch_unlink_failure_is_swallowed(updater_mod, monkeypatch, temp_home):
    payload = b"tampered"
    monkeypatch.setattr(updater_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeDownloadResp([payload]))
    monkeypatch.setattr(updater_mod, "_http_get",
                        lambda url, accept="": b"deadbeef  DevNotifier-1.4.0.dmg\n")

    def boom(self, *a, **k):
        raise OSError("cannot unlink")

    # Even if discarding the bad DMG fails, we still report a checksum mismatch.
    monkeypatch.setattr(updater_mod.Path, "unlink", boom)
    info = {"dmg_url": "https://x/DevNotifier-1.4.0.dmg",
            "dmg_name": "DevNotifier-1.4.0.dmg",
            "sha256_url": "https://x/SHA256SUMS.txt"}
    result = updater_mod.download_and_open(info)
    assert result["ok"] is False
    assert "Checksum mismatch" in result["error"]


def test_download_and_open_download_error(updater_mod, monkeypatch, temp_home):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("download failed")

    monkeypatch.setattr(updater_mod.urllib.request, "urlopen", boom)
    info = {"dmg_url": "https://x/DevNotifier-1.4.0.dmg",
            "dmg_name": "DevNotifier-1.4.0.dmg", "sha256_url": None}
    result = updater_mod.download_and_open(info)
    assert result["ok"] is False
    assert result["error"] is not None
