"""Start Web Handler: open the running app if there is one, otherwise serve it.

--stop ends a running app. --install finishes installing the offline package.
--forget-secrets removes the saved secrets from Windows Credential Manager. All three are for the installers.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_handler import paths, server  # noqa: E402
from web_handler.i18n import t  # noqa: E402

CTRL_C_EVENT = 0
HANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
_console_handler = None


def say(text):
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    stream.write(text.encode(encoding, "replace").decode(encoding) + "\n")
    stream.flush()


def shut_down_when_console_closes(app):
    """Closing the window, signing out or shutting down ends this process without any Python cleanup."""

    def on_console_event(event):
        if event == CTRL_C_EVENT:
            return False
        app.log.add("info", t("Closing the app."))
        app.jobs.shutdown()
        os._exit(0)

    # Kept in a global: Windows calls it at any time, so it must never be garbage collected.
    global _console_handler
    _console_handler = HANDLER_ROUTINE(on_console_event)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)


def stop():
    if server.stop_saved_instance():
        say(t("Web Handler has closed."))
        return 0
    say(t("Could not close the running Web Handler."))
    return 1


def install():
    from web_handler.setup_manager import SetupManager
    from web_handler.system_log import SystemLog

    paths.activate_site()
    say(t("Installing the libraries and the spare browser..."))
    try:
        ready = SetupManager(SystemLog()).install_missing()
    except Exception as error:
        say(t("Not finished installing: {error}", error=error))
        return 1
    say(t("Installed.") if ready else t("Not finished installing. Open the app's Setup screen."))
    return 0 if ready else 1


def forget_secrets():
    """For an uninstall that deletes the data: the secrets themselves live outside the app folder."""
    from web_handler import secret_store

    paths.activate_site()
    names = secret_store.names()
    try:
        for name in names:
            secret_store.delete_secret(name)
    except Exception as error:
        say(t("Could not remove the saved secrets: {error}", error=error))
        return 1
    say(t("Removed {count} saved secrets from Windows Credential Manager.", count=len(names)))
    return 0


def main():
    if "--stop" in sys.argv:
        return stop()
    if "--install" in sys.argv:
        return install()
    if "--forget-secrets" in sys.argv:
        return forget_secrets()
    no_browser = "--no-browser" in sys.argv
    existing = server.running_instance()
    if existing:
        say(t("Web Handler is already running. Opening its page again."))
        if not no_browser:
            webbrowser.open(existing)
        return 0
    # Still recorded but not reusable: it is hung or an older version, and may be holding the browser profile.
    server.stop_saved_instance()

    paths.activate_site()
    instance, app = server.create_server()
    shut_down_when_console_closes(app)
    url = f"http://127.0.0.1:{app.port}/?key={app.key}"
    say(t("Web Handler is running."))
    say(t("Keep this window open while using the app. Close it to close the app."))
    say(t("If the browser does not open by itself, paste this address into it:") + f"\n{url}\n")
    if not no_browser:
        webbrowser.open(url)
    try:
        instance.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.jobs.shutdown()
        instance.server_close()
    # Page streams may still be open on their threads, and nothing must keep this process alive.
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
