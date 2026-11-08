"""Closing the app must leave nothing behind, however it is closed, so the next start always works."""
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "runtime" / "site"))

from web_handler import paths  # noqa: E402

WINDOWS = sys.platform == "win32"
if WINDOWS:
    from ctypes import wintypes

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WM_CLOSE = 0x0010
LONG_WAIT_SECONDS = 600
GONE_WITHIN_SECONDS = 15
# The system reuses process numbers, so a number alone can point at someone else's process.
OURS = {"python.exe", "node.exe", "chrome.exe", "chrome_crashpad_handler.exe", "conhost.exe", "cmd.exe"}
# On macOS the browser's helpers are named after it, such as "Google Chrome Helper (Renderer)".
MAC_OURS = ("python", "node", "Google Chrome", "Chromium", "chrome_crashpad_handler", "bash", "sh")
BROWSERS = {"chrome.exe"} if WINDOWS else {"Google Chrome", "Google Chrome for Testing", "Chromium"}
PS_LINE = re.compile(r"\s*(\d+)\s+(\d+)\s+(\w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4})\s(.*)")


def is_ours(name):
    return name.lower() in OURS if WINDOWS else name.startswith(MAC_OURS)


def kill(pid):
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def process_table():
    """Every process on the machine, as {pid: (parent pid, name, start time)}."""
    if not WINDOWS:
        output = subprocess.run(
            ["/bin/ps", "-A", "-o", "pid=,ppid=,lstart=,comm="],
            capture_output=True, text=True, check=True, env={**os.environ, "LC_ALL": "C"},
        ).stdout
        table = {}
        for line in output.splitlines():
            match = PS_LINE.match(line)
            # A process that has ended but not been waited for yet is gone, as on Windows.
            if match and match[4].strip() != "<defunct>":
                started = int(time.mktime(time.strptime(match[3], "%a %b %d %H:%M:%S %Y")))
                table[int(match[1])] = (int(match[2]), Path(match[4].strip()).name, started)
        return table
    output = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1} {2} {3}' -f $_.ProcessId, $_.ParentProcessId, $_.CreationDate.Ticks, $_.Name }"],
        capture_output=True, text=True, check=True,
    ).stdout
    table = {}
    for line in output.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4:
            table[int(parts[0])] = (int(parts[1]), parts[3], int(parts[2]))
    return table


def descendants(root):
    """Processes started by this one. One older than its supposed parent only shares a reused number."""
    table = process_table()
    found = set()
    frontier = {root}
    while frontier:
        frontier = {
            pid for pid, (parent, _, started) in table.items()
            if parent in frontier and parent in table and started >= table[parent][2]
        } - found
        found |= frontier
    return {pid: table[pid][1] for pid in found}


