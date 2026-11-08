"""Environment checks that run on the standard library alone."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from . import child_processes, paths


MAC_CHROME = Path("Applications") / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
# What Playwright unpacks on macOS: Chrome for Testing in newer releases, Chromium in older ones.
MAC_CHROMIUM_NAMES = ("Google Chrome for Testing", "Chromium")


def find_chrome():
    # Lets a machine with Chrome exercise the path for machines without it.
    if os.environ.get("WEB_HANDLER_BROWSER") == "chromium":
        return None
    if sys.platform != "win32":
        for folder in (Path("/"), Path.home()):
            path = folder / MAC_CHROME
            if path.is_file():
                return path
        return None
    import winreg

    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, subkey) as key:
                path = winreg.QueryValue(key, None)
            if path and Path(path).exists():
                return Path(path)
        except OSError:
            pass
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        folder = os.environ.get(variable)
        if folder:
            path = Path(folder) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if path.exists():
                return path
    return None


def find_bundled_chromium():
    if not paths.CHROMIUM_DIR.is_dir():
        return None
    if sys.platform == "win32":
        return next(paths.CHROMIUM_DIR.rglob("chrome.exe"), None)
    for name in MAC_CHROMIUM_NAMES:
        for path in paths.CHROMIUM_DIR.rglob(name):
            if path.parent.name == "MacOS" and path.is_file() and os.access(path, os.X_OK):
                return path
    return None


def run_with_timeout(cmd, timeout, env=None):
    """Return (exit code or None, output, timed out). Kills the whole process tree on timeout.

    Output goes to a file because a surviving grandchild holding a pipe would
    otherwise block the read after the child itself has been killed.
    """
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                env=paths.child_env() if env is None else env,
                **child_processes.POPEN_OPTIONS,
            )
        except OSError as error:
            return None, str(error), False
        job = child_processes.adopt(process)
        timed_out = False
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if job:
                child_processes.end_job(job)
            else:
                process.kill()
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
        if job:
            child_processes.release_job(job)
        output.seek(0)
        return process.returncode, output.read().decode("utf-8", "replace"), timed_out


def is_bundled_python():
    try:
        return Path(sys.executable).resolve().is_relative_to(paths.RUNTIME_DIR.resolve())
    except OSError:
        return False
