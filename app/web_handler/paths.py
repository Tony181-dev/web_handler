"""Locations of everything the app reads or writes. All relative to the app folder."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "app"
UI_DIR = Path(__file__).resolve().parent / "ui"
RUNTIME_DIR = ROOT / "runtime"
SITE_DIR = RUNTIME_DIR / "site"
CHROMIUM_DIR = RUNTIME_DIR / "chromium"
OFFLINE_DIR = ROOT / "offline"
WHEELS_DIR = OFFLINE_DIR / "wheels"
REQUIREMENTS_FILE = OFFLINE_DIR / "requirements.txt"
CHROMIUM_ZIP = OFFLINE_DIR / "chromium.zip"


def data_dir():
    return Path(os.environ.get("WEB_HANDLER_DATA") or ROOT / "data")


def flows_dir():
    return data_dir() / "flows"


def runs_dir():
    return data_dir() / "runs"


def profile_dir():
    return data_dir() / "profile"


def activate_site():
    """Make libraries installed by Setup importable in this process."""
    site = str(SITE_DIR)
    if SITE_DIR.is_dir() and site not in sys.path:
        sys.path.insert(0, site)
        import importlib

        importlib.invalidate_caches()


def child_env():
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = os.pathsep.join([str(APP_DIR), str(SITE_DIR)])
    return env
