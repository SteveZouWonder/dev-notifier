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
so macOS may warn "cannot verify developer" or "is damaged" on first launch.
That is expected.

To open (either option):

Option A (recommended):
  1. Drag DevNotifier.app into your Applications folder.
  2. Right-click DevNotifier in Applications -> Open.
  3. Click "Open" in the dialog. It launches normally afterwards.

Option B (if it says "is damaged and can't be opened"):
  Open Terminal and run:
    xattr -dr com.apple.quarantine /Applications/DevNotifier.app
  Then double-click to open.

First run creates a config file at:
    ~/.config/dev-notifier/config.json
Fill in your Jira URL / email / API token there. GitHub uses the `gh` CLI
(https://cli.github.com) — run `gh auth login` once.

The app lives in the menu bar (bell icon). Allow notifications when prompted.
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
