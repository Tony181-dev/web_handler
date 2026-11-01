"""Start Web Handler: open the running app if there is one, otherwise serve it.

--stop ends a running app. --install finishes installing the offline package. Both are for the installers.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_handler import paths, server  # noqa: E402

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
        app.log.add("info", "Đang tắt app.")
        app.jobs.shutdown()
        os._exit(0)

    # Kept in a global: Windows calls it at any time, so it must never be garbage collected.
    global _console_handler
    _console_handler = HANDLER_ROUTINE(on_console_event)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)


def stop():
    if server.stop_saved_instance():
        say("Web Handler đã tắt.")
        return 0
    say("Không tắt được Web Handler đang chạy.")
    return 1


def install():
    from web_handler.setup_manager import SetupManager
    from web_handler.system_log import SystemLog

    paths.activate_site()
    say("Đang cài thư viện và trình duyệt dự phòng...")
    try:
        ready = SetupManager(SystemLog()).install_missing()
    except Exception as error:
        say(f"Chưa cài xong: {error}")
        return 1
    say("Đã cài xong." if ready else "Chưa cài xong. Hãy mở màn hình Setup của app.")
    return 0 if ready else 1


def main():
    if "--stop" in sys.argv:
        return stop()
    if "--install" in sys.argv:
        return install()
    no_browser = "--no-browser" in sys.argv
    existing = server.running_instance()
    if existing:
        say("Web Handler đang chạy rồi. Đang mở lại giao diện.")
        if not no_browser:
            webbrowser.open(existing)
        return 0
    # Still recorded but not answering: it is hung and may be holding the browser profile.
    server.stop_saved_instance()

    paths.activate_site()
    instance, app = server.create_server()
    shut_down_when_console_closes(app)
    url = f"http://127.0.0.1:{app.port}/?key={app.key}"
    say("Web Handler đang chạy.")
    say("Giữ cửa sổ này mở trong lúc dùng app. Đóng cửa sổ này để tắt app.")
    say(f"Nếu trình duyệt không tự mở, hãy dán địa chỉ này vào trình duyệt:\n{url}\n")
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
