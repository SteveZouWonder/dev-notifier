# AGENTS.md

Project-level instructions for AI agents working in this repository. These
rules are **mandatory**. Follow them exactly; when in doubt, ask the user.

---

## Git workflow rules (MANDATORY)

1. **Never commit directly to `main`.** No code change may be committed straight
   to the `main` branch.

2. **Before making any change, ask the user whether a new branch is needed.**
   Before editing files, ask the user: does this change need a new branch, and
   what should it be named? Do not create a branch until the user confirms.

3. **Never create a branch automatically.** Do not run `git checkout -b` /
   `git switch -c` (or otherwise create a branch) without the user's explicit
   consent.

4. **After completing a change, ask whether to open a PR.** Once the change is
   made and committed, proactively ask the user whether to create a Pull
   Request. The user decides.

5. **Never create a PR automatically.** Do not run `gh pr create` (or open a PR
   by any other means) without the user's explicit consent.

6. **Base new branches on the latest `main`.** When the user approves a branch,
   create it from the up-to-date remote default branch (`git fetch` first, then
   branch off `origin/main`). Keep the branch focused on a single change; do not
   mix unrelated edits.

### Quick flow

```
change requested
  -> ask: new branch needed? branch name?   (wait for user)
  -> after confirmation, create the branch and make the change
  -> finish and commit
  -> ask: create a PR?                       (wait for user)
  -> after confirmation, create the PR
```

---

## Project overview

Dev Notifier is a **menu-bar / system-tray app (macOS + Windows)** that polls
Jira, GitHub and PagerDuty for items relevant to the current user and shows
native, clickable desktop notifications.

- UI layer: abstracted behind `src/platform_backend/` — macOS uses `rumps`
  (AppKit/PyObjC), Windows uses `pystray` + `winotify`. `notifier_app.py` is
  toolkit-neutral and only talks to the backend interface.
- Entry point: `launcher.py` -> `src/notifier_app.py`.
- Data sources: `src/poll.py` (Jira REST API + `gh` CLI for GitHub + PagerDuty
  REST API). Each fetch records failures via `_record_error`; `collect_all`
  returns them in `extra["errors"]` and the app holds the poll cursor when a
  source failed. Keep that contract when adding a source.
- Config/state/logs live under `~/.config/dev-notifier/` (macOS) or
  `%APPDATA%\dev-notifier\` (Windows) — see `src/paths.py`; never in the repo.
- Never write the in-memory config back to disk wholesale: use
  `config.save_config_patch({...})` so a corrupt/first-run file is not clobbered.

### Layout

| Path | Purpose |
|------|---------|
| `src/notifier_app.py` | Tray app logic: timers, notifications, menu, threading (toolkit-neutral) |
| `src/platform_backend/` | `base.py` interface + `macos.py` (rumps) / `windows.py` (pystray, winotify) |
| `src/poll.py` | Fetch Jira/GitHub/PagerDuty items (pure data gathering + per-source error tracking) |
| `src/deps.py` | Dependency checks + macOS login-item (LaunchAgent) management |
| `src/config.py` | Config file loading/defaults, validation, safe patch-writes |
| `src/paths.py` | Per-platform config/cache directories |
| `src/updater.py` | Auto-update checks against GitHub Releases (SHA-256 verified downloads) |
| `scripts/` | Icon generation, CHANGELOG tooling, release-notes backfill |
| `packaging/` | PyInstaller spec + macOS packaging |
| `tests/` | pytest suite (uses `fake_rumps` stub to run on Linux) |

---

## Testing & coverage (MANDATORY for code changes)

- Run the suite before proposing changes as done:
  ```
  pytest tests/ -v
  ```
- CI enforces **>= 95% coverage** on the app source (`--cov-fail-under=95`).
  Keep new logic covered; aim to not drop coverage.
- Tests must run on **Linux CI without rumps/PyObjC/pystray installed**. The
  `fake_rumps` / `fake_pystray` / `fake_winotify` / `fake_winreg` fixtures
  (`tests/conftest.py`) stub the platform toolkits; app-logic tests drive
  `NotifierApp` through the recording `FakeBackend`. If you add a backend
  method, add it to `FakeBackend` too.
- Supported Python: **3.9+** (CI matrix 3.9–3.12). Don't use 3.10+-only syntax.
- Mark tests that require real rumps/AppKit with `@pytest.mark.macos`; they are
  excluded on Linux (`-m "not macos"`) and run on the macOS CI job.
- Keep tests hermetic: no real network, subprocess, or writes to the real
  `$HOME` (use the provided fixtures).

---

## Threading rule (important, easy to get wrong)

Network I/O (Jira/GitHub/PagerDuty/`gh`) runs on background worker threads so the menu
bar never freezes. **AppKit/rumps UI updates must happen on the main thread.**
Hand results back with `self._run_on_main(fn)`, which uses
`PyObjCTools.AppHelper.callAfter`.

Do **not** schedule UI callbacks with `rumps.Timer` from a worker thread: an
NSTimer started off a non-running run loop never fires, silently dropping all
results.

---

## Icons & theming

- Menu-bar icons are generated from SVG templates per theme via
  `python scripts/generate_icon.py` (needs `rsvg-convert`/librsvg, falls back to
  `qlmanage`).
- Any new icon state (e.g. the "checking" spinner) should follow the active
  theme's colors: add a themed SVG template and generate a
  `<theme>-<state>.png` for every theme, and bundle it in
  `packaging/dev-notifier.spec`.
- Do not commit re-rendered existing icon assets unless they are the point of
  the change; local librsvg versions differ from CI and produce byte churn.

---

## CHANGELOG & releases

- User-facing changes go under the `## [Unreleased]` section of `CHANGELOG.md`
  (Keep a Changelog format).
- Releases are tag-driven (`v*`). The workflow builds release notes from the
  CHANGELOG and archives `[Unreleased]` into the version section via a PR.
- If `[Unreleased]` is empty, entries are auto-generated from git history, so
  **use Conventional Commit subjects** (`feat:`, `fix:`, `docs:`, `perf:`,
  `refactor:`, `build:`, `ci:`) for clean changelog grouping.
- Tooling: `scripts/bump_changelog.py` (extract/bump) and
  `scripts/fix_release_notes.py` (backfill published release notes).

---

## Commit & PR conventions

- Use Conventional Commit messages (see above); keep the subject concise and
  imperative.
- Do not commit secrets or user config (`~/.config/dev-notifier/`).
- Do not commit build artifacts (`build/`, `dist/`) or caches
  (`.pytest_cache`, `.coverage`).
- Keep each branch/PR scoped to one logical change.
