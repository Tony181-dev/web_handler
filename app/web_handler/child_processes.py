"""Everything the app starts for itself ends with it, however the app ends.

Each helper gets its own Windows job. Windows closes a job when the last handle to it goes, which
happens when this process is gone for any reason, and then ends every process in the job along with
what those processes started. A job also ends exactly that helper's processes on request, unlike
following parent process numbers, which Windows reuses for unrelated programs.
Only the app's own helpers go in: a browser or Excel opened for the person must outlive the app.
"""
import ctypes
from ctypes import wintypes

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_WAITING = 0x102

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
_kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
_kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4)
_kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.GetCurrentProcessId.restype = wintypes.DWORD


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "read_operations", "write_operations", "other_operations",
        "read_bytes", "write_bytes", "other_bytes",
    )]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time", ctypes.c_longlong),
        ("per_job_user_time", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


def adopt(process):
    """Tie a just-started subprocess.Popen to this process's life. Returns its job, or None if Windows refuses."""
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _ExtendedLimits()
    limits.basic.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = _kernel32.SetInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits),
    )
    if configured and _kernel32.AssignProcessToJobObject(job, int(process._handle)):
        return job
    _kernel32.CloseHandle(job)
    return None


def end_job(job):
    """End the helper and everything it started."""
    _kernel32.TerminateJobObject(job, 1)


def release_job(job):
    """The helper has finished. Closing its job also ends anything it left behind."""
    _kernel32.CloseHandle(job)


def _creation_time(handle):
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel32.GetProcessTimes(handle, *[ctypes.byref(item) for item in times]):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def started_at(pid):
    """When a process started, which tells it apart from a later one given the same number."""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        return _creation_time(handle)
    finally:
        _kernel32.CloseHandle(handle)


def current_process():
    pid = _kernel32.GetCurrentProcessId()
    return pid, started_at(pid)


def end_process(pid, started, timeout_seconds=10):
    """End that exact process and wait for it. Returns False only if it could not be ended."""
    handle = _kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid,
    )
    if not handle:
        return True
    try:
        if _creation_time(handle) != started:
            return True
        # Exit code 0: any other makes Start.bat stop at its pause, keeping a window open on the app folder.
        _kernel32.TerminateProcess(handle, 0)
        return _kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000)) != STILL_WAITING
    finally:
        _kernel32.CloseHandle(handle)
