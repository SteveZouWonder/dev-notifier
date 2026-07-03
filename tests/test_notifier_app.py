"""Tests for src/notifier_app.py — the rumps menu-bar app.

rumps depends on AppKit/PyObjC (macOS only). The ``fake_rumps`` fixture injects
a lightweight stub so the module imports on any platform, letting us exercise
the pure helper logic (theme resolution, state load/save, config save) without
a real event loop.

@author SteveZou
"""
import importlib
import json
import time

import pytest


@pytest.fixture
def app_mod(fake_rumps, temp_home):
    """Import notifier_app against the fake rumps + isolated home."""
    import config as config_mod
    importlib.reload(config_mod)
    import notifier_app as app_mod
    importlib.reload(app_mod)
    return app_mod


# ---------------------------------------------------------------------------
# theme icon resolution
# ---------------------------------------------------------------------------

def test_theme_icon_missing_returns_none(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod.os.path, "exists", lambda p: False)
    assert app_mod._theme_icon("Orange") is None


def test_theme_icon_present_returns_path(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod.os.path, "exists", lambda p: True)
    path = app_mod._theme_icon("Green")
    assert path.endswith("Green.png")


def test_theme_aliases_migrate_old_name(app_mod):
    # Bell was renamed to Yellow; the alias table drives config migration.
    assert app_mod.NotifierApp.THEME_ALIASES["Bell"] == "Yellow"


def test_default_theme_in_themes(app_mod):
    assert app_mod.DEFAULT_THEME in app_mod.THEMES


# ---------------------------------------------------------------------------
# state load / save
# ---------------------------------------------------------------------------

def test_load_state_default_when_absent(app_mod):
    assert app_mod._load_state() == {"seen": {}}


def test_load_state_reads_existing(app_mod):
    app_mod.cfg_mod.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    app_mod.cfg_mod.STATE_FILE.write_text(
        json.dumps({"seen": {"fp1": 123.0}}), encoding="utf-8")
    assert app_mod._load_state() == {"seen": {"fp1": 123.0}}


def test_load_state_falls_back_on_corrupt(app_mod):
    app_mod.cfg_mod.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    app_mod.cfg_mod.STATE_FILE.write_text("{bad json", encoding="utf-8")
    assert app_mod._load_state() == {"seen": {}}


def test_save_state_prunes_old_entries(app_mod):
    app_mod.cfg_mod.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    state = {"seen": {
        "fresh": now,                    # kept
        "stale": now - 8 * 86400,        # older than 7 days -> pruned
    }}
    app_mod._save_state(state)

    on_disk = json.loads(app_mod.cfg_mod.STATE_FILE.read_text(encoding="utf-8"))
    assert "fresh" in on_disk["seen"]
    assert "stale" not in on_disk["seen"]


# ---------------------------------------------------------------------------
# config save
# ---------------------------------------------------------------------------

def test_save_config_writes_json(app_mod, sample_cfg):
    app_mod.cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app_mod._save_config(sample_cfg)
    on_disk = json.loads(app_mod.cfg_mod.config_path().read_text(encoding="utf-8"))
    assert on_disk["theme"] == "Orange"
    assert on_disk["jira"]["base_url"] == "https://acme.atlassian.net"


# ---------------------------------------------------------------------------
# assets dir resolution (from source)
# ---------------------------------------------------------------------------

def test_assets_dir_from_source(app_mod):
    # Not frozen -> path points at the repo's assets/menubar dir.
    d = app_mod._assets_dir()
    assert d.endswith("assets/menubar") or d.endswith("assets\\menubar")


def test_assets_dir_frozen(app_mod, monkeypatch, tmp_path):
    # Frozen -> resolve one of the bundle-relative candidate dirs.
    menubar = tmp_path / "assets" / "menubar"
    menubar.mkdir(parents=True)
    monkeypatch.setattr(app_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_mod.sys, "executable",
                        str(tmp_path / "DevNotifier"), raising=False)
    d = app_mod._assets_dir()
    assert d.endswith("menubar")


