<div align="center">

<img src="assets/app-icon.png" alt="Dev Notifier app icon" width="128" height="128" />

# Dev Notifier

### A tiny macOS & Windows tray app that watches Jira, GitHub & PagerDuty for things relevant to you and shows clickable desktop notifications

[![Release](https://img.shields.io/github/v/release/SteveZouWonder/dev-notifier)](../../releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)

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
- **PagerDuty** — every change on incidents assigned to you or your teams
  (triggered, acknowledged, escalated / reassigned **to you**, resolved, notes,
  priority changes, responder requests), each saying who did it; plus on‑call
  shift reminders (heads‑up before your shift, start, end) and a menu showing
  whether you're on‑call and your open incidents (via the PagerDuty REST API).

When something new shows up it raises a **native desktop notification**, and
**clicking the notification opens the Jira issue / PR / incident in your
browser**. On macOS it's a properly bundled `.app` with its own bundle
identifier (so macOS grants real notification permission); on Windows it uses
native Action Center toasts via `winotify`.

## Download & Install

### macOS

1. Grab the latest `DevNotifier-<version>.dmg` from
   [Releases](../../releases). The build is **Apple Silicon (arm64)**; Intel
   Macs are not supported by the prebuilt DMG (build from source instead).
2. Open the DMG and drag **DevNotifier.app** to Applications. Don't run it
   from inside the DMG window — **Start at login** needs the app in
   Applications.
3. This is an unsigned open-source build, so macOS blocks the first launch
   ("DevNotifier is damaged and can't be opened" / "cannot be opened because
   the developer cannot be verified"). Clear the quarantine flag once:

   ```bash
   xattr -dr com.apple.quarantine /Applications/DevNotifier.app
   ```

   Alternatively: try to open it, then **System Settings → Privacy &
   Security → Open Anyway**. (On macOS 14 and earlier, right-click → Open →
   Open also works.) You will need to do this again after each update.
4. Allow notifications when prompted. The first launch can take 10–20 s while
   macOS scans the app; the ⚡ icon appears when it's ready.

### Windows

1. Grab the latest `DevNotifier-<version>-setup.exe` from
   [Releases](../../releases).
2. Run it and follow the installer (per-user, no admin rights needed). It adds
   a Start Menu entry, an uninstaller and — if you leave the box ticked —
   starts the app when you sign in. This is an unsigned open-source build, so
   SmartScreen may warn ("Windows protected your PC" → **More info** →
   **Run anyway**).
3. The app appears in the system tray (right-click for the menu). Allow
   notifications when prompted.

Prefer no installer? `DevNotifier-<version>-portable.exe` is the bare app:
just run it from wherever you keep it. In-app updates always fetch the
installer, which replaces an existing install in place.

## Configuration

On first launch a **simple** config file is created (only the fields you need to
fill in — advanced options have sensible defaults and aren't shown):

```
macOS:    ~/.config/dev-notifier/config.json
Windows:  %APPDATA%\dev-notifier\config.json
```

Open it from the menu (tray icon → **Open config file**). To get started you
usually only need three Jira values:

```json
{
  "jira": {
    "enabled": true,
    "base_url": "https://your-domain.atlassian.net",
    "username": "you@example.com",
    "api_token": "<your Jira API token>"
  }
}
```

- **Jira token:** create one at
  <https://id.atlassian.com/manage-profile/security/api-tokens>, then paste it
  into `api_token`.
- **GitHub** *(off by default)*: set `"enabled": true` under `github` to turn
  it on. No token needed — it uses the [`gh` CLI](https://cli.github.com);
  run `gh auth login` once.
- **PagerDuty** *(off by default)*: set `"enabled": true` and paste a personal
  **User API token** into `api_token` — create it under your avatar →
  **My Profile → User Settings → API Access → Create API User Token**
  (`https://<your-subdomain>.pagerduty.com/users/<your-user-id>`; not an
  admin *General Access* key). Step‑by‑step instructions:
  [Tutorial → PagerDuty](TUTORIAL.md#pagerduty). On‑call reminders and team
  incident events are on by default; see the Tutorial for `oncall_reminders`,
  `notify_team_incidents`, `low_urgency_sound` and friends.

After editing, save and click **Check dependencies** in the menu. The config
file stays on your machine and is never committed. For a step‑by‑step walkthrough
and the full list of advanced options, see the **[Tutorial](TUTORIAL.md)**.

## Menu

- **Check now** — poll immediately (manual pull). If a source fails (bad
  token, network down) you're told which one and why — never a false "all
  caught up".
- **Status ▸** — when the last check ran and whether Jira / GitHub / PagerDuty
  are working. A failing source shows its error here (and a ⚠ badge appears
  next to the tray icon) until it recovers. Also **Re-check now** and
  **View log**.
- **PagerDuty ▸** *(when enabled)* — whether you're on‑call right now (and until
  when) or when your next shift starts, plus your open incidents; click one to
  open it.
- **Pause notifications ▸** — mute pop-ups for 1 h / 4 h / until you resume
  (items still land in Recent; a ⏸ badge shows while paused).
- **Recent:** — the last items seen; hover → **Open** / **Remove**.
- **Clear all recent** — empty the list.
- **Check for updates** / **Update available: x.y.z ▸** — download the new
  release (SHA-256 verified). On macOS this opens the DMG for you to
  drag-replace; on Windows it runs the installer.
- **Theme ▸** — switch the tray icon color.
- **Start at login** — toggle auto-start (macOS: a LaunchAgent; Windows: a
  per-user `Run` registry entry).
- **Check dependencies** — reload `config.json` and re-run the gh / Jira /
  PagerDuty checks (use this after editing the file).
- **Open config file** — edit your settings.
- **Quit**.

### Dependency checks

On startup the app verifies the `gh` CLI (installed + logged in), your Jira
config, and your PagerDuty token, showing the result in the **Status ▸**
submenu and guiding you if something is missing. Credentials are only proven
by the first real poll: if Jira/PagerDuty reject them, Status shows the HTTP
error next to that source. You can also run the standalone doctor (macOS):

```bash
bash scripts/doctor.sh
```

### Start at login

Enable **Start at login** to auto-launch on login. On macOS it writes a per-user
LaunchAgent to `~/Library/LaunchAgents/ai.stevezou.devnotifier.plist` (macOS
may show a "Background Items Added" notice); on Windows it adds a per-user
value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Toggling
it off removes the entry. Nothing is installed system-wide. If you move the app
later, the registration is repaired automatically on the next launch.

See the full [Tutorial](TUTORIAL.md) for setup, troubleshooting, and uninstall.

## Build from source

Requires Python 3.9+ (CI tests 3.9–3.12). Platform dependencies (`rumps` on macOS; `pystray` +
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

# Or build the installer + portable .exe (needs Inno Setup 6:
# winget install JRSoftware.InnoSetup):
$env:APP_VERSION = "1.0.0"   # stamped into the exe; the updater reads it
pyinstaller packaging/dev-notifier-win.spec --noconfirm
pwsh packaging/windows_package.ps1
# -> dist/DevNotifier-1.0.0-setup.exe, dist/DevNotifier-1.0.0-portable.exe
# (set $env:DEVNOTIFIER_SKIP_INSTALLER = "1" to skip the Inno Setup step)
```

## Known limitations

- **Unsigned builds.** No Developer ID / notarization, so macOS Gatekeeper and
  Windows SmartScreen warn on first launch and after each update (see above).
- **Apple Silicon only** DMG; Intel users must build from source.
- **Jira Cloud only** (REST API v3 + email/API-token auth). Jira Server / Data
  Center is not supported.
- **PagerDuty US region only** (`api.pagerduty.com`); EU-region accounts are
  not yet supported.
- **GitHub.com only**; GitHub Enterprise Server links are not rewritten.
- GitHub needs the `gh` CLI installed and logged in.
- Behind a TLS-intercepting corporate proxy, Jira/PagerDuty calls verify
  against the bundled `certifi` CA bundle and may fail; Status will show
  "TLS certificate verification failed".

## Releasing

Push a tag and GitHub Actions builds and publishes both the macOS DMG and the
Windows EXE (with a combined `SHA256SUMS.txt`) to the same Release:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## License

MIT
