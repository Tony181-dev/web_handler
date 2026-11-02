"""Environment checks that run on the standard library alone."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from . import child_processes, paths


def find_chrome():
    # Lets a machine with Chrome exercise the path for machines without it.
    if os.environ.get("WEB_HANDLER_BROWSER") == "chromium":
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
    return next(paths.CHROMIUM_DIR.rglob("chrome.exe"), None)


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
                creationflags=subprocess.CREATE_NO_WINDOW,
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