def find_window(title_prefix):
    found = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(window, _):
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(window, buffer, 512)
        if buffer.value.startswith(title_prefix):
            found.append(window)
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="wh_shutdown_test_")
        self.addCleanup(shutil.rmtree, self.data, True)
        self.env = {**paths.child_env(), "WEB_HANDLER_DATA": self.data, "WEB_HANDLER_HEADLESS": "1"}
        self.started = []
        self.watched = {}
        self.addCleanup(self.kill_everything)

    def kill_everything(self):
        # Only what these tests started: still running, with the same name, and of a kind they start.
        for process in self.started:
            if process.poll() is None:
                process.kill()
        table = process_table()
        for pid, name in self.watched.items():
            if pid in table and table[pid][1] == name and is_ours(name):
                kill(pid)

    def main_command(self):
        return [sys.executable, str(ROOT / "app" / "main.py"), "--no-browser"]

    # Away from the console or Terminal these tests run in, so that only the tests close the app.
    GROUP = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}

    def start_app(self):
        process = self.start_app_without_waiting()
        self.wait_until_serving()
        return process

    def start_app_without_waiting(self):
        process = subprocess.Popen(
            self.main_command(), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **self.GROUP,
        )
        self.started.append(process)
        return process

    def open_console(self):
        """The app in a window of its own, as Start.bat or Start.command opens it."""
        if not WINDOWS:
            return self.start_app_without_waiting(), None
        title = f"WH_TEST_{uuid.uuid4().hex[:8]}"
        command = subprocess.list2cmdline(self.main_command())
        console = subprocess.Popen(
            f'cmd /c "title {title} & {command}"', env=self.env, creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        self.started.append(console)
        return console, title

    def close_console(self, console, title):
        """Close the window. On macOS the Terminal hangs up on what runs in it, as this does."""
        if not WINDOWS:
            os.killpg(console.pid, signal.SIGHUP)
            return
        window = None
        deadline = time.monotonic() + 10
        while window is None and time.monotonic() < deadline:
            window = find_window(title)
            time.sleep(0.2)
        self.assertIsNotNone(window, "Console window not found")
        ctypes.windll.user32.PostMessageW(window, WM_CLOSE, 0, 0)

    def wait_until_serving(self):
        deadline = time.monotonic() + 30
        path = Path(self.data) / "server.json"
        while time.monotonic() < deadline:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.base = f"http://127.0.0.1:{saved['port']}"
                self.key = saved["key"]
                self.api("GET", "/api/ping")
                return saved
            except Exception:
                time.sleep(0.2)
        self.fail("The app did not start.")

    def api(self, method, path, body=None):
        request = urllib.request.Request(
            f"{self.base}{path}?key={self.key}", method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with NO_PROXY.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def start_long_run(self):
        """A run sitting in a long Wait, with its browser open."""
        created = self.api("POST", "/api/flows", {"name": "Dài"})
        flow = created["flow"]
        flow["steps"] = [
            {"id": "s1", "do": "goto", "url": f"{self.base}/demo/login"},
            {"id": "s2", "do": "wait", "seconds": LONG_WAIT_SECONDS},
        ]
        self.api("PUT", f"/api/flows/{created['id']}", flow)
        self.api("POST", "/api/runs", {"flow_id": created["id"], "mode": "full"})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            # At the Wait block, past starting the browser and opening the page.
            if self.browser_is_open() and any("Wait" in line["text"] for line in self.api("GET", "/api/log")["lines"]):
                return
            time.sleep(0.5)
        self.fail("The browser did not open.")

    def browser_is_open(self):
        return any(name.lower() in BROWSERS or name in BROWSERS for name in descendants(self.main_pid).values())

    def assert_all_gone(self, pids):
        self.watched.update(pids)
        deadline = time.monotonic() + GONE_WITHIN_SECONDS
        left = {}
        while time.monotonic() < deadline:
            table = process_table()
            left = {pid: table[pid][1] for pid in pids if pid in table and table[pid][1] == pids[pid]}
            if not left:
                return
            time.sleep(0.5)
        self.fail(f"Still running after {GONE_WITHIN_SECONDS} seconds: {left}")

    def assert_next_start_works(self):
        """The next start serves again, and can open the browser on the same profile."""
        self.start_app()
        self.assert_next_run_works()

    def assert_next_run_works(self):
        created = self.api("POST", "/api/flows", {"name": "Lần sau"})
        flow = created["flow"]
        flow["steps"] = [{"id": "s1", "do": "goto", "url": f"{self.base}/demo/login"}]
        self.api("PUT", f"/api/flows/{created['id']}", flow)
        run_id = self.api("POST", "/api/runs", {"flow_id": created["id"], "mode": "full"})["run_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = self.api("GET", f"/api/runs/{run_id}")
            if status.get("outcome"):
                self.assertEqual(status["outcome"], "finished", status)
                return
            time.sleep(0.5)
        self.fail("The run after restarting did not finish.")

    def test_force_killed_app_takes_its_run_and_browser_with_it(self):
        app = self.start_app()
        self.main_pid = app.pid
        self.start_long_run()
        children = descendants(app.pid)
        self.assertTrue(children)
        kill(app.pid)
        self.assert_all_gone(children)
        self.assert_next_start_works()

    def test_closing_the_console_window_takes_everything_with_it(self):
        console, title = self.open_console()
        self.wait_until_serving()
        self.main_pid = console.pid
        self.start_long_run()
        children = descendants(console.pid)
        self.close_console(console, title)
        self.assert_all_gone(children)
        self.assert_next_start_works()

    def test_closing_the_window_during_a_wait_saves_the_result_file(self):
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append(["Cột A"])
        workbook.active.append(["giá trị"])
        source = Path(self.data) / "input.xlsx"
        workbook.save(source)
        workbook.close()

        console, title = self.open_console()
        self.wait_until_serving()
        self.main_pid = console.pid
        created = self.api("POST", "/api/flows", {"name": "Excel"})
        flow = created["flow"]
        flow["excel"] = {"sheet": workbook.active.title, "start_row": 2, "end_row": None, "required": [], "status_column": "B"}
        flow["steps"] = [{
            "id": "s1", "do": "loop", "source": "excel", "count": 1,
            "steps": [{"id": "s2", "do": "wait", "seconds": LONG_WAIT_SECONDS}],
        }]
        self.api("PUT", f"/api/flows/{created['id']}", flow)
        self.api("POST", "/api/runs", {"flow_id": created["id"], "excel_path": str(source), "mode": "full"})
        deadline = time.monotonic() + 90
        while not self.browser_is_open() and time.monotonic() < deadline:
            time.sleep(0.5)
        time.sleep(2)
        children = descendants(console.pid)
        self.close_console(console, title)
        self.assert_all_gone(children)
        self.assertEqual(len(list(Path(self.data).glob("input_result_*.xlsx"))), 1)

    def active_job(self):
        return self.api("GET", "/api/active").get("job")

    def wait_for_no_active_job(self, seconds=30):
        deadline = time.monotonic() + seconds
        while self.active_job() and time.monotonic() < deadline:
            time.sleep(0.5)
        self.assertIsNone(self.active_job())

    def test_closing_the_run_browser_ends_the_run_and_frees_the_next_one(self):
        app = self.start_app()
        self.main_pid = app.pid
        self.start_long_run()
        self.assertEqual(self.active_job()["kind"], "run")
        children = descendants(app.pid)
        self.watched.update(children)
        for pid, name in children.items():
            if name.lower() in BROWSERS or name in BROWSERS:
                kill(pid)
        self.wait_for_no_active_job()
        self.assert_next_run_works()

    def test_stop_now_ends_a_run_that_nobody_can_reach(self):
        app = self.start_app()
        self.main_pid = app.pid
        self.start_long_run()
        job = self.active_job()
        children = descendants(app.pid)
        self.watched.update(children)
        self.api("POST", "/api/active/stop", {})
        self.assertIsNone(self.active_job())
        self.assert_all_gone(children)
        self.assertEqual(self.api("GET", f"/api/runs/{job['run_id']}")["outcome"], "stopped")
        self.assert_next_run_works()

    def finish_a_kept_run(self):
        self.api("PUT", "/api/keep-browser", {"keep": True})
        created = self.api("POST", "/api/flows", {"name": "Giữ"})
        flow = created["flow"]
        flow["steps"] = [{"id": "s1", "do": "goto", "url": f"{self.base}/demo/login"}]
        self.api("PUT", f"/api/flows/{created['id']}", flow)
        run_id = self.api("POST", "/api/runs", {"flow_id": created["id"], "mode": "full"})["run_id"]
        deadline = time.monotonic() + 90
        while not self.api("GET", f"/api/runs/{run_id}").get("outcome") and time.monotonic() < deadline:
            time.sleep(0.5)
        return run_id

    def test_a_kept_browser_outlasts_the_run_and_the_next_run_closes_it(self):
        app = self.start_app()
        self.main_pid = app.pid
        self.finish_a_kept_run()
        # Well past the 10 seconds after which a finished run's worker is ended.
        time.sleep(12)
        answer = self.api("GET", "/api/active")
        self.assertIsNone(answer["job"], "A kept browser must not block the next run")
        self.assertEqual(answer["browser"], "kept")
        self.assertTrue(self.browser_is_open())
        self.api("PUT", "/api/keep-browser", {"keep": False})
        self.assert_next_run_works()
        lines = [line["text"] for line in self.api("GET", "/api/log")["lines"]]
        self.assertTrue(any("Closed the browser kept open" in line for line in lines), lines[-5:])

    def test_close_browser_ends_a_kept_browser(self):
        app = self.start_app()
        self.main_pid = app.pid
        self.finish_a_kept_run()
        self.assertEqual(self.api("GET", "/api/active")["browser"], "kept")
        children = descendants(app.pid)
        self.watched.update(children)
        self.api("POST", "/api/active/close-browser", {})
        self.assertIsNone(self.api("GET", "/api/active")["browser"])
        self.assert_all_gone(children)

    def start_older_app(self):
        """The same app, but reporting an older version, like a window left open across an update."""
        code = (
            "import runpy, sys\n"
            f"sys.path.insert(0, {str(ROOT / 'app')!r})\n"
            "import web_handler\n"
            "web_handler.__version__ = '0.0.1'\n"
            f"sys.argv = ['main.py', '--no-browser']\n"
            f"runpy.run_path({str(ROOT / 'app' / 'main.py')!r}, run_name='__main__')\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code], env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **self.GROUP,
        )
        self.started.append(process)
        self.wait_until_serving()
        self.assertEqual(self.api("GET", "/api/ping")["version"], "0.0.1")
        return process

    def test_starting_a_newer_version_replaces_an_older_app_still_running(self):
        older = self.start_older_app()
        newer = self.start_app_without_waiting()
        deadline = time.monotonic() + 30
        while older.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertIsNotNone(older.poll(), "The older app is still running")
        self.assertIsNone(newer.poll(), "The new start gave up")
        deadline = time.monotonic() + 30
        version = None
        while time.monotonic() < deadline:
            try:
                self.wait_until_serving()
                version = self.api("GET", "/api/ping")["version"]
                if version != "0.0.1":
                    break
            except Exception:
                pass
            time.sleep(0.2)
        from web_handler import __version__

        self.assertEqual(version, __version__)
        self.assertEqual(self.api("GET", "/api/browser-mode")["mode"], "visible")

    def test_starting_the_same_version_still_reuses_the_running_app(self):
        running = self.start_app()
        second = self.start_app_without_waiting()
        self.assertEqual(second.wait(30), 0)
        self.assertIsNone(running.poll())

    def test_only_adopted_processes_die_with_the_app(self):
        """The person's own Chrome or Excel, opened from the app, must survive it closing."""
        sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
        script = "\n".join((
            "import json, subprocess, sys",
            "from web_handler import child_processes",
            f"sleeper = {sleeper!r}",
            "adopted = subprocess.Popen(sleeper)",
            "child_processes.adopt(adopted)",
            "free = subprocess.Popen(sleeper)",
            "print(json.dumps([adopted.pid, free.pid]), flush=True)",
            "sys.stdin.read()",
        ))
        parent = subprocess.Popen(
            [sys.executable, "-c", script], env=self.env, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.started.append(parent)
        line = parent.stdout.readline()
        adopted, free = json.loads(line)
        self.watched[free] = process_table()[free][1]
        name = process_table()[adopted][1]
        kill(parent.pid)
        self.assert_all_gone({adopted: name})
        self.assertIn(free, process_table())

    def test_stop_option_ends_a_running_app(self):
        app = self.start_app()
        self.main_pid = app.pid
        self.start_long_run()
        children = descendants(app.pid)
        name = process_table()[app.pid][1]
        result = subprocess.run(
            [*self.main_command()[:2], "--stop"], env=self.env, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_all_gone({app.pid: name, **children})
        # Any other code makes Start.bat stop at its pause, leaving a window open on the app folder.
        self.assertEqual(app.wait(5), 0)
        self.assert_next_start_works()

    def test_stop_option_is_fine_when_nothing_runs(self):
        result = subprocess.run(
            [*self.main_command()[:2], "--stop"], env=self.env, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_hung_app_is_replaced_on_the_next_start(self):
        """A process that no longer answers must not keep the profile or block the next start."""
        app = self.start_app()
        self.main_pid = app.pid
        self.start_long_run()
        children = descendants(app.pid)
        name = process_table()[app.pid][1]
        # Suspending the process makes it stop answering, as a hang would.
        if WINDOWS:
            kernel32 = ctypes.windll.kernel32
            PROCESS_SUSPEND_RESUME = 0x0800
            handle = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, app.pid)
            self.addCleanup(kernel32.CloseHandle, handle)
            self.assertEqual(ctypes.windll.ntdll.NtSuspendProcess(handle), 0)
        else:
            os.kill(app.pid, signal.SIGSTOP)
        self.assert_next_start_works()
        self.assert_all_gone({app.pid: name, **children})


if __name__ == "__main__":
    unittest.main()
