"""macOS platform backend.

Wraps the existing macOS integrations so ``notifier_app`` can reach them
through the :class:`~platform_backend.base.TrayBackend` interface without
changing behaviour:

- ``open_url`` shells out to ``open`` (the app has always used this).
- The login-item methods **delegate to** :mod:`deps`, which manages the
  per-user LaunchAgent plist via ``launchctl``. They are not reimplemented here,
  so the established, tested behaviour is preserved verbatim.
- ``run_on_main`` marshals worker-thread results onto the main run loop via
  ``PyObjCTools.AppHelper.callAfter`` — the same mechanism ``notifier_app``
  already relies on (an NSTimer started off the main run loop never fires, so
  callAfter is required, not a rumps.Timer).

@author SteveZou
"""
import subprocess

import deps as _deps

from platform_backend.base import TrayBackend


class MacOSBackend(TrayBackend):
    """Backend for macOS (Darwin)."""

    def run_on_main(self, fn):
        # Imported lazily so this module is importable on non-macOS machines
        # (and under the Linux CI stub) without PyObjC installed.
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn, None)

    def open_url(self, url):
        subprocess.run(["open", url], check=False)

    def login_item_enabled(self):
        return _deps.login_item_enabled()

    def enable_login_item(self):
        return _deps.enable_login_item()

    def disable_login_item(self):
        return _deps.disable_login_item()
