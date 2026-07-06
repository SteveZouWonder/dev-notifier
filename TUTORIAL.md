# Dev Notifier — Tutorial

**Dev Notifier** is a little app that sits in your menu bar (macOS) or system
tray (Windows) and quietly watches **Jira**, **GitHub**, and **PagerDuty** for
things that concern you. When something new shows up, it pops a desktop
notification — and clicking it opens the issue / pull request / incident in your
browser.

You don't need to be a developer to use it. This guide starts with the
**5‑minute quick start**; the later sections are only there if you want them.

- New to it? Read **[Quick start](#quick-start)**.
- Want GitHub or PagerDuty too? See **[Add more sources](#add-more-sources-optional)**.
- Something not working? See **[Troubleshooting](#troubleshooting)**.
- Command‑line lover / power user? See **[Advanced](#advanced-optional)**.

---

## Quick start

Get Jira notifications working in about 5 minutes — all by clicking, no
command line needed.

### Step 1 — Install the app

**macOS**
1. Download the latest **`DevNotifier‑<version>.dmg`** from
   [Releases](../../releases).
2. Open it and drag **DevNotifier** into your **Applications** folder.
3. The first time, **right‑click** DevNotifier → **Open** → **Open**.
   *(This build is free and open‑source, so it isn't signed with a paid Apple
   certificate — that's why macOS asks. It's safe.)*
4. If it asks to send notifications, click **Allow**.

**Windows**
1. Download the latest **`DevNotifier‑<version>.exe`** from
   [Releases](../../releases).
2. Double‑click it. If Windows shows *"Windows protected your PC"*, click
   **More info** → **Run anyway**.
   *(Same reason as above — it's an unsigned open‑source build, not a virus.)*
3. If it asks to send notifications, click **Allow**.

A small **lightning‑bolt icon** now appears — in the menu bar (macOS, top‑right)
or the system tray (Windows, bottom‑right; click the ▲ arrow if it's hidden).

### Step 2 — Get a Jira token (30 seconds)

1. Open <https://id.atlassian.com/manage-profile/security/api-tokens> in your
   browser.
2. Click **Create API token**, give it a name like `dev-notifier`, and click
   **Create**.
3. **Copy** the token (you'll paste it in the next step).

### Step 3 — Enter your Jira details

1. Click the lightning‑bolt icon → **Open config file**. A text file opens.
2. Fill in three things under `"jira"`:
   - `base_url` — your Jira address, e.g. `https://acme.atlassian.net`
   - `username` — your Atlassian login email
   - `api_token` — paste the token from Step 2
3. **Save** the file.

> The file has short notes next to each field to guide you. Only change the
> values inside the quotes; keep the quotes and commas as they are.

### Step 4 — Confirm it's working

Click the icon → **Check dependencies**. The **Status** line should show
**Jira ✓**. That's it — you'll now get a notification whenever a relevant Jira
issue changes, and clicking it opens the issue.

Don't want GitHub? In the config file set `"enabled": false` under `"github"`,
save, and click **Check dependencies** again.

---

## What you'll get notified about

- **Jira** — issues where you're the assignee, reporter, or watcher that were
  updated, plus comments that mention you.
- **GitHub** *(optional)* — review requests, mentions, assignments, and activity
  on your own pull requests. Plus a heads‑up when your PR's checks **fail** or
  are **pending**.
- **PagerDuty** *(optional)* — incidents assigned to you or your teams, and when
  their status changes (acknowledge → resolve → escalate).

Click any notification to open it in your browser. You can also reopen recent
items from the **Recent** menu.

---

## Using the menu

Click (macOS) or right‑click (Windows) the icon:

| Item | What it does |
|------|--------------|
| **Check now** | Check right away instead of waiting for the timer |
| **Status** | Shows whether Jira / GitHub / PagerDuty are ready |
| **Recent** | The last items seen — reopen or remove them |
| **Clear all recent** | Empty the recent list |
| **Theme** | Change the icon color |
| **Start at login** | Launch automatically when you sign in |
| **Check dependencies** | Re‑check your setup and report any problems |
| **Open config file** | Edit your settings |
| **Quit** | Exit the app |

---

## Add more sources (optional)

### GitHub

GitHub uses the free **GitHub CLI** (`gh`) so no token is stored in the app.
This step does need a terminal once.

1. Install the GitHub CLI:
   - **macOS:** `brew install gh`
   - **Windows:** `winget install --id GitHub.cli` (or download from
     <https://cli.github.com>), then open a **new** terminal window.
2. Sign in once: run `gh auth login`, choose **GitHub.com → HTTPS**, and finish
   in the browser.
3. Back in Dev Notifier, click **Check dependencies** — GitHub should show ✓.

If you'd rather not use GitHub at all, set `"enabled": false` under `"github"`
in the config file.

### PagerDuty

1. In PagerDuty, go to your avatar → **My Profile → User Settings → API
   Access → Create API User Token**. Copy the token (shown once).
2. Click the icon → **Open config file**. Under `"pagerduty"`, set
   `"enabled": true` and paste the token into `"api_token"`. Save.
3. Click **Check dependencies** — PagerDuty should show ✓.

Your teams and user ID are detected automatically from the token.

---

## Start at login

Click the icon → **Start at login** to have Dev Notifier launch automatically
when you sign in. Click it again to turn it off. Nothing is installed
system‑wide, and it only affects your own user account.

---

## Troubleshooting

**No notifications appear**
- **macOS:** System Settings → Notifications → **DevNotifier** → set to *Allow*.
  Turn off Do Not Disturb / Focus.
- **Windows:** Settings → System → **Notifications** → turn on *Dev Notifier*.
  Turn off Focus assist / Do not disturb.

**Status shows Jira isn't ready**
- Open the config file and make sure `base_url`, `username`, and `api_token`
  are your real values (not the `your-domain` / `you@example.com` placeholders),
  then **Save** and click **Check dependencies**.
- If you see a note in the log about "could not read config", the file has a
  small typo (often a missing comma or quote). Your file is kept as‑is so you
  can fix it — see [View the log](#view-the-log).

**Status shows GitHub isn't ready**
- The `gh` tool isn't installed or you're not signed in. Follow
  [GitHub](#github) above, then click **Check dependencies**.

**Status shows "PagerDuty: Needs token"**
- You turned PagerDuty on but didn't paste a token. Add one (see
  [PagerDuty](#pagerduty)) or set `"enabled": false` to turn it back off.

**Clicking a notification doesn't open anything**
- Make sure you allowed notifications the first time. On Windows, also make sure
  you have a default web browser set.

**macOS says the app "is damaged"**
- This is the unsigned‑build warning. See [Advanced](#advanced-optional) for the
  one‑line fix, or right‑click → **Open** as in Step 1.

---

## Advanced (optional)

Everything below is for power users. A typical user never needs it.

### Run from source

Requires Python 3.12+. Platform dependencies are picked automatically.

**macOS**
```bash
git clone https://github.com/SteveZouWonder/dev-notifier.git
cd dev-notifier
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python launcher.py
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/SteveZouWonder/dev-notifier.git
cd dev-notifier
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python launcher.py
```

### Where files live

```
Config   macOS:   ~/.config/dev-notifier/config.json
         Windows: %APPDATA%\dev-notifier\config.json
Log      macOS:   ~/.config/dev-notifier/notifier.log
         Windows: %APPDATA%\dev-notifier\notifier.log
```

### Advanced config fields

The first‑run file only shows the common settings. You can add any of these by
hand; each has a sensible built‑in default, so you only need them to change
behaviour:

| Field | Default | Meaning |
|-------|---------|---------|
| `poll.interval_seconds` | `300` | How often to check (seconds) |
| `poll.window_minutes` | `1440` | How far back the first check looks |
| `poll.max_window_minutes` | `10080` | Cap on the look‑back window (7 days) |
| `jira.event_mode` | `true` | One notification per change/comment vs. per issue |
| `jira.event_fields` | `["status","assignee"]` | Which Jira field changes notify you |
| `github.login` | `""` | Leave blank to auto‑detect via `gh api user` |
| `pagerduty.user_id` / `team_ids` | auto | Leave blank to auto‑detect |
| `update.enabled` | `true` | Auto‑check GitHub Releases for a newer version |
| `theme` | `"Orange"` | `Orange \| Green \| Purple \| Rainbow \| Yellow` |

### View the log

```bash
# macOS
tail -f ~/.config/dev-notifier/notifier.log
```
```powershell
# Windows (PowerShell)
Get-Content -Wait "$env:APPDATA\dev-notifier\notifier.log"
```

### Start‑at‑login internals

- **macOS** — a LaunchAgent at
  `~/Library/LaunchAgents/ai.stevezou.devnotifier.plist`.
- **Windows** — a value named `DevNotifier` under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

### macOS "is damaged" fix

```bash
xattr -dr com.apple.quarantine /Applications/DevNotifier.app
```

### Uninstall

**macOS**
1. Quit from the menu, and turn off **Start at login**.
2. Delete `/Applications/DevNotifier.app`.
3. Optionally remove your data:
   ```bash
   rm -rf ~/.config/dev-notifier ~/Library/Caches/dev-notifier
   ```

**Windows**
1. Quit from the tray menu, and turn off **Start at login**.
2. Delete the `DevNotifier-<version>.exe` you downloaded.
3. Optionally remove your data:
   ```powershell
   Remove-Item -Recurse -Force "$env:APPDATA\dev-notifier", "$env:LOCALAPPDATA\dev-notifier"
   ```
