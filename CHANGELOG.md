# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Record unreleased changes for the next version here. On release they are moved under the corresponding version number.

### Added
- the menu's Recent list now persists across restarts/upgrades: up to the 100 most recent items are saved to `state.json` (via the atomic writer) and restored on launch, instead of resetting to empty every time the app quits. The menu still shows the newest 10, and removing a shown item now backfills the next kept item into view; the id counter is persisted too so restored entries can't collide with new ones

## [v1.5.6] - 2026-07-08

### Fixed
- menu-bar menu actions work again on macOS: clicking a Recent item's "Open"/"Remove" or switching the theme did nothing since the platform-backend refactor. rumps calls the callback with its own menu item as the sender, but the handlers read tag attributes (`entry_id`, `theme_name`) that live on the toolkit-neutral menu item; the macOS backend now passes the neutral item through as the sender (matching the Windows backend), so those tags survive

## [v1.5.5] - 2026-07-08

### Fixed
- clicking the notification's "Open" button now reliably opens the item's URL. Notifications that carry a link now include an explicit "Open" action button; previously only clicking the notification body worked, because macOS does not deliver the system default button's click (with the URL) to the app
- GitHub notifications no longer re-notify the same thread repeatedly. The de-duplication fingerprint now keys on the notification thread id alone instead of `id` + `updated_at`; previously any new activity in a thread (comment, push, re-requested review) bumped `updated_at` and minted a fresh fingerprint, so the same item popped up again
- the seen-items state is now written atomically (temp file + `os.replace`). A crash or kill mid-write can no longer leave a truncated `state.json` that fails to parse on restart and resets the de-dup memory, which would otherwise re-notify every currently-unread item

## [v1.5.4] - 2026-07-07

### Changed
- internal groundwork toward cross-platform support: introduced a `platform_backend` abstraction (system integration: open-URL, start-at-login, main-thread dispatch) and a cross-platform `paths` module for config/cache directories. macOS behaviour and paths are unchanged; `rumps` is now an install requirement only on macOS (see `docs/windows-support-plan.md`)
- added a Windows platform backend implementing the system-integration surface: open-URL via `os.startfile`, start-at-login via the per-user `Run` registry key, and native toast notifications (with a clickable "Open" action) via `winotify`. The Windows tray/menu UI is not wired up yet; this is the backend foundation. `winotify` is an install requirement only on Windows
- the auto-updater is now cross-platform: the cache directory uses the shared `paths` helper, the release fetch selects the current platform's installer asset (`.exe` on Windows, `.dmg` on macOS) while keeping the macOS `dmg_*` fields, and the download step launches the installer via `os.startfile` on Windows / `open` on macOS. Windows-frozen builds read their version from the bundled `__version__`. macOS behaviour and paths are unchanged
- Windows packaging and CI: added a PyInstaller spec (`packaging/dev-notifier-win.spec`) producing a one-file `DevNotifier.exe`, a packaging script (`packaging/windows_package.ps1`) that names it `DevNotifier-<version>.exe` (matched by the updater), a `test-windows` CI job running the suite on `windows-latest`, and a `build-windows` release job that builds the `.exe` and publishes it alongside the macOS DMG in the same GitHub Release (with combined `SHA256SUMS.txt`)
- the tray/menu UI is now fully behind the platform backend: `notifier_app` is toolkit-neutral (no direct `rumps` import) and drives the active backend through its interface. macOS renders the menu/icon/notifications/timers via `rumps` exactly as before; Windows renders a real tray icon and right-click menu via `pystray` (with `Pillow` for icon images) plus `winotify` toasts. `pystray` and `Pillow` are install requirements only on Windows
- documentation: the README and TUTORIAL now cover Windows alongside macOS — install (`.exe` / SmartScreen), the `gh` CLI via `winget`, the `%APPDATA%` config path, start-at-login via the `Run` registry entry, troubleshooting, and uninstall
- friendlier onboarding for non-technical users: the first-run config file is now a **simple template** showing only the settings you fill in (Jira/GitHub/PagerDuty), each with a short plain-language note; advanced tuning options (`poll.*`, `jira.event_mode`, etc.) are omitted from the file but keep their built-in defaults. The TUTORIAL was reorganised into a click-only **Quick start** plus an **Advanced** section, and the README config example was slimmed to the three Jira fields most users need
- a broken/typo'd config file is no longer silently discarded: the app still starts with defaults for that run, but your file is **left unchanged** and a note is written to the log so you can fix the typo without losing your edits