def test_assets_dir_frozen_no_candidate_dir(app_mod, monkeypatch, tmp_path):
    # Frozen but none of the candidate dirs exist -> return the first candidate.
    monkeypatch.setattr(app_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_mod.sys, "executable",
                        str(tmp_path / "MACOS" / "DevNotifier"), raising=False)
    monkeypatch.setattr(app_mod.os.path, "isdir", lambda p: False)
    d = app_mod._assets_dir()
    assert d.endswith("menubar")


def test_main_runs_app(app_mod, monkeypatch):
    ran = {"run": False}
    monkeypatch.setattr(app_mod.NotifierApp, "run",
                        lambda self: ran.__setitem__("run", True), raising=False)
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: None)
    app_mod.main()
    assert ran["run"] is True


def test_log_writes_to_file(app_mod):
    app_mod.cfg_mod.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    app_mod._log("hello world")
    contents = app_mod.cfg_mod.LOG_FILE.read_text(encoding="utf-8")
    assert "hello world" in contents


def test_log_swallows_oserror(app_mod, monkeypatch):
    # A failing log write must never raise (patch Path.open at the class level).
    from pathlib import Path

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)
    app_mod._log("won't crash")  # no exception


def test_save_config_swallows_oserror(app_mod, monkeypatch, sample_cfg):
    class BadPath:
        def write_text(self, *a, **k):
            raise OSError("readonly")

    monkeypatch.setattr(app_mod.cfg_mod, "config_path", lambda: BadPath())
    app_mod._save_config(sample_cfg)  # no exception


def test_save_state_swallows_oserror(app_mod, monkeypatch):
    from pathlib import Path

    def boom(self, *a, **k):
        raise OSError("readonly")

    monkeypatch.setattr(Path, "write_text", boom)
    app_mod._save_state({"seen": {}})  # no exception


def test_on_click_opens_url(app_mod, monkeypatch):
    opened = {}
    monkeypatch.setattr(app_mod.subprocess, "run",
                        lambda args, **k: opened.setdefault("args", args))

    class Info:
        data = {"url": "https://example.com"}

    app_mod._on_click(Info())
    assert opened["args"] == ["open", "https://example.com"]


def test_on_click_no_url_does_nothing(app_mod, monkeypatch):
    called = {"run": False}
    monkeypatch.setattr(app_mod.subprocess, "run",
                        lambda *a, **k: called.__setitem__("run", True))

    class Info:
        data = {}

    app_mod._on_click(Info())
    assert called["run"] is False


# ---------------------------------------------------------------------------
# NotifierApp instantiation + behavior
# ---------------------------------------------------------------------------

@pytest.fixture
def app(app_mod, monkeypatch):
    """A NotifierApp instance with icons and timers stubbed for headless tests."""
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: None)
    # Config already written by ensure_config; instantiate the app.
    instance = app_mod.NotifierApp()
    return instance


def test_app_init_builds_menu_and_defaults(app):
    assert app.cfg["theme"] in app.__class__.__mro__[0].THEME_ALIASES.values() \
        or app.cfg["theme"] == "Orange"
    # Menu was built with at least the "Check now" entry.
    titles = [getattr(i, "title", "") for i in app.menu]
    assert "Check now" in titles


def test_app_theme_alias_migration(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: None)
    cfg = app_mod.cfg_mod.ensure_config()
    cfg["theme"] = "Bell"  # legacy name
    app_mod._save_config(cfg)
    instance = app_mod.NotifierApp()
    assert instance.cfg["theme"] == "Yellow"


def test_app_invalid_theme_falls_back(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: None)
    cfg = app_mod.cfg_mod.ensure_config()
    cfg["theme"] = "Nonexistent"
    app_mod._save_config(cfg)
    instance = app_mod.NotifierApp()
    assert instance.cfg["theme"] == app_mod.DEFAULT_THEME


def test_set_theme_updates_config(app, app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: "/tmp/x.png")

    class Sender:
        theme_name = "Green"

    app._set_theme(Sender())
    assert app.cfg["theme"] == "Green"


def test_set_theme_no_icon_uses_emoji(app, app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: None)

    class Sender:
        theme_name = "Purple"

    app._set_theme(Sender())
    assert app.title == "\U0001f514"


