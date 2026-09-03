# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Record unreleased changes for the next version here. On release they are moved under the corresponding version number.

### Added
- PagerDuty on-call shift reminders: a heads-up before your shift starts (default 1 day and 1 hour before, `pagerduty.oncall_remind_before_minutes`), when it starts, and when it ends (silent), sourced from `/oncalls`. Turn off with `pagerduty.oncall_reminders: false`
- a **PagerDuty ▸** menu (when enabled) showing whether you are on-call right now and until when (or when your next shift starts), and the open incidents assigned to you — click one to open it. The Status line also shows `· on-call`
- PagerDuty now notifies when an incident is **escalated or reassigned to you**, when someone **requests you as a responder**, when a **note mentions you**, and on **notes, priority/urgency changes, snoozes, ack timeouts and exhausted escalation paths** on incidents assigned to you or your teams; each notification says who did it (e.g. `[assigned to you] escalated to you by Bob — Disk full`)
- low-urgency PagerDuty incidents are tagged `· low urgency` and notify silently; set `pagerduty.low_urgency_sound: true` to keep the sound. `pagerduty.notify_team_incidents: false` limits notifications to incidents assigned to you (plus escalations / responder requests aimed at you)
- Jira changes made by apps/automation rules (e.g. "Automation for Jira" moving a ticket to *Code Review* right after you opened a PR) no longer notify, unless the rule assigns the issue to you. New `jira.suppress_automation` key (default `true`) turns this off
- `github.ci_notify` (default `["fail"]`) picks which CI roll-up states on your own open PRs pop up. `pending` — which every push of yours produces — is now remembered silently instead of notifying; add `"pending"` to hear about it again, or set `[]` to silence CI roll-ups entirely
- failing sources are no longer silent. The **Status ▸** submenu shows when the last check ran and, per source, the live error (`Jira: ⚠ HTTP 401 unauthorized — check the API token / username`, `timed out`, `TLS certificate verification failed (corporate proxy?)`, …) instead of a misleading `✓ Ready`; a ⚠ badge appears next to the tray icon while a source or the config has problems; a "check problems" notification tells you which source failed and why (once per distinct failure on the timer, always on **Check now**); and **Check now** no longer claims "You're all caught up" when a source failed. Status ▸ also gained **View log**
- **Pause notifications ▸** (1 hour / 4 hours / until resumed) mutes pop-ups without quitting; items still land in Recent and a ⏸ badge shows while paused. The pause survives restarts
- burst protection: when one check finds more than 5 new items (first launch, back from a weekend offline) they are folded into one summary notification ("12 new updates — Jira 7 · GitHub 5 — see Recent") instead of a machine-gun of sounded pop-ups; every item is still listed under Recent
- config problems are visible: unknown keys (typos such as `supress_self`) and values of the wrong type are listed under Status ▸ as `Config: ⚠ …` and logged once, instead of being silently ignored; an unparsable file is shown there too
- the "setup needed" / "Nothing to check" / dependency-check notifications carry an **Open** button that opens `config.json`
- macOS: if the app was moved since **Start at login** was enabled, the LaunchAgent is re-pointed at the current location on the next launch; enabling it while running from the mounted DMG is refused with an explanation (that path vanishes when the image is ejected); failures to toggle it are reported instead of silently leaving the checkbox unchanged
- `poll.interval_seconds` edits take effect on the next config reload (no relaunch)
- the **PagerDuty ▸** menu lists every escalation level you are currently on with level-specific wording — **On-call now** (level 1), **Backup on-call (level 2)**, **Fallback on-call (level N)** — plus your next shift; each row opens its schedule / escalation policy in PagerDuty so the menu can be checked against the service page. A schedule-less entry is labelled "direct policy target, no end". `pagerduty.oncall_max_level` (default `1`) decides which levels count as "on-call" for the header, the Status line and the shift reminders
- pre-release builds: tagging `vX.Y.Z-<suffix>` (e.g. `v2.0.0-beta`) publishes a GitHub **pre-release**, which is excluded from `/releases/latest` so existing installs are not offered it by the in-app updater; the CHANGELOG `[Unreleased]` section is left in place for the final release. The updater now ranks a pre-release below its final version, so `2.0.0-beta` users are offered `2.0.0` (and `2.0.0` users are never offered the beta)

