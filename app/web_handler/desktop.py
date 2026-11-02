"""Bring the app's browser window up, which the browser automation library cannot do.

Windows lets only the program the person is using take the foreground. A Chrome started by the app in
the background therefore opens behind the app's own page, as if it were hidden. Placing a window above
the others needs no such right, and leaves the keyboard where the person has it.
"""
import ctypes
from ctypes import wintypes

from . import child_processes

JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
MAX_PROCESSES = 1024
CHROME_WINDOW = "Chrome_WidgetWin_1"
GW_OWNER = 4
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

_WindowCallback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.QueryInformationJobObject.argtypes = (
    wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.EnumWindows.argtypes = (_WindowCallback, wintypes.LPARAM)
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)
_user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
_user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
_user32.GetWindow.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.SetWindowPos.argtypes = (
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
)


class _ProcessIds(ctypes.Structure):
    _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD), ("ids", ctypes.c_size_t * MAX_PROCESSES)]


def _own_processes():
    """The processes in this process's job that started after it: the browser it started.

    Unlike parent process numbers, which Windows reuses, the job lists only processes alive in it.
    Should the helper have no job of its own, an outer one may hold older programs, which are left out.
    Outside any job the list is empty and nothing is moved.
    """
    ids = _ProcessIds()
    if not _kernel32.QueryInformationJobObject(
        None, JOB_OBJECT_BASIC_PROCESS_ID_LIST, ctypes.byref(ids), ctypes.sizeof(ids), None,
    ):
        return set()
    me, started = child_processes.current_process()
    return {
        pid for pid in ids.ids[:ids.listed]
        if pid != me and (child_processes.started_at(pid) or 0) > (started or 0)
    }


def _is_browser_window(hwnd):
    if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, GW_OWNER):
        return False
    if not _user32.GetWindowTextLengthW(hwnd):
        return False
    name = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, name, 64)
    return name.value == CHROME_WINDOW


def raise_browser_windows():
    """Place the windows of the browser this process started above all others, without taking the keyboard."""
    own = _own_processes()
    if not own:
        return 0
    found = []

    def each(hwnd, _):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in own and _is_browser_window(hwnd):
            found.append(hwnd)
        return True

    _user32.EnumWindows(_WindowCallback(each), 0)
    # Found from the top down: raising the lowest first keeps their order among themselves.
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    for hwnd in reversed(found):
        _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    return len(found)
