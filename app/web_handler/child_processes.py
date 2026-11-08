"""Everything the app starts for itself ends with it, however the app ends.

Windows ties each helper to a job (child_processes_windows). macOS follows it through the process
table (child_processes_posix). Both offer the same functions.
"""
import sys

if sys.platform == "win32":
    from .child_processes_windows import (  # noqa: F401
        POPEN_OPTIONS, adopt, current_process, end_job, end_process, release_job, started_at,
    )
else:
    from .child_processes_posix import (  # noqa: F401
        POPEN_OPTIONS, adopt, current_process, end_job, end_process, release_job, started_at,
    )
