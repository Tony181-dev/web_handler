"""The macOS open-file dialog, shown through AppleScript because a web page cannot see real paths."""
import subprocess

from .i18n import t

# The prompt arrives as an argument, never as part of the script text.
SCRIPT = """on run argv
    activate
    try
        set chosen to choose file with prompt (item 1 of argv) of type {"org.openxmlformats.spreadsheetml.sheet", "org.openxmlformats.spreadsheetml.sheet.macroenabled", "com.microsoft.excel.sheet.macroEnabled", "xlsx", "xlsm"}
    on error number -128
        return ""
    end try
    return POSIX path of chosen
end run
"""


def pick_excel_file():
    """Return the chosen path, or None when the person cancels."""
    result = subprocess.run(
        ["/usr/bin/osascript", "-", t("Choose an Excel file")],
        input=SCRIPT, capture_output=True, text=True, encoding="utf-8",
    )
    path = result.stdout.rstrip("\n")
    if result.returncode != 0 or not path:
        return None
    return path
