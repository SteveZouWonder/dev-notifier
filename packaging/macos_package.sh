#!/usr/bin/env bash
# macOS packaging: ad-hoc sign the PyInstaller .app and wrap it into a .dmg.
#
# Usage (from repo root, after pyinstaller build):
#   APP_VERSION=1.0.0 packaging/macos_package.sh
#
# This is a free, unsigned (ad-hoc) build — no Apple Developer certificate.
# Users open it via right-click -> Open the first time.
set -euo pipefail

APP_VERSION="${APP_VERSION:-0.0.0}"
APP_NAME="DevNotifier"
DIST_DIR="dist"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
DMG_DIR="${DIST_DIR}/dmg"
DMG_PATH="${DIST_DIR}/${APP_NAME}-${APP_VERSION}.dmg"
ENTITLEMENTS="packaging/entitlements.plist"

echo "==> Verify .app exists"
if [[ ! -d "${APP_PATH}" ]]; then
  echo "ERROR: ${APP_PATH} not found. Run: pyinstaller packaging/dev-notifier.spec --noconfirm"
  exit 1
fi

echo "==> Ad-hoc sign (deep, with entitlements)"
codesign --force --deep --sign - \
  --entitlements "${ENTITLEMENTS}" \
  --options runtime \
  "${APP_PATH}" || {
    echo "==> hardened-runtime sign failed; falling back to basic ad-hoc"
    codesign --force --deep --sign - "${APP_PATH}"
  }

echo "==> Verify signature"
codesign --verify --verbose "${APP_PATH}" || echo "(ad-hoc verify warning, ok)"

echo "==> Stage DMG contents"
rm -rf "${DMG_DIR}"
mkdir -p "${DMG_DIR}"
cp -R "${APP_PATH}" "${DMG_DIR}/"

cat > "${DMG_DIR}/READ ME FIRST.txt" <<'EOF'
Dev Notifier — first launch
================================

This is a free, open-source, unsigned build (no Apple Developer certificate),
so macOS blocks the first launch with "is damaged and can't be opened" or
"cannot verify developer". That is expected. (Apple Silicon build.)

To open:

  1. Drag DevNotifier.app into your Applications folder (do not run it from
     this window — "Start at login" needs it in Applications).
  2. Open Terminal and run:
       xattr -dr com.apple.quarantine /Applications/DevNotifier.app
  3. Double-click DevNotifier in Applications. The first launch can take
     10-20 seconds while macOS scans it.

  Without the terminal: try to open it, then System Settings -> Privacy &
  Security -> "Open Anyway". (On macOS 14 and earlier, right-click -> Open ->
  Open also works.) You will need to repeat this after each update.

First run creates a config file at:
    ~/.config/dev-notifier/config.json
Fill in your Jira URL / email / API token there, save, then click "Check
dependencies" in the app menu. GitHub uses the `gh` CLI
(https://cli.github.com) — run `gh auth login` once. PagerDuty is optional
(set "enabled": true and paste a User API token).

The app lives in the menu bar (lightning-bolt icon). Allow notifications when
prompted. Full guide: https://github.com/SteveZouWonder/dev-notifier/blob/main/TUTORIAL.md
EOF

echo "==> Build DMG"
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg \
    --volname "${APP_NAME} ${APP_VERSION}" \
    --window-pos 200 120 \
    --window-size 640 400 \
    --icon-size 100 \
    --icon "${APP_NAME}.app" 160 200 \
    --app-drop-link 480 200 \
    --no-internet-enable \
    "${DMG_PATH}" \
    "${DMG_DIR}" || {
      echo "==> create-dmg failed; falling back to hdiutil"
      hdiutil create -volname "${APP_NAME} ${APP_VERSION}" \
        -srcfolder "${DMG_DIR}" -ov -format UDZO "${DMG_PATH}"
    }
else
  echo "==> create-dmg not found; using hdiutil"
  hdiutil create -volname "${APP_NAME} ${APP_VERSION}" \
    -srcfolder "${DMG_DIR}" -ov -format UDZO "${DMG_PATH}"
fi

echo "==> Done: ${DMG_PATH}"
