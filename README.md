<div align="center">

# Dev Notifier 🔔

### A tiny macOS menu-bar app that watches Jira & GitHub for things relevant to you and shows clickable desktop notifications

[![Release](https://img.shields.io/github/v/release/SteveZouWonder/dev-notifier)](../../releases)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)

[Download](#download--install) · [Configure](#configuration) · [Build from source](#build-from-source)

</div>

---

Dev Notifier lives in your menu bar and polls, every few minutes:

- **Jira** — issues where you are the assignee, reporter, or watcher that were
  recently updated, plus comments mentioning you.
- **GitHub** — review requests, mentions, assignments, and activity on your own
  PRs (via the `gh` CLI notifications API).
- **GitHub CI (fallback)** — the CI rollup of your open PRs, so you get pinged
  on ❌ failures / ⏳ pending even if notification settings suppress them.

When something new shows up it raises a **native macOS notification**, and
**clicking the notification opens the Jira issue / PR in your browser**. New
items are also auto-opened (capped, so you don't get a wall of tabs).

Because it's a properly bundled `.app` with its own bundle identifier, macOS
grants it real notification permission — unlike ad-hoc `osascript`/CLI
notifications, its click action actually works.

## Download & Install

1. Grab the latest `DevNotifier-<version>.dmg` from
   [Releases](../../releases).
2. Open the DMG and drag **DevNotifier.app** to Applications.
3. This is an unsigned open-source build, so the first launch needs:
   **right-click DevNotifier → Open → Open**. (If it says "is damaged", run
   `xattr -dr com.apple.quarantine /Applications/DevNotifier.app`.)
4. Allow notifications when prompted.

## Configuration

On first launch a config file is created at:

```
~/.config/dev-notifier/config.json
```

Open it (menu-bar icon → **Open config file**) and fill in your details:

```json
{
  "jira": {
    "enabled": true,
    "base_url": "https://your-domain.atlassian.net",
    "username": "you@example.com",
    "api_token": "<your Jira API token>"
  },
  "github": {
    "enabled": true,
    "login": ""
  },
  "poll": {
    "interval_seconds": 300,
    "window_minutes": 10,
    "max_auto_open": 3
  }
}
```

- **Jira token:** create one at
  <https://id.atlassian.com/manage-profile/security/api-tokens>.
- **GitHub:** no token needed — it uses the [`gh` CLI](https://cli.github.com).
  Run `gh auth login` once. Leave `login` blank to auto-detect.
- **poll:** `interval_seconds` how often to check, `window_minutes` how far
  back each check looks, `max_auto_open` how many links to auto-open per check.

The config file stays on your machine and is never committed.

## Menu

- **Check now** — poll immediately.
- **Recent:** — the last items seen; click one to reopen it.
- **Open config file** — edit your settings.
- **Quit**.

## Build from source

Requires Python 3.12+ and (for packaging) `create-dmg` (`brew install create-dmg`).

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-build.txt

# Run directly:
python launcher.py

# Or build the .app + .dmg:
python scripts/generate_icon.py
APP_VERSION=1.0.0 pyinstaller packaging/dev-notifier.spec --noconfirm
APP_VERSION=1.0.0 bash packaging/macos_package.sh
# -> dist/DevNotifier-1.0.0.dmg
```

## Releasing

Push a tag and GitHub Actions builds and publishes the DMG:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## License

MIT
