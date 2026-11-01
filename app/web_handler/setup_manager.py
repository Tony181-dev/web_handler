"""The Setup screen: what is present, what is missing, and installing it on request."""
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile

from . import paths, setup_checks
from .workers import WorkerSession

LIBRARY_NAMES = ("playwright", "openpyxl", "beautifulsoup4", "keyring")
SELF_TESTS = {
    "browser_control": ("browser", "Điều khiển trình duyệt"),
    "recording": ("recording", "Ghi thao tác"),
    "credentials": ("credentials", "Windows Credential Manager"),
}
SELF_TEST_TIMEOUT = 180


class SetupManager:
    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.busy = None
        self.message = ""
        self.self_tests = {}

    # Fast checks

    def check_python(self):
        version = platform.python_version()
        if setup_checks.is_bundled_python():
            return {"status": "PASS", "detail": f"Python {version} riêng của app."}
        return {"status": "PASS", "detail": f"Python {version} của máy, vì bản riêng của app không chạy được hoặc chưa được giải nén."}

    def check_libraries(self):
        code = (
            "from importlib.metadata import version\n"
            f"for name in {LIBRARY_NAMES!r}:\n"
            "    print(name, version(name))\n"
            "import playwright.sync_api, bs4, openpyxl, keyring\n"
        )
        returncode, output, _ = setup_checks.run_with_timeout([sys.executable, "-c", code], 90)
        if returncode == 0:
            return {"status": "PASS", "detail": ", ".join(output.split("\n")[:4]).replace("\r", "").strip(", ")}
        detail = "Chưa cài thư viện."
        if paths.SITE_DIR.is_dir() and any(paths.SITE_DIR.iterdir()):
            detail = "Thư viện cài chưa đủ hoặc bị lỗi: " + output.strip().splitlines()[-1][:200] if output.strip() else "Thư viện bị lỗi."
        fixable = paths.WHEELS_DIR.is_dir()
        return {"status": "FAIL", "detail": detail, "fixable": fixable, "size_mb": _folder_size_mb(paths.WHEELS_DIR)}

    def check_browser(self):
        chrome = setup_checks.find_chrome()
        if chrome:
            return {"status": "PASS", "detail": "Tìm thấy Chrome đã cài trên máy."}
        chromium = setup_checks.find_bundled_chromium()
        if chromium:
            return {"status": "PASS", "detail": "Không có Chrome; đang dùng Chromium riêng của app (không phải Chrome chính hãng)."}
        fixable = paths.CHROMIUM_ZIP.is_file()
        return {
            "status": "FAIL",
            "detail": "Không tìm thấy Chrome." if fixable else "Không tìm thấy Chrome và gói này không kèm Chromium.",
            "fixable": fixable,
            "size_mb": round(paths.CHROMIUM_ZIP.stat().st_size / 1048576) if fixable else 0,
        }

    def check_write(self):
        try:
            paths.data_dir().mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=paths.data_dir(), prefix="write_test_"):
                pass
            return {"status": "PASS", "detail": "Ghi được vào thư mục data."}
        except OSError as error:
            return {"status": "FAIL", "detail": f"Không ghi được vào thư mục data: {error}"}

    def status(self):
        items = [
            {"id": "python", "title": "Python riêng của app", **self.check_python()},
            {"id": "libraries", "title": "Thư viện", **self.check_libraries()},
            {"id": "browser", "title": "Trình duyệt", **self.check_browser()},
            {"id": "write", "title": "Quyền ghi", **self.check_write()},
        ]
        for key, (_, title) in SELF_TESTS.items():
            result = self.self_tests.get(key) or {"status": "NOT_RUN", "detail": "Chưa thử."}
            items.append({"id": key, "title": title, **result})
        return {"items": items, "busy": self.busy, "message": self.message, "ready": self.ready(items)}

    @staticmethod
    def ready(items):
        needed = {"libraries", "browser", "write"}
        return all(item["status"] == "PASS" for item in items if item["id"] in needed)

    # Long work runs on a thread; the UI polls status().

    def _start(self, label, work):
        with self.lock:
            if self.busy:
                return False
            self.busy = label
            self.message = label
        self.log.add("info", f"Setup: {label}")

        def run():
            try:
                work()
            except Exception as error:
                self.message = f"Lỗi: {error}"
                self.log.add("error", f"Setup: {error}")
            else:
                self.message = "Đã xong."
                results = ", ".join(f"{SELF_TESTS[key][1]}: {item['status']}" for key, item in self.self_tests.items())
                self.log.add("info", f"Setup xong. {results}".strip())
            finally:
                self.busy = None

        threading.Thread(target=run, daemon=True).start()
        return True

    def start_install(self):
        return self._start("Đang cài...", self._install)

    def start_self_tests(self):
        return self._start("Đang kiểm tra...", self._run_self_tests)

    def _install(self):
        self.install_missing()
        if self.check_libraries()["status"] == "PASS":
            self._run_self_tests()

    def install_missing(self):
        """Returns whether the libraries work afterwards. The installer calls this directly."""
        if self.check_libraries()["status"] != "PASS":
            self._install_libraries()
        if self.check_browser()["status"] != "PASS":
            if paths.CHROMIUM_ZIP.is_file():
                self.message = "Đang giải nén Chromium..."
                self._install_chromium()
        return self.check_libraries()["status"] == "PASS"

    def _install_libraries(self):
        if not paths.WHEELS_DIR.is_dir() or not paths.REQUIREMENTS_FILE.is_file():
            raise RuntimeError("Gói cài thiếu thư mục offline/wheels.")
        self.message = "Đang cài thư viện..."
        paths.SITE_DIR.mkdir(parents=True, exist_ok=True)
        returncode, output, timed_out = setup_checks.run_with_timeout(
            [
                sys.executable, "-m", "pip", "install", "--isolated", "--no-index",
                "--no-input", "--disable-pip-version-check", "--no-warn-script-location",
                "--upgrade", "--find-links", str(paths.WHEELS_DIR), "--target", str(paths.SITE_DIR),
                "-r", str(paths.REQUIREMENTS_FILE),
            ],
            600,
        )
        if timed_out:
            raise RuntimeError("Cài thư viện quá thời gian.")
        if returncode != 0:
            tail = output.strip().splitlines()[-1] if output.strip() else "không rõ"
            raise RuntimeError(f"Không cài được thư viện: {tail}")
        paths.activate_site()

    def _install_chromium(self):
        if paths.CHROMIUM_DIR.exists():
            shutil.rmtree(paths.CHROMIUM_DIR, ignore_errors=True)
        with zipfile.ZipFile(paths.CHROMIUM_ZIP) as bundle:
            bundle.extractall(paths.CHROMIUM_DIR)

    def _run_self_tests(self):
        for key, (test, title) in SELF_TESTS.items():
            self.message = f"Đang thử: {title}..."
            self.self_tests[key] = self._self_test(test)

    def _self_test(self, test):
        session = WorkerSession({"action": "selftest", "test": test})
        result = {"status": "FAIL", "detail": "Không nhận được kết quả."}

        def on_event(event):
            if event.get("type") == "selftest":
                result["status"] = "PASS" if event["ok"] else "FAIL"
                result["detail"] = event["detail"]
            elif event.get("type") == "finished" and event.get("outcome") == "failed":
                result["detail"] = event.get("message") or result["detail"]

        session.on_event = on_event
        session.start()
        if not session.wait(SELF_TEST_TIMEOUT):
            session.kill()
            return {"status": "FAIL", "detail": f"Quá {SELF_TEST_TIMEOUT} giây, có thể bị chặn."}
        return result


def _folder_size_mb(folder):
    if not folder.is_dir():
        return 0
    return round(sum(path.stat().st_size for path in folder.iterdir() if path.is_file()) / 1048576)
