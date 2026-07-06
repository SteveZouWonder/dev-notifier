# Windows Support Plan

Plan for extending Dev Notifier to run natively on **Windows**, while keeping
the existing **macOS** integration (rumps / AppKit / LaunchAgent) completely
unchanged.

> Status: **proposal / planning only**. No source-code behaviour changes are
> made by this document. Implementation is broken into staged PRs (see
> [Rollout](#8-rollout-order)).

---

## 1. Guiding principles

- **Zero change to the macOS code path.** The current `rumps` /
  `PyObjCTools.AppHelper.callAfter` / `launchctl` / plist / `open` logic is kept
  as-is, merely relocated into `platform_backend/macos.py`. macOS runtime
  behaviour stays identical to today.
- **Runtime dispatch.** Select the backend by `sys.platform`
  (`darwin` -> macOS, `win32` -> Windows).
- **Interface boundary.** All platform-specific operations are funneled through
  one backend interface. `NotifierApp` depends on that interface, not on `rumps`
  directly.

---

## 2. Why this is feasible

The codebase is already cleanly layered. The data layer is pure, cross-platform
Python (`urllib`, the `gh` CLI, `pathlib`). Platform coupling is concentrated in
a small number of places:

| Coupling point | Location | Today |
|---|---|---|
| Tray/menu UI + notifications | `notifier_app.py` (`rumps`, `PyObjCTools.AppHelper`) | macOS-only |
| Open URL / file | multiple `subprocess.run(["open", ...])` | macOS-only (`open` command) |
| Start at login | `deps.py` (LaunchAgent + `launchctl` + plist) | macOS-only |
| Auto-update install | `updater.py` (download `.dmg`, then `open`) | macOS-only |
| Config/cache dirs | `config.py` uses `~/.config`; `updater.py` uses `~/Library/Caches` | former is portable, latter needs work |

`poll.py`, `config.py`, and the network parts of `updater.py` are already
cross-platform and need little to no change.

---

## 3. Target module structure

```
src/
  platform_backend/            # NOTE: not "platform" (shadows the stdlib module)
    __init__.py                # get_backend() -> instance based on sys.platform
    base.py                    # abstract interface + neutral data structures
    macos.py                   # existing rumps logic moved here (behaviour unchanged)
    windows.py                 # new: pystray + winotify
  paths.py                     # new: cross-platform dirs (replaces hardcoded ~/Library/...)
  notifier_app.py              # refactor: use backend interface; drop direct rumps import
  config.py / poll.py          # largely untouched (already portable)
  updater.py                   # split: network logic portable; install logic per-platform
  deps.py                      # split: checks portable; login-item moves into backend
```

---

## 4. Backend abstraction (`base.py`)

```python
class TrayBackend(ABC):
    # lifecycle
    def run(self): ...                      # start the event loop (blocking)
    def run_on_main(self, fn): ...          # thread-safe UI update on the main thread
    # icon / menu
    def set_icon(self, path): ...
    def set_menu(self, items: list[MenuItem]): ...
    # timers
    def add_timer(self, fn, interval_s): ...
    # notifications
    def notify(self, title, subtitle, message, url=None, sound=False): ...
    def on_notification_click(self, handler): ...
    # system integration
    def open_url(self, url): ...            # replaces all ["open", url]
    def login_item_enabled(self) -> bool: ...
    def enable_login_item(self) -> bool: ...
    def disable_login_item(self) -> bool: ...
```

Backend capability mapping:

| Capability | macOS backend | Windows backend |
|---|---|---|
| Tray icon/menu | `rumps.App` / `rumps.MenuItem` | `pystray.Icon` / `pystray.MenuItem` |
| Main-thread dispatch | `AppHelper.callAfter` | pystray has no separate main-thread constraint; use a thread-safe queue / direct callback |
| Notifications | `rumps.notification` | `winotify.Notification` (supports click-to-open-URL) |
| Timers | `rumps.Timer` | looped `threading.Timer` / a background scheduler thread |
| Open URL | `subprocess.run(["open", url])` | `os.startfile(url)` / `webbrowser.open(url)` |
| Start at login | LaunchAgent plist + `launchctl` | registry `HKCU\...\Run` or a Startup-folder `.lnk` |
| Icon format | PNG | pystray uses `PIL.Image`; load from existing PNG (may need a generated `.ico`) |

---

## 5. Concrete coupling points to address

1. **`notifier_app.py`** (largest effort)
   - `class NotifierApp(rumps.App)` -> hold `self.backend` (composition, not inheritance)
   - `rumps.Timer` -> `backend.add_timer(...)`
   - `rumps.notification(...)` -> `backend.notify(...)`
   - `subprocess.run(["open", url])` (3 sites) -> `backend.open_url(url)`
   - `AppHelper.callAfter` -> `backend.run_on_main(...)`
   - menu construction with `rumps.MenuItem` -> neutral `MenuItem` data structure rendered by the backend

2. **`deps.py`**
   - `check_dependencies` / `check_gh` / `check_jira` / `check_pagerduty` -> unchanged (portable)
   - `augmented_env` (hardcoded Homebrew paths) -> on Windows resolve `gh.exe` from `PATH`; keep macOS behaviour as-is
   - `login_item_*` + LaunchAgent + plist -> move into each backend

3. **`updater.py`**
   - version check / `fetch_latest_release` / SHA verification -> kept portable
   - `current_version` reading `Info.plist` -> keep the macOS branch; Windows needs another version source (e.g. a `__version__` constant or the PyInstaller version resource)
   - `download_and_open` downloading a `.dmg` and `open` -> Windows downloads the matching installer (`.exe`/`.zip`) and uses `os.startfile`; the asset-match regex `DevNotifier-*.dmg` becomes platform-specific
   - `CACHE_DIR = ~/Library/Caches/...` -> use the cross-platform cache dir from `paths.py`

4. **`paths.py`** (new)
   - use `platformdirs` to unify config/state/log/cache directories
   - keep macOS at `~/.config/dev-notifier` (matches current behaviour; avoids migrating existing user configs)
   - Windows uses `%APPDATA%\dev-notifier`

---

## 6. New dependencies (Windows-only where possible)

Isolate with platform markers so the macOS environment is unaffected:

```
pystray ;      sys_platform == "win32"
winotify ;     sys_platform == "win32"
Pillow ;       sys_platform == "win32"    # required by pystray for icons
platformdirs                              # all platforms (lightweight; replaces hardcoded paths)
rumps ;        sys_platform == "darwin"
pyobjc-* ;     sys_platform == "darwin"
```

---

## 7. Testing strategy (keep CI >= 95% coverage)

- Follow the existing `tests/conftest.py` stub approach; add `fake_pystray` /
  `fake_winotify` stubs so Windows-backend logic can be tested on Linux CI.
- Mark tests that genuinely need Windows APIs with `@pytest.mark.windows`
  (add a Windows CI job, or run locally).
- Use **contract tests** for the backend interface (the same assertions run
  against both the macOS and Windows backend stubs) to guarantee consistent
  behaviour.
- All existing macOS tests are preserved and must stay green after the refactor.

---

## 8. Rollout order

Staged; each stage can ship as an independent PR.

| Stage | Content | Risk |
|---|---|---|
| P1 | Add `paths.py` + `platform_backend/base.py`; extract a neutral `MenuItem`; move existing macOS logic into `macos.py`; make `notifier_app.py` use the backend — **behaviour unchanged** | Medium (pure refactor, guarded by existing tests) |
| P2 | Implement `windows.py` (tray + menu + notifications + open_url + start-at-login) | Medium |
| P3 | `updater.py` Windows install path + Windows version source | Low |
| P4 | Windows packaging spec + CI Windows job + docs/CHANGELOG | Low |

**Key note:** P1 is the foundation and carries the highest risk (refactoring the
UI layer). Do P1 first and confirm the macOS tests are fully green with no
behaviour regression before moving to P2.

---

## 9. Packaging (PyInstaller)

- Add `packaging/dev-notifier-win.spec`: `--windowed` (no console), bundle a
  `.ico`, bundle assets.
- Output: a Windows `.exe` (optionally wrap with Inno Setup / NSIS for an
  installer, or ship a onefile exe directly).
- Add a `feat: add Windows support` entry to the CHANGELOG (Conventional
  Commits).
