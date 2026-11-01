"""Start and talk to worker processes from the server."""
import json
import subprocess
import sys
import threading

from . import child_processes, paths

EVENT_PREFIX = "@@"


class WorkerSession:
    def __init__(self, job):
        self.job = job
        self.process = None
        self.on_event = None
        self.on_exit = None
        self.finished = threading.Event()
        self.output_tail = []

    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "web_handler.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=paths.child_env(), cwd=str(paths.ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        # Before the job is sent, so whatever the worker starts is covered too.
        child_processes.adopt(self.process)
        self.send(self.job)
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, payload):
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _read(self):
        for line in self.process.stdout:
            line = line.rstrip("\r\n")
            if line.startswith(EVENT_PREFIX):
                try:
                    event = json.loads(line[len(EVENT_PREFIX):])
                except ValueError:
                    continue
                if self.on_event:
                    self.on_event(event)
            elif line.strip():
                self.output_tail = (self.output_tail + [line])[-20:]
        self.process.wait()
        if self.on_exit:
            self.on_exit(self.process.returncode)
        self.finished.set()

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def wait(self, timeout=None):
        return self.finished.wait(timeout)

    def kill(self):
        if self.process is None:
            return
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
