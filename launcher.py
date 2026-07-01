"""Entry point for the dev-notifier menu-bar app.

Adds ``src`` to the path and launches the rumps app. Kept minimal so the same
launcher works both from source (``python launcher.py``) and inside the
PyInstaller bundle.

@author SteveZou
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from notifier_app import main  # noqa: E402

if __name__ == "__main__":
    main()
