"""The system's open-file dialog: file_dialog_windows on Windows, file_dialog_macos on macOS."""
import sys

if sys.platform == "win32":
    from .file_dialog_windows import pick_excel_file  # noqa: F401
else:
    from .file_dialog_macos import pick_excel_file  # noqa: F401