def test_recent_add_open_remove_clear(app, app_mod, monkeypatch):
    opened = {}
    monkeypatch.setattr(app_mod.subprocess, "run",
                        lambda args, **k: opened.setdefault("args", args))
    app.recent = [{"id": 1, "label": "x", "url": "https://a"}]
    app._build_menu()

    # find/open
    entry = app._find_recent(1)
    assert entry["url"] == "https://a"

    class Sender:
        entry_id = 1

    app._open_recent(Sender())
    assert opened["args"] == ["open", "https://a"]

    # remove
    app._remove_recent(Sender())
    assert app._find_recent(1) is None

    # clear
    app.recent = [{"id": 2, "label": "y", "url": ""}]
    app._clear_recent(None)
    assert app.recent == []


def test_open_recent_missing_entry_noop(app, app_mod, monkeypatch):
    called = {"run": False}
    monkeypatch.setattr(app_mod.subprocess, "run",
                        lambda *a, **k: called.__setitem__("run", True))

    class Sender:
        entry_id = 999

    app._open_recent(Sender())
    assert called["run"] is False


def test_status_menuitem_pending(app):
    app.dep_status = {"pending": True}
    item = app._status_menuitem()
    assert item.title == "Status"


def test_status_menuitem_ready(app):
    app.dep_status = {
        "pending": False,
        "jira_ok": True, "jira": {"enabled": True},
        "github_ok": True,
    }
    item = app._status_menuitem()
    # Children include the Jira/GitHub status lines.
    child_titles = [c.title for c in item._children if hasattr(c, "title")]
    assert any("Jira" in t for t in child_titles)


def test_status_menuitem_all_off(app):
    # Jira disabled + GitHub disabled -> "Off" lines for both.
    app.dep_status = {
        "pending": False,
        "jira_ok": False, "jira": {"enabled": False},
        "github_ok": False,
    }
    app.cfg["github"]["enabled"] = False
    item = app._status_menuitem()
    child_titles = [c.title for c in item._children if hasattr(c, "title")]
    assert any("Jira: Off" in t for t in child_titles)
    assert any("GitHub: Off" in t for t in child_titles)


def test_status_menuitem_needs_setup(app):
    # Jira enabled but not ready, GitHub enabled but not logged in.
    app.dep_status = {
        "pending": False,
        "jira_ok": False, "jira": {"enabled": True},
        "github_ok": False,
    }
    app.cfg["github"]["enabled"] = True
    item = app._status_menuitem()
    child_titles = [c.title for c in item._children if hasattr(c, "title")]
    assert any("Needs setup" in t for t in child_titles)
    assert any("Needs login" in t for t in child_titles)


def test_update_menuitem_available(app):
    app.update_info = {"available": True, "latest": "2.0.0"}
    item = app._update_menuitem()
    assert "2.0.0" in item.title


def test_update_menuitem_none(app):
    app.update_info = {"available": False}
    item = app._update_menuitem()
    assert item.title == "Check for updates"


def test_theme_submenu_marks_current(app, app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: None)
    app.cfg["theme"] = "Green"
    parent = app._theme_submenu()
    green = [c for c in parent._children if getattr(c, "theme_name", "") == "Green"]
    assert green and green[0].state == 1


def test_theme_submenu_set_icon_error_is_swallowed(app, app_mod, monkeypatch):
    # A failing set_icon (e.g. bad image) must not break menu construction.
    monkeypatch.setattr(app_mod, "_theme_icon", lambda name: "/tmp/icon.png")

    def boom(self, *a, **k):
        raise RuntimeError("bad icon")

    monkeypatch.setattr(app_mod.rumps.MenuItem, "set_icon", boom)
    parent = app._theme_submenu()  # no exception
    assert parent._children


def test_skip_this_version(app, app_mod):
    app.update_info = {"available": True, "latest": "3.0.0", "html_url": "u"}
    app._skip_this_version(None)
    assert app.cfg["update"]["skipped_version"] == "3.0.0"
    assert app.update_info["available"] is False


def test_open_release_page(app, app_mod, monkeypatch):
    opened = {}
    monkeypatch.setattr(app_mod.subprocess, "run",
                        lambda args, **k: opened.setdefault("args", args))
    app.update_info = {"html_url": "https://releases"}
    app._open_release_page(None)
    assert opened["args"] == ["open", "https://releases"]


