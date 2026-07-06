# Dev Notifier — Tutorial

A step-by-step guide to installing, configuring, and running Dev Notifier: a
**macOS & Windows** tray app that watches Jira, GitHub & PagerDuty and shows
clickable desktop notifications.

On macOS it lives in the **menu bar**; on Windows it lives in the **system
tray** (bottom-right, near the clock). The behaviour is the same on both.

---

## 1. Install

### macOS — Option A: download the DMG (recommended)

1. Go to [Releases](../../releases) and download the latest
   `DevNotifier-<version>.dmg`.
2. Open the DMG and drag **DevNotifier.app** into your `Applications` folder.
3. This is an unsigned, open-source build (no paid Apple Developer
   certificate), so macOS blocks it on first launch. Open it once via:
   - **Right-click** `DevNotifier.app` → **Open** → click **Open** in the
     dialog.
   - If macOS says *"is damaged and can't be opened"*, run in Terminal:
     ```bash
     xattr -dr com.apple.quarantine /Applications/DevNotifier.app
     ```
     then double-click to open.
4. When prompted **"DevNotifier wants to send you notifications"**, click
   **Allow**. (Required for the notifications to appear — and for clicking them
   to open links.)

After launch, a lightning-bolt icon appears in your menu bar.

### Windows — Option A: download the EXE (recommended)

1. Go to [Releases](../../releases) and download the latest
   `DevNotifier-<version>.exe`.
2. Double-click to run it. This is an unsigned, open-source build, so Windows
   SmartScreen may warn *"Windows protected your PC"*. Click **More info** →
   **Run anyway**.
