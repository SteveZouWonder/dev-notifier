# Dev Notifier — Tutorial

A step-by-step guide to installing, configuring, and running Dev Notifier: a
macOS menu-bar app that watches Jira & GitHub and shows clickable desktop
notifications.

---

## 1. Install

### Option A — download the DMG (recommended)

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

### Option B — run from source

Requires Python 3.12+.

```bash
git clone https://github.com/SteveZouWonder/dev-notifier.git
cd dev-notifier
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python launcher.py
```

---

## 2. Prerequisites

Dev Notifier needs at least one working data source. It checks these on
startup (menu → **Status:** line, and **Check dependencies**).

### GitHub — via the `gh` CLI (no token needed)

1. Install the GitHub CLI:
   ```bash
   brew install gh
   ```
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

---

## 3. Configure

On first launch a config file is created at:

```
~/.config/dev-notifier/config.json
```

Open it from the menu: **menu-bar icon → Open config file**, and fill in:

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
  "poll": {
    "interval_seconds": 300,
    "window_minutes": 10,
    "max_auto_open": 3
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
| `poll.interval_seconds` | How often to check (default 300 = 5 min) |
| `poll.window_minutes` | How far back each check looks (default 10) |
| `poll.max_auto_open` | Max links auto-opened per check (default 3) |
| `theme` | `Orange` \| `Green` \| `Purple` \| `Rainbow` \| `Bell` |

After editing, click **Check dependencies** in the menu — the **Status:** line
should show `Jira ✓  ·  GitHub ✓`.

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

When something new appears, Dev Notifier shows a native notification and
auto-opens the link (capped by `max_auto_open`). **Clicking any notification
opens its Jira issue / PR in your browser.**

---

## 5. Using the menu

| Item | What it does |
|------|--------------|
| **Check now** | Poll immediately instead of waiting for the timer |
| **Status:** | Shows Jira / GitHub readiness; click to re-check |
| **Recent:** | Last items seen; hover an entry → **Open** / **Remove** |
| **Clear all recent** | Empties the recent list |
| **Theme ▸** | Switch the menu-bar icon color |
| **Start at login** | Toggle auto-start (installs/removes a LaunchAgent) |
| **Check dependencies** | Re-run the gh/Jira checks and report |
| **Open config file** | Edit your settings |
| **Quit** | Exit the app |

---

## 6. Start at login

Enable **menu → Start at login** to have Dev Notifier launch automatically when
you log in. This writes a per-user LaunchAgent to:

```
~/Library/LaunchAgents/ai.stevezou.devnotifier.plist
```

Toggling it off unloads and removes that file. Nothing is installed
system-wide.

---

## 7. Troubleshooting

**No notifications appear**
- Check **System Settings → Notifications → DevNotifier** is set to *Allow*
  (style *Alerts* keeps them on screen).
- Ensure Focus / Do Not Disturb is off.

**Status shows `GitHub …` (not ✓)**
- `gh` isn't installed or logged in. Run `brew install gh` then
  `gh auth login`, and click **Check dependencies**.

**Status shows `Jira …` (not ✓)**
- Open the config file and make sure `base_url`, `username`, and `api_token`
  are filled with real values (not the `your-domain` / `@example.com`
  placeholders).

**Clicking a notification doesn't open anything**
- Make sure you allowed notifications on first launch. If you denied it, enable
  it in System Settings → Notifications → DevNotifier, then quit and relaunch.

**"App is damaged" on launch**
- Run: `xattr -dr com.apple.quarantine /Applications/DevNotifier.app`

**See the logs**
```bash
tail -f ~/.config/dev-notifier/notifier.log
```

---

## 8. Uninstall

1. Quit from the menu.
2. Turn off **Start at login** first (or delete
   `~/Library/LaunchAgents/ai.stevezou.devnotifier.plist`).
3. Delete `/Applications/DevNotifier.app`.
4. Optionally remove settings: `rm -rf ~/.config/dev-notifier`.
