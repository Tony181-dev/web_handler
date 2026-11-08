"""Bring the app's browser window up: desktop_windows on Windows, desktop_macos on macOS."""
import sys

if sys.platform == "win32":
    from .desktop_windows import raise_browser_windows  # noqa: F401
else:
    from .desktop_macos import raise_browser_windows  # noqa: F401
