"""Web Handler environment probe.

Runs on the standard library alone so it still works on a system Python when
the bundled one is blocked. Anything that needs a third-party library runs in a
child process with a time limit, so a hung browser can never stop the report.
Only 127.0.0.1 is contacted.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.request

PROBE_VERSION = "1"
EXPECTED_PYTHON = (3, 12)
MAX_DETAIL = 1500
SERVER_ROUNDS = 20
RESULT_MARKER = "__WEB_HANDLER_PROBE_RESULT__"
WINDOWS = sys.platform == "win32"
# Helpers get no console window on Windows, and a session of their own on macOS so the whole tree can be ended.
POPEN_OPTIONS = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {"start_new_session": True}
REPORT_NAME = "probe_report.txt"
CREDENTIAL_SERVICE = "WebHandler/probe"

BASE_DIR = Path(__file__).resolve().parent
SITE_DIR = BASE_DIR / "runtime" / "site"
WHEELS_DIR = BASE_DIR / "offline" / "wheels"
REQUIREMENTS_FILE = BASE_DIR / "offline" / "requirements.txt"

PLAYWRIGHT_CHECKS = ("install", "imports", "browser", "recording")

DECISION_MESSAGES = {
    "ALL_PASS": "Môi trường đạt. Web Handler sẽ dùng Playwright như thiết kế.",
    "REBUILD_FOR_SYSTEM_PYTHON": (
        "Python portable bị chặn. Cần tạo lại gói theo Python có sẵn của máy, rồi chạy lại."
    ),
    "PLAYWRIGHT_BLOCKED": (
        "Playwright không chạy được đầy đủ. Cần quyết định: dùng Selenium và làm chế độ "
        "ghi khó hơn, hay dùng Selenium và hoãn chế độ ghi."
    ),
    "NEEDS_REVIEW": "Có hạng mục chưa đạt. Cần xem chi tiết bên dưới trước khi làm tiếp.",
}


@dataclass
class CheckResult:
    id: str
    title: str
    status: str
    detail: str = ""


@dataclass
class Check:
    id: str
    title: str
    func: object
    requires: tuple = ()


@dataclass
class ProcessResult:
    returncode: object
    output: str
    timed_out: bool = False


def default_replacements():
    values = {
        os.environ.get("USERPROFILE", ""): "<HOME>",
        str(Path.home()): "<HOME>",
        str(BASE_DIR): "<THƯ MỤC GÓI>",
        os.environ.get("COMPUTERNAME", ""): "<TÊN MÁY>",
        os.environ.get("USERNAME", ""): "<NGƯỜI DÙNG>",
    }
    return values


def sanitize(text, replacements=None):
    """Remove user, machine and path names, whatever the case or slash style."""
    replacements = default_replacements() if replacements is None else replacements
    for value in sorted(filter(None, replacements), key=len, reverse=True):
        escaped = "".join(r"[\\/]" if char in "\\/" else re.escape(char) for char in value)
        pattern = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
        text = re.sub(pattern, lambda _: replacements[value], text, flags=re.IGNORECASE)
    return text


def shorten(text):
    text = text.strip()
    if len(text) <= MAX_DETAIL:
        return text
    # The end of an error output is where the cause usually is.
    return "...\n" + text[-MAX_DETAIL:]


def safe_print(text, stream=None):
    stream = sys.stdout if stream is None else stream
    encoding = getattr(stream, "encoding", None) or "utf-8"
    stream.write(text.encode(encoding, "replace").decode(encoding) + "\n")
    stream.flush()


def run_checks(checks, on_start=None, on_result=None):
    results = []
    statuses = {}
    for check in checks:
        if on_start:
            on_start(check)
        blocked = [name for name in check.requires if statuses.get(name) not in ("PASS", "INFO")]
        if blocked:
            status = "SKIP"
            detail = "Bỏ qua vì chưa đạt: " + ", ".join(blocked)
        else:
            try:
                status, detail = check.func()
            except Exception as error:
                status = "FAIL"
                detail = f"{type(error).__name__}: {error}"
        item = CheckResult(check.id, check.title, status, sanitize(shorten(detail)))
        statuses[check.id] = status
        results.append(item)
        if on_result:
            on_result(item)
    return results


def decide(results):
    statuses = {item.id: item.status for item in results}
    if statuses.get("portable_python") == "FAIL":
        code = "REBUILD_FOR_SYSTEM_PYTHON"
    elif any(statuses.get(name) == "FAIL" for name in PLAYWRIGHT_CHECKS):
        code = "PLAYWRIGHT_BLOCKED"
    elif any(status in ("FAIL", "SKIP") for status in statuses.values()):
        code = "NEEDS_REVIEW"
    else:
        code = "ALL_PASS"
    return code, DECISION_MESSAGES[code]


def render_report(results, decision, meta):
    code, message = decision
    lines = ["WEB HANDLER - BÁO CÁO KIỂM TRA MÔI TRƯỜNG", ""]
    lines += [f"KẾT LUẬN: {code}", message, ""]
    lines += [f"{key}: {value}" for key, value in meta.items()]
    lines += ["", "CHI TIẾT"]
    for item in results:
        lines.append(f"[{item.status}] {item.id} - {item.title}")
        for row in item.detail.splitlines():
            lines.append(f"    {row}")
    return sanitize("\n".join(lines) + "\n")


def write_report(text, path):
    path = Path(path)
    try:
        path.write_text(text, encoding="utf-8-sig")
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / path.name
        fallback.write_text(text, encoding="utf-8-sig")
        return fallback


def child_env(site):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if site:
        env["PYTHONPATH"] = str(site)
    return env


def run_with_timeout(cmd, timeout, env=None):
    """Run a command, killing its whole process tree when time runs out.

    Output goes to a file, not a pipe: a surviving grandchild holding a pipe
    open would otherwise block the read after the child itself has been killed.
    """
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=child_env(None) if env is None else env,
                **POPEN_OPTIONS,
            )
        except OSError as error:
            return ProcessResult(None, str(error))

        timed_out = False
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if WINDOWS:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    capture_output=True,
                    **POPEN_OPTIONS,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()

        output.seek(0)
        text = output.read().decode("utf-8", "replace")
    return ProcessResult(process.returncode, text, timed_out)


def parse_child_output(output):
    for line in reversed(output.splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                return json.loads(line[len(RESULT_MARKER):])
            except ValueError:
                return None
    return None


# Checks that need nothing but the standard library.

def check_portable_python(runner):
    version = platform.python_version()
    if runner == "portable" and sys.version_info[:2] == EXPECTED_PYTHON:
        return "PASS", f"Python {version}"
    return "FAIL", f"Python portable không chạy được. Đang dùng Python của máy: {version}"


def check_system_python():
    code = (
        "import platform\n"
        "from importlib.metadata import PackageNotFoundError, version\n"
        "try:\n"
        "    selenium = version('selenium')\n"
        "except PackageNotFoundError:\n"
        "    selenium = 'không có'\n"
        "print(f'Python {platform.python_version()}, Selenium {selenium}')\n"
    )
    found = []
    names = (("py", ["-3"]), ("python", [])) if WINDOWS else (("python3", []),)
    for name, arguments in names:
        # Resolve through PATH first: CreateProcess also searches the folder of
        # the running interpreter, which would report the bundled Python here.
        program = shutil.which(name)
        if program is None:
            continue
        outcome = run_with_timeout([program, *arguments, "-c", code], 30)
        if outcome.returncode == 0:
            found.append(f"{name}: {outcome.output.strip()}")
    return "INFO", "\n".join(found) or "Không tìm thấy Python của máy."


def check_write():
    with tempfile.NamedTemporaryFile(dir=BASE_DIR, prefix="probe_write_"):
        pass
    return "PASS", "Ghi được vào thư mục gói."


class ProbeSite:
    """A local page with an iframe, for the server and browser checks."""

    PAGES = {
        "/": (
            "<!doctype html><meta charset='utf-8'><title>Web Handler Probe</title>"
            "<input id='text-input'><button id='main-button'>Bấm</button>"
            "<iframe id='inner' src='/frame'></iframe>"
        ),
        "/frame": (
            "<!doctype html><meta charset='utf-8'><button id='frame-button'>Trong khung</button>"
        ),
    }

    def __enter__(self):
        pages = self.PAGES

        class Handler(BaseHTTPRequestHandler):
            # Headers and body must leave in one write. Sent separately, about 1 in
            # 200 responses lost the body on a machine with a network filter.
            wbufsize = 64 * 1024

            def do_GET(self):
                page = pages.get(self.path)
                body = (page or "").encode("utf-8")
                self.send_response(200 if page else 404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


def fetch_with_retry(opener, url, attempts, timeout):
    """Return (body, retries). Raise the last error when every attempt fails."""
    for attempt in range(attempts):
        try:
            with opener.open(url, timeout=timeout) as response:
                return response.read(), attempt
        except (OSError, http.client.HTTPException):
            if attempt == attempts - 1:
                raise


def check_local_server():
    retries = 0
    with ProbeSite() as site:
        # A company proxy must not be involved in reaching our own server.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(SERVER_ROUNDS):
            body, used = fetch_with_retry(opener, site.url, 3, 5)
            retries += used
            if b"main-button" not in body:
                return "FAIL", "Trang cục bộ trả về nội dung không đúng."
    detail = f"Mở được server trên 127.0.0.1: {SERVER_ROUNDS} yêu cầu, thử lại {retries} lần."
    if retries:
        detail += " Mạng cục bộ không ổn định, có thể do phần mềm lọc mạng."
    return "PASS", detail


def check_file_dialog():
    if not WINDOWS:
        outcome = run_with_timeout(["/usr/bin/osascript", "-e", "return 1"], 30)
        if outcome.returncode != 0:
            return "FAIL", outcome.output or "Không chạy được AppleScript (osascript)."
        return "PASS", "Chạy được AppleScript, dùng cho hộp thoại chọn file của macOS."
    import ctypes

    ctypes.windll.comdlg32.GetOpenFileNameW
    return "PASS", "Có hàm hộp thoại chọn file của Windows."


def find_chrome():
    if not WINDOWS:
        for folder in (Path("/"), Path.home()):
            path = folder / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
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


def check_chrome():
    path = find_chrome()
    if path is None:
        return "FAIL", "Không tìm thấy Chrome."
    return "PASS", f"Tìm thấy Chrome: {path}"


def check_install():
    if not WHEELS_DIR.is_dir() or not REQUIREMENTS_FILE.is_file():
        return "FAIL", "Thiếu thư mục offline/wheels hoặc offline/requirements.txt."
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    outcome = run_with_timeout(
        [
            sys.executable, "-m", "pip", "install", "--isolated", "--no-index",
            "--no-input", "--disable-pip-version-check", "--no-warn-script-location",
            "--upgrade", "--find-links", str(WHEELS_DIR), "--target", str(SITE_DIR),
            "-r", str(REQUIREMENTS_FILE),
        ],
        300,
    )
    if outcome.timed_out:
        return "FAIL", "Cài thư viện quá thời gian.\n" + outcome.output
    if outcome.returncode != 0:
        return "FAIL", outcome.output or "Không chạy được pip."
    return "PASS", "Đã cài thư viện từ gói có sẵn, không dùng mạng."


def child_check(name, timeout):
    def run():
        outcome = run_with_timeout(
            [sys.executable, str(BASE_DIR / "probe.py"), "--child", name, "--site", str(SITE_DIR)],
            timeout,
            child_env(SITE_DIR),
        )
        if outcome.timed_out:
            return "FAIL", f"Quá {timeout} giây mà chưa xong, có thể bị chặn.\n{outcome.output}"
        parsed = parse_child_output(outcome.output)
        if parsed is None:
            return "FAIL", outcome.output or "Tiến trình con không trả kết quả."
        return ("PASS" if parsed["ok"] else "FAIL"), parsed["detail"]

    return run


def build_checks(runner):
    return [
        Check("portable_python", "Python portable", lambda: check_portable_python(runner)),
        Check("system_python", "Python có sẵn của máy (chỉ để biết)", check_system_python),
        Check("write", "Quyền ghi vào thư mục gói", check_write),
        Check("local_server", "Server cục bộ", check_local_server),
        Check("file_dialog", "Hộp thoại chọn file", check_file_dialog),
        Check("chrome_found", "Tìm Chrome", check_chrome),
        Check("install", "Cài thư viện không cần mạng", check_install),
        Check("imports", "Nạp thư viện", child_check("imports", 120), ("install",)),
        Check(
            "browser", "Điều khiển Chrome bằng Playwright",
            child_check("browser", 180), ("imports", "chrome_found"),
        ),
        Check(
            "recording", "Chèn script và nhận click (chế độ ghi)",
            child_check("recording", 180), ("browser",),
        ),
        Check("excel", "Đọc và ghi Excel", child_check("excel", 60), ("imports",)),
        Check(
            "credentials", "Windows Credential Manager" if WINDOWS else "macOS Keychain",
            child_check("credentials", 60), ("imports",),
        ),
    ]


# Checks that run in a child process, with the libraries on the path.

def child_imports():
    from importlib.metadata import version

    from bs4 import BeautifulSoup
    import keyring  # noqa: F401
    import openpyxl  # noqa: F401
    import playwright.sync_api  # noqa: F401

    if not BeautifulSoup("<input id='x'>", "html.parser").select("#x"):
        raise RuntimeError("BeautifulSoup không tìm được phần tử thử.")
    names = ("playwright", "openpyxl", "beautifulsoup4", "keyring")
    return ", ".join(f"{name} {version(name)}" for name in names)


def child_browser():
    from playwright.sync_api import sync_playwright

    with ProbeSite() as site, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        try:
            page = browser.new_page()
            page.goto(site.url)
            if page.title() != "Web Handler Probe":
                raise RuntimeError(f"Tiêu đề trang không đúng: {page.title()!r}")
            page.fill("#text-input", "Xin chào")
            if page.input_value("#text-input") != "Xin chào":
                raise RuntimeError("Giá trị đã điền không khớp.")
            return f"Chrome {browser.version}: mở trang, điền và đọc lại được."
        finally:
            browser.close()


def child_recording():
    from playwright.sync_api import sync_playwright

    received = []
    script = (
        "document.addEventListener('click', event => "
        "window.__webHandlerProbe(event.target.id), true);"
    )
    with ProbeSite() as site, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        try:
            context = browser.new_context()
            context.expose_binding(
                "__webHandlerProbe", lambda source, target: received.append(target)
            )
            context.add_init_script(script)
            page = context.new_page()
            page.goto(site.url)
            page.click("#main-button")
            page.frame_locator("#inner").locator("#frame-button").click()
            for _ in range(50):
                if len(received) >= 2:
                    break
                page.wait_for_timeout(100)
        finally:
            browser.close()
    if set(received) != {"main-button", "frame-button"}:
        raise RuntimeError(f"Nhận được click không đủ: {received!r}")
    return "Nhận được click ở trang chính và trong iframe."


def child_excel():
    from openpyxl import Workbook, load_workbook

    link = "https://example.test/item/1"
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "source.xlsx"
        result = Path(folder) / "result.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet["A2"] = "Mục 1"
        sheet["A2"].hyperlink = link
        sheet["C2"] = "Tên mới"
        workbook.save(source)
        workbook.close()

        workbook = load_workbook(source, data_only=True)
        sheet = workbook.active
        if sheet["A2"].hyperlink.target != link or sheet["C2"].value != "Tên mới":
            raise RuntimeError("Dữ liệu đọc lại không khớp.")
        sheet["D2"] = "OK"
        workbook.save(result)
        workbook.close()

        workbook = load_workbook(result, data_only=True)
        status = workbook.active["D2"].value
        workbook.close()
        if status != "OK":
            raise RuntimeError("Trạng thái ghi ra file kết quả không khớp.")
    return "Đọc hyperlink, đọc dữ liệu và ghi ra file bản sao được."


def child_credentials():
    import keyring

    account = "probe"
    value = secrets.token_urlsafe(16)
    backend = type(keyring.get_keyring()).__name__
    try:
        keyring.set_password(CREDENTIAL_SERVICE, account, value)
        if keyring.get_password(CREDENTIAL_SERVICE, account) != value:
            raise RuntimeError("Giá trị đọc lại không khớp.")
    finally:
        try:
            keyring.delete_password(CREDENTIAL_SERVICE, account)
        except keyring.errors.PasswordDeleteError:
            pass
    if keyring.get_password(CREDENTIAL_SERVICE, account) is not None:
        raise RuntimeError("Không xóa được khóa thử.")
    return f"Ghi, đọc và xóa khóa thử được ({backend})."


CHILD_CHECKS = {
    "imports": child_imports,
    "browser": child_browser,
    "recording": child_recording,
    "excel": child_excel,
    "credentials": child_credentials,
}


def run_child(name):
    try:
        payload = {"ok": True, "detail": CHILD_CHECKS[name]()}
    except Exception as error:
        tail = traceback.format_exc(limit=2)
        payload = {"ok": False, "detail": f"{type(error).__name__}: {error}\n{tail}"}
    print(RESULT_MARKER + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0 if payload["ok"] else 1


def report_meta(runner):
    return {
        "Thời gian": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Bản probe": PROBE_VERSION,
        "Hệ điều hành": platform.platform(),
        "Kiến trúc": platform.machine(),
        "Python đang chạy probe": platform.python_version(),
        "Cách chạy": runner,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Web Handler environment probe")
    parser.add_argument("--runner", choices=("portable", "system"), default="system")
    parser.add_argument("--child", choices=sorted(CHILD_CHECKS))
    parser.add_argument("--site")
    arguments = parser.parse_args(argv)

    if arguments.child:
        return run_child(arguments.child)

    safe_print("Web Handler - kiểm tra môi trường. Có thể mất vài phút; Chrome sẽ tự mở rồi đóng.\n")
    results = run_checks(
        build_checks(arguments.runner),
        on_start=lambda check: safe_print(f"... {check.title}"),
        on_result=lambda item: safe_print(f"    [{item.status}] {item.detail.splitlines()[0] if item.detail else ''}"),
    )
    decision = decide(results)
    text = render_report(results, decision, report_meta(arguments.runner))
    path = write_report(text, BASE_DIR / REPORT_NAME)
    safe_print(f"\nKẾT LUẬN: {decision[0]}\n{decision[1]}")
    safe_print(f"\nBáo cáo: {path}\nHãy gửi lại nội dung file báo cáo này.")
    return 0 if decision[0] == "ALL_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
