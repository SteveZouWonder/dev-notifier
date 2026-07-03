# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Record unreleased changes for the next version here. On release they are moved under the corresponding version number.

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
