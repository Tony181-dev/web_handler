"""Everything the app starts for itself ends with it, however the app ends. The macOS side.

macOS has no Windows jobs, so each helper is followed through the process table instead:
- It starts in a session of its own, so closing the Terminal window or pressing Ctrl+C reaches only the
  app, which then ends its helpers itself, after giving a run its moment to save.
- While it runs, the processes below it are noted every second. The browser automation library starts
  its browser in a session of its own, and once a parent is gone its children move to launchd, where
  nothing ties them to the helper any more. What was noted still finds them.
- Should the app itself be killed outright, nothing in it can act any more. So each helper gets a small
  watcher process of its own, which waits for the app to be gone and then ends the helper's whole tree.
Process numbers are reused, so a process is only ever ended along with the start time it was seen with.
Only the app's own helpers are followed: a browser or Excel opened for the person must outlive the app.
"""
import ctypes
import ctypes.util
import os
import select
import signal
import subprocess
import sys
import threading
import time

PROC_ALL_PIDS = 1
PROC_PIDTBSDINFO = 3
ZOMBIE = 5
NOTE_INTERVAL_SECONDS = 1
# The watcher is woken by the exit itself. This is only in case a wake-up is missed.
WATCH_CHECK_SECONDS = 5

# The helpers get a session of their own, away from the Terminal window's signals.
POPEN_OPTIONS = {"start_new_session": True}

_libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
_libproc.proc_listpids.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int)
_libproc.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)


class _BsdInfo(ctypes.Structure):
    """struct proc_bsdinfo from <sys/proc_info.h>."""
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("exit_status", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("open_files", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("job_control", ctypes.c_uint32),
        ("tty", ctypes.c_uint32),
        ("tty_pgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    ]


def _info(pid):
    """(parent, start time) of a living process, or None once it is gone or only a zombie."""
    info = _BsdInfo()
    size = ctypes.sizeof(info)
    if _libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), size) != size:
        return None
    if info.status == ZOMBIE:
        return None
    return info.ppid, info.start_seconds * 1_000_000 + info.start_microseconds


def _all_pids():
    count = _libproc.proc_listpids(PROC_ALL_PIDS, 0, None, 0)
    while True:
        # Room for some more: processes start between the two calls.
        buffer = (ctypes.c_int * (count + 256))()
        filled = _libproc.proc_listpids(PROC_ALL_PIDS, 0, buffer, ctypes.sizeof(buffer))
        if filled <= 0:
            return []
        if filled < ctypes.sizeof(buffer):
            return [pid for pid in buffer[:filled // ctypes.sizeof(ctypes.c_int)] if pid > 0]
        count = len(buffer) * 2


def _table():
    """Every living process, as {pid: (parent pid, start time)}."""
    table = {}
    for pid in _all_pids():
        info = _info(pid)
        if info:
            table[pid] = info
    return table


def descendants(root, root_started=None):
    """{pid: start time} of the processes below root. One older than its parent only shares a reused number."""
    table = _table()
    if root not in table or (root_started is not None and table[root][1] != root_started):
        return {}
    found = {}
    frontier = {root}
    while frontier:
        frontier = {
            pid for pid, (parent, started) in table.items()
            if parent in frontier and pid not in found and started >= table[parent][1]
        }
        found.update({pid: table[pid][1] for pid in frontier})
    return found


def _signal_exact(pid, started, number):
    info = _info(pid)
    if info is None or info[1] != started:
        return
    try:
        os.kill(pid, number)
    except OSError:
        pass


class _Helper:
    """What a Windows job is to the other side: a helper and every process it started."""

    def __init__(self, pid, started):
        self.pid = pid
        self.started = started
        self.known = {}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.watcher = _start_watcher(pid, started)
        threading.Thread(target=self._note, daemon=True).start()

    def stop_watching(self):
        if self.watcher is None:
            return
        try:
            self.watcher.kill()
            self.watcher.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def note(self):
        found = descendants(self.pid, self.started)
        with self.lock:
            self.known.update(found)

    def _note(self):
        while not self.closed.wait(NOTE_INTERVAL_SECONDS):
            try:
                self.note()
            except Exception:
                pass

    def end_all(self, include_helper):
        """Stop the whole tree at once, so that none of it can start anything new, then end it."""
        self.note()
        with self.lock:
            known = dict(self.known)
        if include_helper:
            known[self.pid] = self.started
        for number in (signal.SIGSTOP, signal.SIGKILL):
            for pid, started in known.items():
                _signal_exact(pid, started, number)


def adopt(process):
    """Follow a just-started subprocess.Popen and what it starts. Returns its helper, or None if it is gone already."""
    started = started_at(process.pid)
    if started is None:
        return None
    return _Helper(process.pid, started)


def end_job(job):
    """End the helper and everything it started."""
    job.end_all(include_helper=True)


def release_job(job):
    """The helper has finished. Anything it left behind is ended too."""
    job.closed.set()
    job.stop_watching()
    job.end_all(include_helper=False)


def started_at(pid):
    """When a process started, which tells it apart from a later one given the same number."""
    info = _info(pid)
    return info[1] if info else None


def current_process():
    pid = os.getpid()
    return pid, started_at(pid)


def end_process(pid, started, timeout_seconds=10):
    """End that exact process and wait for it. Returns False only if it could not be ended.

    It is asked first, so that the app it may be ends its own helpers and lets a run save its result.
    """
    if started_at(pid) != started:
        return True
    _signal_exact(pid, started, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if started_at(pid) != started:
            return True
        time.sleep(0.1)
    _signal_exact(pid, started, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if started_at(pid) != started:
            return True
        time.sleep(0.1)
    return False


def _start_watcher(pid, started):
    """A process of its own, which outlives this one only to end the helper's tree. None if it cannot start."""
    from . import paths

    try:
        return subprocess.Popen(
            [sys.executable, "-m", "web_handler.child_processes_posix", str(pid), str(started)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=paths.child_env(), cwd="/", start_new_session=True,
        )
    except OSError:
        return None


def _watch(helper, helper_started):
    """In the watcher: wait for the app or the helper to end. Once the app is gone, end the helper's tree."""
    app = os.getppid()
    queue = select.kqueue()
    for pid in (app, helper):
        try:
            queue.control([select.kevent(pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD, select.KQ_NOTE_EXIT)], 0)
        except OSError:
            pass  # Already gone, which the checks below see.
    while True:
        if started_at(helper) != helper_started:
            return 0
        # A parent that is gone hands its children to launchd, so the parent number changes.
        if os.getppid() != app:
            below = descendants(helper, helper_started)
            below[helper] = helper_started
            for number in (signal.SIGSTOP, signal.SIGKILL):
                for pid, child_started in below.items():
                    _signal_exact(pid, child_started, number)
            return 0
        queue.control(None, 2, WATCH_CHECK_SECONDS)


if __name__ == "__main__":
    raise SystemExit(_watch(int(sys.argv[1]), int(sys.argv[2])))