3. The app appears as a lightning-bolt icon in the **system tray**
   (click the ▲ "show hidden icons" arrow if it's collapsed). **Right-click**
   the icon for the menu.
4. Allow notifications if Windows prompts; toasts appear in the Action Center.

### Option B — run from source (macOS or Windows)

Requires Python 3.12+. Platform dependencies are selected automatically by
`requirements.txt` markers (rumps on macOS; pystray + Pillow + winotify on
Windows).

**macOS:**
```bash
git clone https://github.com/SteveZouWonder/dev-notifier.git
cd dev-notifier
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python launcher.py
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/SteveZouWonder/dev-notifier.git
cd dev-notifier
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python launcher.py
```

---

## 2. Prerequisites

Dev Notifier needs at least one working data source. It checks these on
startup (menu → **Status:** line, and **Check dependencies**).

### GitHub — via the `gh` CLI (no token needed)

1. Install the GitHub CLI:
   - **macOS:** `brew install gh`
   - **Windows:** `winget install --id GitHub.cli` (or download from
     <https://cli.github.com>). Open a **new** terminal afterward so `gh` is on
     your `PATH`.
2. Log in once:
   ```bash
   gh auth login
   ```
   Choose **GitHub.com** → **HTTPS** → authenticate in the browser. Grant at
   least the `repo` and `read:org` scopes.
3. Verify:
   ```bash
   gh auth status
   gh api user --jq .login
   ```

Dev Notifier calls `gh` under the hood, so no GitHub token is stored in its
config.

### Jira — API token

1. Create a token at
   <https://id.atlassian.com/manage-profile/security/api-tokens>
   → **Create API token** → copy it.
2. You'll paste it into the config file in the next step.

### PagerDuty — User API token (optional)

1. In the PagerDuty web app, click your avatar → **My Profile** → **User
   Settings**.
2. Under **API Access**, click **Create API User Token**, give it a description
   (e.g. `dev-notifier`), then **Create Key**.
3. **Copy the token now** — it's shown only once. It's a 20-character string.
4. You'll paste it into the config file in the next step. Leave `user_id` and
   `team_ids` blank — Dev Notifier auto-detects them from your token.

> Requests made with a personal (User) API token are restricted to your own
> permissions, so a read-only view of your incidents needs no extra setup.

---

## 3. Configure

On first launch a config file is created at:

```
macOS:    ~/.config/dev-notifier/config.json
Windows:  %APPDATA%\dev-notifier\config.json
```

Open it from the menu: **tray icon → Open config file**, and fill in:

```json
{
  "jira": {
    "enabled": true,
    "base_url": "https://your-domain.atlassian.net",
    "username": "you@example.com",
    "api_token": "PASTE_YOUR_JIRA_TOKEN_HERE"
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
  },
  "theme": "Orange"
}
```

Field reference:

| Field | Meaning |
|-------|---------|
| `jira.base_url` | Your Atlassian site, e.g. `https://acme.atlassian.net` |
| `jira.username` | Your Atlassian account email |
| `jira.api_token` | The token created in step 2 |
| `github.login` | Leave blank to auto-detect via `gh api user` |
| `pagerduty.enabled` | Set to `true` to turn on PagerDuty notifications |
| `pagerduty.api_token` | Your PagerDuty **User** API token (from step 2) |
| `pagerduty.user_id` | Leave blank to auto-detect via `/users/me` |
| `pagerduty.team_ids` | Leave blank (`[]`) to auto-detect your teams |
| `poll.interval_seconds` | How often to check (default 300 = 5 min) |
| `poll.window_minutes` | How far back each check looks (default 10) |
| `theme` | `Orange` \| `Green` \| `Purple` \| `Rainbow` \| `Yellow` |

After editing, click **Check dependencies** in the menu — the **Status:** line
should show `Jira ✓  ·  GitHub ✓  ·  PagerDuty ✓` (PagerDuty appears only when
enabled).

> The config file stays on your machine and is never committed or uploaded.

---

## 4. What gets monitored

- **Jira** — issues where you are assignee, reporter, or watcher that were
  updated recently, plus comments mentioning you.
- **GitHub** — review requests, mentions, assignments, and activity on your
  own PRs (via `gh api notifications`; the noisy `subscribed` reason is
  filtered out).
- **GitHub CI (fallback)** — the CI rollup of your open PRs, so you get pinged
  on ❌ failures / ⏳ pending even if GitHub notification settings suppress them.
  Green CI does not notify.
- **PagerDuty** (when enabled) — incidents **assigned to you** that are
  triggered or acknowledged, plus your **teams' incidents** changed within the
  poll window. Because each notification is keyed on the incident's last
  status-change time, transitions (acknowledge → resolve → escalate) resurface
  as new notifications.

When something new appears, Dev Notifier shows a native notification.
**Clicking the notification opens its Jira issue / PR / PagerDuty incident in
your browser.** You can also reopen anything later from the **Recent:** menu.

---

## 5. Using the menu

| Item | What it does |
|------|--------------|
| **Check now** | Poll immediately instead of waiting for the timer |
| **Status:** | Shows Jira / GitHub / PagerDuty readiness; click to re-check |
| **Recent:** | Last items seen; hover an entry → **Open** / **Remove** |
| **Clear all recent** | Empties the recent list |
| **Theme ▸** | Switch the tray icon color |
| **Start at login** | Toggle auto-start (macOS: LaunchAgent; Windows: `Run` registry entry) |
| **Check dependencies** | Re-run the gh / Jira / PagerDuty checks and report |
| **Open config file** | Edit your settings |
| **Quit** | Exit the app |

---

## 6. Start at login

Enable **menu → Start at login** to have Dev Notifier launch automatically when
you log in. This adds a per-user entry:

- **macOS** — a LaunchAgent at
  `~/Library/LaunchAgents/ai.stevezou.devnotifier.plist`.
- **Windows** — a value named `DevNotifier` under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

Toggling it off removes that entry. Nothing is installed system-wide.

---

## 7. Troubleshooting

**No notifications appear**
- **macOS:** check **System Settings → Notifications → DevNotifier** is set to
  *Allow* (style *Alerts* keeps them on screen); ensure Focus / Do Not Disturb
  is off.
- **Windows:** check **Settings → System → Notifications** is on for
  *Dev Notifier*, and turn off **Focus assist / Do not disturb**.

**Status shows `GitHub …` (not ✓)**
- `gh` isn't installed or logged in. Install it (macOS: `brew install gh`;
  Windows: `winget install --id GitHub.cli`), run `gh auth login`, then click
  **Check dependencies**. On Windows, make sure you opened a new terminal so
  `gh` is on the `PATH`.

**Status shows `Jira …` (not ✓)**
- Open the config file and make sure `base_url`, `username`, and `api_token`
  are filled with real values (not the `your-domain` / `@example.com`
  placeholders).

**Status shows `PagerDuty: ⚠ Needs token`**
- You set `pagerduty.enabled` to `true` but left `api_token` empty. Paste a
  User API token (My Profile → User Settings → API Access), then click
  **Check dependencies**. To turn PagerDuty off entirely, set `enabled` back to
  `false`.

**PagerDuty enabled but no incidents notify**
- With auto-detect, confirm your token can reach the API. Only incidents
  assigned to you (triggered/acknowledged) or your teams' incidents changed
  within `window_minutes` are surfaced; already-resolved older incidents won't
  re-notify.

**Clicking a notification doesn't open anything**
- Make sure you allowed notifications on first launch. If you denied it, re-enable
  it (macOS: System Settings → Notifications → DevNotifier; Windows: Settings →
  System → Notifications → Dev Notifier), then quit and relaunch.
- **Windows:** the toast's **Open** button opens the link; make sure a default
  browser is set.

**"App is damaged" on launch (macOS)**
- Run: `xattr -dr com.apple.quarantine /Applications/DevNotifier.app`

**"Windows protected your PC" on launch (Windows)**
- SmartScreen warns on unsigned apps. Click **More info** → **Run anyway**.

**See the logs**
```bash
# macOS
tail -f ~/.config/dev-notifier/notifier.log
```
```powershell
# Windows (PowerShell)
Get-Content -Wait "$env:APPDATA\dev-notifier\notifier.log"
```

---

## 8. Uninstall

### macOS

1. Quit from the menu.
2. Turn off **Start at login** first (or delete
   `~/Library/LaunchAgents/ai.stevezou.devnotifier.plist`).
3. Delete `/Applications/DevNotifier.app`.
4. Optionally remove settings and cache:
   ```bash
   rm -rf ~/.config/dev-notifier ~/Library/Caches/dev-notifier
   ```

### Windows

1. Quit from the tray menu.
2. Turn off **Start at login** first (or delete the `DevNotifier` value under
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, e.g.
   `reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v DevNotifier /f`).
3. Delete the `DevNotifier-<version>.exe` you downloaded.
4. Optionally remove settings and cache:
   ```powershell
   Remove-Item -Recurse -Force "$env:APPDATA\dev-notifier", "$env:LOCALAPPDATA\dev-notifier"
   ```