### Changed
- the poll cursor is only advanced when every enabled source fetched successfully. Previously a failed fetch (401, timeout, VPN not up yet after wake) looked like "no news" and the window moved on, so the events from that interval were lost for good; they are now fetched on the next successful check (fingerprints prevent duplicates from the sources that did succeed)
- Jira, GitHub and PagerDuty are fetched concurrently and in isolation: a slow source no longer delays the others, and an unexpected exception in one source is recorded as that source's error instead of aborting the whole poll and discarding the results already fetched
- **Check dependencies** (and Status ▸ **Re-check now**) re-reads `config.json` first, so the documented "fill in the file, save, click Check dependencies" flow works immediately instead of after the next 5-minute poll
- the unconfigured-state reminder ("Nothing to check") is shown once per distinct problem on the timer instead of every 5 minutes, and is always shown on a manual **Check now** (which used to end in silence)
- the in-app updater now **requires** a matching SHA-256 from the release's `SHA256SUMS.txt`; a download whose checksum file cannot be fetched, or that is not listed, is discarded with an explanation (previously it was opened unverified with only a log line). The macOS menu item is **Download update…** and the follow-up says what to actually do (quit, drag-replace, relaunch, and the `xattr` line for Gatekeeper) instead of "Installer opened — follow the installer"
- Status ▸ distinguishes `GitHub: ⚠ Needs gh CLI` from `⚠ Needs login (gh auth login)`
- `config.json` is written atomically and created user-readable only (0600); menu actions (theme, skip version) patch just the key they change via `config.save_config_patch` and refuse to write while the file is unparsable
- the `gh` login lookup is skipped entirely when GitHub is disabled (it logged a WARN on every poll for non-GitHub users)
- docs: install steps lead with the `xattr` / "Open Anyway" path (right-click → Open no longer works on macOS 15), note the Apple-Silicon-only DMG, the "Background Items Added" notice and first-launch scan delay; new **Updating** section and expanded troubleshooting (permission denied, Focus allow-list, body-vs-Open click, stale login item, two icons, config typos, HTTP 401/timeout/TLS); Windows uninstall covers the installer; README gained a **Known limitations** section; Python requirement corrected to 3.9+; `scripts/doctor.sh` checks quarantine, architecture, JSON validity and a stale LaunchAgent path; the DMG "READ ME FIRST" and release-notes template were refreshed
- GitHub `ci_activity` threads ("workflow run failed for … branch") are suppressed under `github.suppress_self`: by GitHub's definition they are workflow runs *you* triggered, and CI on your open PRs is already covered by the roll-up items (so a failure no longer pops up twice)
- the Jira identity lookup (`/rest/api/3/myself`) is cached for 6 hours and, if a refresh fails, the last known id is reused; the GitHub login auto-detection is cached too
- PagerDuty incident notifications are now driven by the incident **timeline** (`/log_entries`) instead of polling incident status: every event has a stable id, so each acknowledge / resolve / escalate / reassign notifies exactly once, transitions that don't change the status (reassign, escalate) are no longer missed, and resolves of incidents assigned to you are detected even when you have no teams or the incident is old. A new incident's trigger + initial assignment is reported as a single notification
- `pagerduty.suppress_self` now applies to incidents assigned to you as well (previously only to team incidents), so acknowledging your own incident no longer pops up a notification
- the PagerDuty identity lookup (`/users/me`) is cached for 6 hours instead of running on every poll when `user_id`/`team_ids` are left blank, and incident/timeline/on-call queries follow pagination instead of stopping at 50 results
- the "All good" dependency-check and "setup needed" messages now mention PagerDuty (previously only Jira/GitHub); the TUTORIAL documents `suppress_self` and the new PagerDuty options
- Windows releases now ship a real installer, `DevNotifier-<version>-setup.exe` (Inno Setup; per-user, no admin rights): Start Menu entry, uninstaller, an optional "start when I sign in" task, and in-place upgrades that close the running app and relaunch it. The bare one-file exe is still published as `DevNotifier-<version>-portable.exe`. The in-app updater prefers the installer asset
- Windows builds carry the release version (bundled `APP_VERSION` stamp plus a `VERSIONINFO` resource visible in Properties ▸ Details) and ship a proper multi-size `.ico`
- `gh` is also looked up in its usual Windows install locations (`Program Files\GitHub CLI`, scoop shims), and the "gh CLI is not installed" hint suggests `winget install --id GitHub.cli` on Windows instead of `brew install gh`

