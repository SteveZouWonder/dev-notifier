"""macOS platform backend.

Implements the :class:`~platform_backend.base.TrayBackend` interface on top of
``rumps`` (AppKit/PyObjC). It owns a ``rumps.App`` and translates the app's
toolkit-neutral :class:`~platform_backend.base.MenuItem`s into ``rumps.MenuItem``
objects, so ``notifier_app`` no longer touches rumps directly. Behaviour is
preserved: colored (non-template) menu-bar icons, click-to-open notifications,
and worker-thread results marshalled onto the main run loop.

The login-item methods delegate to :mod:`deps` (LaunchAgent via ``launchctl``),
reusing the established, tested implementation verbatim.

``rumps`` / ``PyObjCTools`` are imported lazily so this module is importable —
and unit-testable via the ``fake_rumps`` stub — on non-macOS CI.

@author SteveZou
"""
import subprocess

import deps as _deps

from platform_backend.base import MenuItem, TrayBackend, Timer


class _RumpsTimer(Timer):
    """Wraps a ``rumps.Timer`` so the app sees a uniform start/stop handle."""

    def __init__(self, rumps_timer):
        self._t = rumps_timer

    def start(self):
        self._t.start()

    def stop(self):
        self._t.stop()


class MacOSBackend(TrayBackend):
    """Backend for macOS (Darwin)."""

    def __init__(self):
        self._app = None
        # Click handler is registered by notifier_app via on_notification_click.
        self._click_handler = None

    # -- lifecycle ----------------------------------------------------------
    def setup(self, name, icon):
        import rumps

        # Colored (non-template) icon so themes show their color in the menu bar.
        self._app = rumps.App(name=name, icon=icon, template=False,
                              quit_button=None)

        # Register the delivered-notification click handler. rumps routes clicks
        # to the function decorated with @rumps.notifications; wire it to open
        # the URL carried in the notification's data.
        @rumps.notifications
        def _on_click(info):
            url = (info.data or {}).get("url")
            if url:
                self.open_url(url)

    def run(self):
        self._app.run()

    def quit(self):
        import rumps

        rumps.quit_application()

    def run_on_main(self, fn):
        # An NSTimer started off the main run loop never fires, so results must
        # be marshalled with AppHelper.callAfter (not a rumps.Timer).
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn, None)

    # -- tray appearance / menu --------------------------------------------
    def set_icon(self, path):
        self._app.icon = path

    def set_title(self, title):
        self._app.title = title

    def _to_rumps(self, item: MenuItem):
        """Translate a neutral MenuItem into a rumps MenuItem (recursively)."""
        import rumps

        if item.separator:
            return rumps.separator
        r = rumps.MenuItem(item.title, callback=item.callback)
        r.state = item.state
        if item.icon:
            try:
                r.set_icon(item.icon, dimensions=(16, 16), template=False)
            except Exception:  # noqa: BLE001 - a bad image must not break the menu
                pass
        for child in item.children:
            r.add(self._to_rumps(child))
        return r

    def set_menu(self, items):
        self._app.menu.clear()
        self._app.menu = [self._to_rumps(i) for i in items]

    def add_timer(self, fn, interval_s):
        import rumps

        t = rumps.Timer(fn, interval_s)
        t.start()
        return _RumpsTimer(t)

    # -- notifications ------------------------------------------------------
    def notify(self, title="", subtitle="", message="", data=None, sound=False,
               icon=None, action_button=None):
        import rumps

        kwargs = {"title": title, "subtitle": subtitle, "message": message,
                  "data": data or {}, "sound": sound}
        if icon is not None:
            kwargs["icon"] = icon
        # An explicit action button makes clicking it deliver the activation
        # (activationType == action_button_clicked) to the @rumps.notifications
        # handler, so the URL opens. The system default button does not.
        if action_button is not None:
            kwargs["action_button"] = action_button
        rumps.notification(**kwargs)

    # -- system integration -------------------------------------------------
    def open_url(self, url):
        subprocess.run(["open", url], check=False)

    def login_item_enabled(self):
        return _deps.login_item_enabled()

    def enable_login_item(self):
        return _deps.enable_login_item()

    def disable_login_item(self):
        return _deps.disable_login_item()
