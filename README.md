<div align="center">

<img src="assets/app-icon.png" alt="Dev Notifier app icon" width="128" height="128" />

# Dev Notifier

### A tiny macOS & Windows tray app that watches Jira, GitHub & PagerDuty for things relevant to you and shows clickable desktop notifications

[![Release](https://img.shields.io/github/v/release/SteveZouWonder/dev-notifier)](../../releases)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)

[Download](#download--install) · [Configure](#configuration) · [Tutorial](TUTORIAL.md) · [Build from source](#build-from-source)

</div>

---

Dev Notifier lives in your menu bar (macOS) or system tray (Windows) and polls,
every few minutes:

- **Jira** — issues where you are the assignee, reporter, or watcher that were
  recently updated, plus comments mentioning you.
- **GitHub** — review requests, mentions, assignments, and activity on your own
  PRs (via the `gh` CLI notifications API).
- **GitHub CI (fallback)** — the CI rollup of your open PRs, so you get pinged
  on ❌ failures / ⏳ pending even if notification settings suppress them.
- **PagerDuty** — incidents assigned to you (triggered/acknowledged) and your
  teams' incidents changed recently, so acknowledge / resolve / escalate status
  changes resurface (via the PagerDuty REST API).

When something new shows up it raises a **native desktop notification**, and
**clicking the notification opens the Jira issue / PR / incident in your
browser**. On macOS it's a properly bundled `.app` with its own bundle
identifier (so macOS grants real notification permission); on Windows it uses
native Action Center toasts via `winotify`.

## Download & Install

### macOS

1. Grab the latest `DevNotifier-<version>.dmg` from
   [Releases](../../releases).
2. Open the DMG and drag **DevNotifier.app** to Applications.
3. This is an unsigned open-source build, so the first launch needs:
   **right-click DevNotifier → Open → Open**. (If it says "is damaged", run
   `xattr -dr com.apple.quarantine /Applications/DevNotifier.app`.)
4. Allow notifications when prompted.

### Windows

1. Grab the latest `DevNotifier-<version>.exe` from
   [Releases](../../releases).
2. Run it. This is an unsigned open-source build, so SmartScreen may warn
   ("Windows protected your PC" → **More info** → **Run anyway**).
3. The app appears in the system tray (right-click for the menu). Allow
   notifications when prompted.

## Configuration

On first launch a config file is created at:

```
macOS:    ~/.config/dev-notifier/config.json
Windows:  %APPDATA%\dev-notifier\config.json
```

Open it (tray icon → **Open config file**) and fill in your details:

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
  "pagerduty": {
    "enabled": false,
    "api_token": "",
    "user_id": "",
    "team_ids": []
  },
  "poll": {
    "interval_seconds": 300,
    "window_minutes": 10
  }
}
```

- **Jira token:** create one at
  <https://id.atlassian.com/manage-profile/security/api-tokens>.
- **GitHub:** no token needed — it uses the [`gh` CLI](https://cli.github.com).
  Run `gh auth login` once. Leave `login` blank to auto-detect.
- **PagerDuty:** set `enabled` to `true` and paste a **User API token**
  (PagerDuty → *My Profile → User Settings → API Access → Create API User
  Token*). Leave `user_id` and `team_ids` blank to auto-detect the current user
  and their teams via `/users/me`.
- **poll:** `interval_seconds` how often to check, `window_minutes` how far
  back each check looks.

The config file stays on your machine and is never committed.

## Menu

- **Check now** — poll immediately (manual pull).
- **Status:** — shows whether Jira / GitHub / PagerDuty are ready; click to re-check.
- **Recent:** — the last items seen; hover → **Open** / **Remove**.
- **Clear all recent** — empty the list.
- **Theme ▸** — switch the tray icon color.
- **Start at login** — toggle auto-start (macOS: a LaunchAgent; Windows: a
  per-user `Run` registry entry).
- **Check dependencies** — re-run the gh / Jira / PagerDuty checks.
- **Open config file** — edit your settings.
- **Quit**.

### Dependency checks

On startup the app verifies the `gh` CLI (installed + logged in), your Jira
config, and your PagerDuty token, showing the result in the **Status:** line and
guiding you if something is missing. You can also run the standalone doctor:

```bash
bash scripts/doctor.sh
```

### Start at login

Enable **Start at login** to auto-launch on login. On macOS it writes a per-user
LaunchAgent to `~/Library/LaunchAgents/ai.stevezou.devnotifier.plist`; on Windows
it adds a per-user value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Toggling it off removes the
entry. Nothing is installed system-wide.

See the full [Tutorial](TUTORIAL.md) for setup, troubleshooting, and uninstall.

## Build from source

Requires Python 3.12+. Platform dependencies (`rumps` on macOS; `pystray` +
`Pillow` + `winotify` on Windows) are selected automatically by
`requirements.txt` markers.

### macOS

Needs `create-dmg` (`brew install create-dmg`) for packaging.

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

### Windows

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements-build.txt

# Run directly:
python launcher.py

# Or build the one-file .exe:
$env:APP_VERSION = "1.0.0"
pyinstaller packaging/dev-notifier-win.spec --noconfirm
pwsh packaging/windows_package.ps1
# -> dist/DevNotifier-1.0.0.exe
```

## Releasing

Push a tag and GitHub Actions builds and publishes both the macOS DMG and the
Windows EXE (with a combined `SHA256SUMS.txt`) to the same Release:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## License

MIT
