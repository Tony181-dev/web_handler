"""The Windows open-file dialog, called directly because a web page cannot see real paths."""
import ctypes
from ctypes import wintypes

from .i18n import t

OFN_FILEMUSTEXIST = 0x00001000
OFN_PATHMUSTEXIST = 0x00000800
OFN_NOCHANGEDIR = 0x00000008
OFN_EXPLORER = 0x00080000
BUFFER_CHARS = 4096


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def pick_excel_file():
    """Return the chosen path, or None when the person cancels."""
    buffer = ctypes.create_unicode_buffer(BUFFER_CHARS)
    dialog = OPENFILENAMEW()
    dialog.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    dialog.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
    dialog.lpstrFilter = f"{t('Excel files')}\0*.xlsx;*.xlsm\0{t('All files')}\0*.*\0\0"
    dialog.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    dialog.nMaxFile = BUFFER_CHARS
    dialog.lpstrTitle = t("Choose an Excel file")
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR | OFN_EXPLORER
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(dialog)):
        return buffer.value
    return None