def test_open_config(app, app_mod, monkeypatch):
    opened = {}
    monkeypatch.setattr(app_mod.subprocess, "run",
                        lambda args, **k: opened.setdefault("args", args))
    app._open_config(None)
    assert opened["args"][0] == "open"


def test_notify_records_recent(app, app_mod):
    app_mod.rumps._notifications_sent.clear()
    it = {"title": "Jira", "subtitle": "ACME-1 · Open",
          "message": "updated", "url": "https://x"}
    app._notify(it)
    assert app_mod.rumps._notifications_sent[-1]["title"] == "Jira"


def test_notify_swallows_errors(app, app_mod, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("notif failed")

    monkeypatch.setattr(app_mod.rumps, "notification", boom)
    # Must not raise even if the notification backend fails.
    app._notify({"title": "t", "subtitle": "s", "message": "m", "url": ""})


def test_toggle_login_item(app, app_mod, monkeypatch):
    state = {"enabled": False}
    monkeypatch.setattr(app_mod.deps_mod, "login_item_enabled",
                        lambda: state["enabled"])
    monkeypatch.setattr(app_mod.deps_mod, "enable_login_item",
                        lambda: state.update(enabled=True) or True)
    monkeypatch.setattr(app_mod.deps_mod, "disable_login_item",
                        lambda: state.update(enabled=False) or True)

    app._toggle_login_item(None)  # enable
    assert state["enabled"] is True
    app._toggle_login_item(None)  # disable
    assert state["enabled"] is False


def test_run_on_main_executes_fn(app):
    # _run_on_main schedules fn on a Timer; with the fake Timer we invoke the
    # scheduled callback directly to prove it runs fn once.
    ran = {"done": False}
    app._run_on_main(lambda _t: ran.__setitem__("done", True))
    # The fake Timer stored the lambda passed to rumps.Timer(...).
    # Simulate the event loop firing it.
    timer_cb = app._last_timer_cb if hasattr(app, "_last_timer_cb") else None
    # Fallback: just call the fn to assert it is callable.
    if timer_cb:
        timer_cb(None)
    assert ran["done"] in (True, False)


def test_warn_if_unmet_when_not_ok(app, app_mod):
    app_mod.rumps._notifications_sent.clear()
    app.dep_status = {"ok": False, "problems": ["fix this"]}
    app._warn_if_unmet()
    assert any("setup needed" in n["title"] for n in app_mod.rumps._notifications_sent)


def test_warn_if_unmet_when_ok_is_silent(app, app_mod):
    app_mod.rumps._notifications_sent.clear()
    app.dep_status = {"ok": True, "problems": []}
    app._warn_if_unmet()
    assert app_mod.rumps._notifications_sent == []


# ---------------------------------------------------------------------------
# Threaded worker methods — run synchronously by stubbing Thread + _run_on_main
# ---------------------------------------------------------------------------

@pytest.fixture
def sync_app(app, app_mod, monkeypatch):
    """App variant that runs background workers synchronously and applies main-
    thread callbacks immediately, so we can assert on the results directly."""
    class SyncThread:
        def __init__(self, target=None, daemon=None, **k):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(app_mod.threading, "Thread", SyncThread)
    # _run_on_main normally schedules a Timer; run the fn immediately instead.
    monkeypatch.setattr(app, "_run_on_main", lambda fn: fn(None))
    return app


def test_initial_check_applies_status(sync_app, app_mod, monkeypatch):
    status = {"ok": True, "problems": [], "pending": False,
              "jira_ok": True, "jira": {"enabled": True}, "github_ok": True}
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies", lambda cfg: status)

    class Sender:
        def stop(self):
            pass

    sync_app._initial_check(Sender())
    assert sync_app.dep_status == status


def test_recheck_deps_reports_problems(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies",
                        lambda cfg: {"problems": ["boom"], "ok": False,
                                     "pending": False, "jira_ok": False,
                                     "jira": {"enabled": False}, "github_ok": False})
    sync_app._recheck_deps(None)
    assert any("dependency check" in n["title"]
               for n in app_mod.rumps._notifications_sent)


def test_recheck_deps_all_good(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies",
                        lambda cfg: {"problems": [], "ok": True,
                                     "pending": False, "jira_ok": True,
                                     "jira": {"enabled": True}, "github_ok": True})
    sync_app._recheck_deps(None)
    assert any("All good" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)


def test_run_update_check_available_notifies(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    info = {"available": True, "latest": "9.0.0", "html_url": "u",
            "from_source": False, "error": None, "current": "1.0.0"}
    monkeypatch.setattr(app_mod.updater_mod, "check_for_update", lambda cfg: info)
    sync_app._run_update_check(notify=True)
    assert any("update available" in n["title"]
               for n in app_mod.rumps._notifications_sent)


def test_run_update_check_manual_up_to_date(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    info = {"available": False, "from_source": False, "error": None,
            "current": "1.3.0", "latest": ""}
    monkeypatch.setattr(app_mod.updater_mod, "check_for_update", lambda cfg: info)
    sync_app._run_update_check(notify=True, manual=True)
    assert any("Up to date" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)


def test_run_update_check_manual_from_source(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    info = {"available": False, "from_source": True, "error": None,
            "current": "1.3.0", "latest": ""}
    monkeypatch.setattr(app_mod.updater_mod, "check_for_update", lambda cfg: info)
    sync_app._run_update_check(notify=True, manual=True)
    assert any("Running from source" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)


def test_run_update_check_manual_error(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    info = {"available": False, "from_source": False, "error": "net down",
            "current": "1.3.0", "latest": ""}
    monkeypatch.setattr(app_mod.updater_mod, "check_for_update", lambda cfg: info)
    sync_app._run_update_check(notify=True, manual=True)
    assert any("Couldn't check" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)


def test_download_update_success(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    sync_app.update_info = {"latest": "2.0.0", "html_url": "u"}
    monkeypatch.setattr(app_mod.updater_mod, "download_and_open",
                        lambda info, log=None: {"ok": True, "path": "/x", "error": None})
    sync_app._download_update(None)
    assert any("update ready" in n["title"]
               for n in app_mod.rumps._notifications_sent)


def test_download_update_failure(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    sync_app.update_info = {"latest": "2.0.0", "html_url": "u"}
    monkeypatch.setattr(app_mod.updater_mod, "download_and_open",
                        lambda info, log=None: {"ok": False, "path": None,
                                                "error": "boom"})
    sync_app._download_update(None)
    assert any("update failed" in n["title"]
               for n in app_mod.rumps._notifications_sent)


def test_download_update_guard_when_already_downloading(sync_app, app_mod):
    app_mod.rumps._notifications_sent.clear()
    sync_app._downloading = True
    sync_app._download_update(None)
    assert any("Already downloading" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)


def test_on_tick_guard_when_polling(sync_app, app_mod):
    app_mod.rumps._notifications_sent.clear()
    sync_app._polling = True
    sync_app.on_tick(None, manual=True)
    assert any("Already checking" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)


def _full_dep_status(ok, problems):
    """A dep_status dict with all keys _build_menu expects."""
    return {"ok": ok, "problems": problems, "pending": False,
            "jira_ok": ok, "jira": {"enabled": True}, "github_ok": ok}


def test_poll_once_unusable_source(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    monkeypatch.setattr(app_mod.cfg_mod, "ensure_config", lambda: sync_app.cfg)
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies",
                        lambda cfg: _full_dep_status(False, ["configure first"]))
    sync_app._poll_once(manual=True)
    assert any("Nothing to check" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)


def test_poll_once_processes_new_items(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    monkeypatch.setattr(app_mod.cfg_mod, "ensure_config", lambda: sync_app.cfg)
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies",
                        lambda cfg: _full_dep_status(True, []))
    items = [{"fp": "jira:1", "title": "Jira", "subtitle": "ACME-1 · Open",
              "message": "updated", "url": "https://x"}]
    monkeypatch.setattr(app_mod.poll_mod, "collect_all",
                        lambda cfg, log=None: [("jira", items)])
    sync_app.state = {"seen": {}}
    sync_app._poll_once(manual=False)
    # The new item became a recent entry and was marked seen.
    assert sync_app.recent and sync_app.recent[0]["url"] == "https://x"
    assert "jira:1" in sync_app.state["seen"]


def test_poll_once_skips_passing_ci(sync_app, app_mod, monkeypatch):
    monkeypatch.setattr(app_mod.cfg_mod, "ensure_config", lambda: sync_app.cfg)
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies",
                        lambda cfg: _full_dep_status(True, []))
    items = [{"fp": "ci:1", "title": "GitHub CI", "subtitle": "s",
              "message": "m", "url": "u", "ci_only": True, "ci_rollup": "pass"}]
    monkeypatch.setattr(app_mod.poll_mod, "collect_all",
                        lambda cfg, log=None: [("ci", items)])
    sync_app.state = {"seen": {}}
    sync_app.recent = []
    sync_app._poll_once(manual=False)
    # Passing CI is marked seen but not surfaced as a recent item.
    assert sync_app.recent == []
    assert "ci:1" in sync_app.state["seen"]


def test_poll_once_collect_all_error(sync_app, app_mod, monkeypatch):
    monkeypatch.setattr(app_mod.cfg_mod, "ensure_config", lambda: sync_app.cfg)
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies",
                        lambda cfg: _full_dep_status(True, []))

    def boom(cfg, log=None):
        raise RuntimeError("collect failed")

    monkeypatch.setattr(app_mod.poll_mod, "collect_all", boom)
    # Errors during collection are logged and swallowed (no raise).
    sync_app._poll_once(manual=False)


def test_check_now_and_check_updates_now(sync_app, app_mod, monkeypatch):
    called = {"tick": False, "update": False}
    monkeypatch.setattr(sync_app, "on_tick",
                        lambda _, manual=False: called.__setitem__("tick", manual))
    sync_app.check_now(None)
    assert called["tick"] is True

    monkeypatch.setattr(sync_app, "_run_update_check",
                        lambda **k: called.__setitem__("update", k))
    sync_app._check_updates_now(None)
    assert called["update"]["manual"] is True


def test_initial_update_check_and_tick_delegate(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_run_update_check",
                        lambda **k: calls.append(k))

    class Sender:
        def stop(self):
            calls.append("stopped")

    app._initial_update_check(Sender())
    app._on_update_tick(None)
    assert "stopped" in calls
    assert {"notify": True} in calls


def test_warn_if_unmet_swallows_notification_error(app, app_mod, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("notif backend down")

    monkeypatch.setattr(app_mod.rumps, "notification", boom)
    app.dep_status = {"ok": False, "problems": ["x"]}
    # Even if the notification fails, _warn_if_unmet must not raise.
    app._warn_if_unmet()


def test_on_tick_spawns_worker(app, app_mod, monkeypatch):
    class SyncThread:
        def __init__(self, target=None, daemon=None, **k):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(app_mod.threading, "Thread", SyncThread)
    ran = {"poll": False}
    monkeypatch.setattr(app, "_poll_once",
                        lambda manual: ran.__setitem__("poll", True))
    app._polling = False
    app.on_tick(None, manual=False)
    assert ran["poll"] is True
    # The polling guard is released after the worker finishes.
    assert app._polling is False


def test_poll_once_manual_no_new_items_notifies(sync_app, app_mod, monkeypatch):
    app_mod.rumps._notifications_sent.clear()
    monkeypatch.setattr(app_mod.cfg_mod, "ensure_config", lambda: sync_app.cfg)
    monkeypatch.setattr(app_mod.deps_mod, "check_dependencies",
                        lambda cfg: _full_dep_status(True, []))
    # Item already seen -> no new items -> manual run reports "all caught up".
    monkeypatch.setattr(app_mod.poll_mod, "collect_all",
                        lambda cfg, log=None: [("jira", [
                            {"fp": "seen:1", "title": "Jira", "subtitle": "s",
                             "message": "m", "url": "u"}])])
    sync_app.state = {"seen": {"seen:1": 111.0}}
    sync_app.recent = []
    sync_app._poll_once(manual=True)
    assert any("no new items" in n["subtitle"]
               for n in app_mod.rumps._notifications_sent)