### Fixed
- on Windows the config/state/log files now live under `%APPDATA%\dev-notifier` (via the shared `paths` helper) instead of a `.config` folder in the home directory; macOS keeps `~/.config/dev-notifier` unchanged

## [v1.5.3] - 2026-07-06

### Added
- Jira notifications are now event-level to match Jira's notification feed: instead of one notification per updated issue, each in-window status/assignee change and each comment is notified individually. Controlled by `jira.event_mode` (default on) and `jira.event_fields` (default `["status", "assignee"]`; comments are always included). Set `jira.event_mode` to `false` for the previous issue-level behaviour

### Fixed
- Jira timestamps with a timezone offset lacking a colon (e.g. `-0400`) are now parsed correctly; previously they raised `ValueError` on Python < 3.11 and could silently drop comment/changelog events
- the `seen` de-duplication TTL now scales with `poll.max_window_minutes` (window + 3-day margin) so an event still inside the lookback window is never re-notified after its de-dup record would have expired

## [v1.5.2] - 2026-07-03

### Changed
- first-run poll lookback (`poll.window_minutes`, the fallback used when there is no prior poll) now defaults to 24 hours instead of 10 minutes, so the very first poll after install surfaces the past day's activity

### Fixed
- "Check for updates" failing with "Couldn't check / Check your network" because the updater verified TLS with the default CA store, which the packaged app / stock macOS Python cannot use to verify `api.github.com`; it now uses the `certifi` CA bundle (same fix already applied to Jira/PagerDuty)

## [v1.5.1] - 2026-07-03

### Changed
- poll lookback is now dynamic: each poll records its timestamp and the next poll's window spans from the previous poll to now, so no Jira/GitHub/PagerDuty update between polls is missed regardless of the poll interval. The window is capped by the new configurable `poll.max_window_minutes` (default 7 days) so a long sleep/shutdown doesn't fetch an unbounded backlog; `poll.window_minutes` is now only the first-run fallback

## [v1.5.0] - 2026-07-03

### Added
- add PagerDuty as a notification source
- theme desktop notification icons

### Fixed
- verify TLS for Jira/PagerDuty via certifi CA bundle

### Other
- verify notification icon follows theme switch

## [v1.4.0] - 2026-07-03

### Added
- PagerDuty as a notification source (REST API v2): notifies on incidents assigned to you (triggered/acknowledged) and on your teams' incidents changed within the poll window, so status changes (acknowledge/resolve/escalate) resurface; configure a user API token in `config.json` (leave `user_id`/`team_ids` blank to auto-detect via `/users/me`). A "PagerDuty" line is shown in the Status menu.
- automatic update checks against GitHub Releases: menu-bar prompt + clickable notification when a newer version is available, with one-click download (SHA-256 verified) that opens the DMG to install; "Check for updates" and "Skip this version" menu actions
- immediate feedback for manual "Check now": a themed spinner menu-bar icon and a "Checking…" menu item that follow the active theme's colors, restored when the check finishes

### Changed
- desktop notifications now show the active theme's colored icon instead of the plain default app icon

### Fixed
- Jira (and PagerDuty) notifications never arriving because every API request failed TLS verification (`CERTIFICATE_VERIFY_FAILED`) on the stock macOS Python; requests now use the `certifi` CA bundle, which is bundled into the packaged app
- manual checks producing no notifications at all: poll results were scheduled onto the main thread with an NSTimer started from a worker thread, which never fired and silently dropped every result; results are now marshalled via `AppHelper.callAfter`
- surface a "Check failed" notification when a manual check errors, instead of failing silently

### Documentation
- add `AGENTS.md` with mandatory AI-agent instructions (Git workflow, testing/coverage, threading, icons, release rules)

### Build
- add a pytest suite and CI/CD pipeline; stub `PyObjCTools` so `notifier_app` imports on Linux CI without PyObjC

## [v1.3.0] - 2026-07-01

### Fixed
- eliminate startup lag by moving network checks off the main thread

## [v1.2.0] - 2026-07-01

### Added
- automate CHANGELOG-driven release notes and archival

### Other
- UX improvements: manual-check feedback, status submenu, theme fixes, no auto-open

## [v1.1.0]

### Added
- Setup tutorial (`TUTORIAL.md`).
- Dependency checks for the `gh` CLI and Jira configuration, surfaced in the menu.
- "Start at login" toggle backed by a per-user LaunchAgent.

## [v1.0.0]

### Added
- Menu-bar app polling Jira and GitHub for relevant items.
- Native macOS notifications that open the related URL on click.
- Themed lightning-bolt menu-bar icons.
- macOS packaging (PyInstaller + DMG).