### Fixed
- your own actions could still notify you, and others' actions on your PRs could be hidden:
  - GitHub decided "who acted last" from `subject.latest_comment_url`, which GitHub only updates for *comments*. After a push, review, review request, merge or close it is stale or points at the PR itself — whose `user` is the PR **author**. So a reviewer approving your PR looked like your own activity (hidden), while your own push (or any lookup failure) fell through and popped up as "your PR updated". The latest actor is now read from the PR/issue **timeline** (resolving pushes to the commit's GitHub login), the login comparison is case-insensitive, and lookups are cached per thread update
  - pushes whose git email GitHub cannot link to an account (typically a mistyped `git config user.email`) are attributed via your profile's public email or the new `github.emails` list, and — on a PR you authored — assumed to be yours (a one-time hint is logged)
  - when the actor lookup failed (network hiccup) the thread was notified anyway and marked seen. It is now skipped and re-checked on the next poll instead
  - one failed `/rest/api/3/myself` call (it ran on every poll) switched Jira self-suppression off for that whole poll, letting your own comments/status changes through — 52 such polls were found in one log. See the identity cache above
  - Jira's legacy issue-level mode (`event_mode: false`) ignored `suppress_self` entirely; issues whose only in-window activity is yours are now skipped
- Windows builds always reported version `1.3.0` because the release version was never stamped into the exe, so every launch announced an "update available", and "Download & Install" merely opened the downloaded one-file exe — a second copy of the app — while claiming an installer had been opened. The app now reads the version stamped at build time, so it recognises when it is up to date, and updates run the real installer
- "Start at login" on Windows pinned the registry `Run` entry to whatever path the exe was launched from (e.g. `Downloads\DevNotifier-1.5.8.exe`), silently breaking once the file was moved or deleted. It now points at the installed `DevNotifier.exe` when the app is installed, and the installer repoints an existing entry
- `gh` output is decoded as UTF-8 (it was decoded with the Windows locale codec, so a non-ASCII PR title or user name raised `UnicodeDecodeError` and dropped the whole GitHub poll), and `gh` is run without flashing a console window on Windows
- GitHub threads notified **once per thread, ever**: the fingerprint was the notification thread id, which never changes for a PR, so the first review request popped up and every later review, comment or merge on that PR was silent for at least 7 days. The fingerprint now includes the thread's `updated_at` (your own activity is still filtered by `suppress_self`)
- GitHub CI: after the first failing run on a PR, later failures on the same PR (fail → push → fail) never notified because the fingerprint was `(PR, roll-up)`. It now includes the identity of the check runs (from `gh pr checks --json link`)
- `github.ci_notify: ["pass"]` was silently ignored — the app force-quieted every passing roll-up regardless of config. It now honours the `quiet` flag computed from `ci_notify`
- changing the theme or clicking **Skip this version** wrote the whole in-memory config back to disk. On first run that replaced the friendly template (with its `_readme` notes) by bare defaults; when `config.json` was unparsable it **overwrote the user's real credentials with placeholders**. Only the changed key is patched now, and nothing is written while the file cannot be parsed. `DEFAULT_CONFIG` is also deep-copied so those edits can no longer leak into the module defaults
- Windows: the two startup one-shot timers (first dependency check, first update check) call `sender.stop()`, but the Windows timer passed `None` as the sender, so both raised `AttributeError` on every fire — re-arming forever — and the startup check never ran (Status stuck on "Checking…", no "setup needed" hint, no update check). The Windows timer now passes itself as the sender, matching rumps, and a failing tick no longer kills the timer
- an unexpected exception inside the poll worker (e.g. a malformed `gh` payload) left the app stuck in "Checking…" — **Check now** greyed out and the spinner icon shown until relaunch — with the traceback lost on stderr. It is now logged, shown in Status ▸, and the UI state is restored
- the stale `updater.__version__` (`1.3.0`) is now `1.5.8` so source runs report a sensible version
- **Windows: the app crashed at startup** (`ValueError` from `pystray` in `set_menu`). pystray only accepts menu callbacks with 0–2 positional parameters by `__code__.co_argcount`, and the adapter lambda carried two extra default-valued parameters. Callbacks are now built as two-argument closures; the `fake_pystray` test stub enforces the same rule so this cannot regress
- PagerDuty on-call status showed you as on-call when you were only the level-2/3 fallback on an escalation policy: `/oncalls` returns every level you sit on, and a schedule-less "direct target" entry took precedence over (and could never be replaced by) a real scheduled shift. Deeper levels are now listed with their own wording but do not count as on-call by default (`pagerduty.oncall_max_level`), and scheduled shifts win over direct targets regardless of the order PagerDuty returns them

## [v1.5.8] - 2026-07-15

### Added
- actions you trigger yourself no longer notify you: Jira changelog changes/comments you authored, PagerDuty status changes you made (e.g. you acknowledged/resolved), and GitHub threads whose latest activity was your own are now suppressed. This is on by default and controlled per source via a new `suppress_self` config key (set it to `false` under `jira`/`github`/`pagerduty` to keep being notified of your own activity). Jira matches on your `accountId` (resolved via `/rest/api/3/myself`), PagerDuty on your `user_id`, and GitHub on your login

## [v1.5.7] - 2026-07-08

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
