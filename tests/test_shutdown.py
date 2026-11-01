"""Closing the app must leave nothing behind, however it is closed, so the next start always works."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
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

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WM_CLOSE = 0x0010
LONG_WAIT_SECONDS = 600
GONE_WITHIN_SECONDS = 15


def process_table():
    """Every process on the machine, as {pid: (parent pid, name)}."""
    output = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1} {2}' -f $_.ProcessId, $_.ParentProcessId, $_.Name }"],
        capture_output=True, text=True, check=True,
    ).stdout
    table = {}
    for line in output.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) == 3:
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def descendants(root):
    table = process_table()
    found = set()
    frontier = {root}
    while frontier:
        frontier = {pid for pid, (parent, _) in table.items() if parent in frontier} - found
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
        for process in self.started:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        # A failed test leaves processes whose parent is gone, which /T can no longer reach.
        table = process_table()
        for pid, name in self.watched.items():
            if table.get(pid, (0, ""))[1] == name:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

    def main_command(self):
        return [sys.executable, str(ROOT / "app" / "main.py"), "--no-browser"]

    def start_app(self):
        process = subprocess.Popen(
            self.main_command(), env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self.started.append(process)
        self.wait_until_serving()
        return process

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
            if self.browser_is_open():
                return
            time.sleep(0.5)
        self.fail("The browser did not open.")

    def browser_is_open(self):
        return any(name.lower() == "chrome.exe" for name in descendants(self.main_pid).values())

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
        subprocess.run(["taskkill", "/F", "/PID", str(app.pid)], capture_output=True, check=True)
        self.assert_all_gone(children)
        self.assert_next_start_works()

    def test_closing_the_console_window_takes_everything_with_it(self):
        title = f"WH_TEST_{uuid.uuid4().hex[:8]}"
        command = subprocess.list2cmdline(self.main_command())
        console = subprocess.Popen(
            f'cmd /c "title {title} & {command}"', env=self.env, creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        self.started.append(console)
        self.wait_until_serving()
        self.main_pid = console.pid
        self.start_long_run()
        children = descendants(console.pid)
        window = None
        deadline = time.monotonic() + 10
        while window is None and time.monotonic() < deadline:
            window = find_window(title)
            time.sleep(0.2)
        self.assertIsNotNone(window, "Console window not found")
        ctypes.windll.user32.PostMessageW(window, WM_CLOSE, 0, 0)
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

        title = f"WH_TEST_{uuid.uuid4().hex[:8]}"
        command = subprocess.list2cmdline(self.main_command())
        console = subprocess.Popen(
            f'cmd /c "title {title} & {command}"', env=self.env, creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        self.started.append(console)
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
        ctypes.windll.user32.PostMessageW(find_window(title), WM_CLOSE, 0, 0)
        self.assert_all_gone(children)
        self.assertEqual(len(list(Path(self.data).glob("input_result_*.xlsx"))), 1)

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
        self.addCleanup(subprocess.run, ["taskkill", "/F", "/PID", str(free)], capture_output=True)
        name = Path(sys.executable).name
        subprocess.run(["taskkill", "/F", "/PID", str(parent.pid)], capture_output=True, check=True)
        self.assert_all_gone({adopted: name})
        self.assertIn(free, process_table())

    def test_stop_option_ends_a_running_app(self):
        app = self.start_app()
        self.main_pid = app.pid
        self.start_long_run()
        children = descendants(app.pid)
        result = subprocess.run(
            [*self.main_command()[:2], "--stop"], env=self.env, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_all_gone({app.pid: Path(sys.executable).name, **children})
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
        kernel32 = ctypes.windll.kernel32
        # Suspending the process makes it stop answering, as a hang would.
        PROCESS_SUSPEND_RESUME = 0x0800
        handle = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, app.pid)
        self.addCleanup(kernel32.CloseHandle, handle)
        self.assertEqual(ctypes.windll.ntdll.NtSuspendProcess(handle), 0)
        self.assert_next_start_works()
        self.assert_all_gone({app.pid: Path(sys.executable).name, **children})


if __name__ == "__main__":
    unittest.main()
