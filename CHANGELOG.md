# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Record unreleased changes for the next version here. On release they are moved under the corresponding version number.

### Added
- automatic update checks against GitHub Releases: menu-bar prompt + clickable notification when a newer version is available, with one-click download (SHA-256 verified) that opens the DMG to install; "Check for updates" and "Skip this version" menu actions

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
