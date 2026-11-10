"""Bring the app's browser window up, which the browser automation library cannot always do.

On macOS a Chrome started by the app's helper may open behind the app's own page. The browser this
process started is asked to come forward through NSRunningApplication, which needs no permission from
the person. macOS may decline, and then the window simply stays where it is.
"""
import ctypes
import ctypes.util

from . import child_processes_posix as child_processes

ACTIVATE_ALL_WINDOWS = 1 << 0
ACTIVATE_IGNORING_OTHER_APPS = 1 << 1

_objc = None


def _runtime():
    global _objc
    if _objc is None:
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = (ctypes.c_char_p,)
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        objc.objc_autoreleasePoolPop.argtypes = (ctypes.c_void_p,)
        _objc = objc
    return _objc


def _send(objc, returns, *argtypes):
    """objc_msgSend with the exact signature of one method, as arm64 needs."""
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    return ctypes.CFUNCTYPE(returns, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)


def raise_browser_windows():
    """Bring the browser this process started to the front. Returns how many applications were asked."""
    me, started = child_processes.current_process()
    own = child_processes.descendants(me, started)
    if not own:
        return 0
    objc = _runtime()
    find = _send(objc, ctypes.c_void_p, ctypes.c_int)
    activate = _send(objc, ctypes.c_bool, ctypes.c_ulong)
    running_application = objc.objc_getClass(b"NSRunningApplication")
    by_pid = objc.sel_registerName(b"runningApplicationWithProcessIdentifier:")
    activate_with = objc.sel_registerName(b"activateWithOptions:")
    pool = objc.objc_autoreleasePoolPush()
    asked = 0
    try:
        # Only the browser's main process is an application. Its helpers find nothing.
        for pid in own:
            application = find(running_application, by_pid, pid)
            if application:
                activate(application, activate_with, ACTIVATE_ALL_WINDOWS | ACTIVATE_IGNORING_OTHER_APPS)
                asked += 1
    finally:
        objc.objc_autoreleasePoolPop(pool)
    return asked
