#!/bin/bash
# Checks whether Web Handler can run on this Mac, and writes probe_report.txt next to this file.

cd "$(dirname "$0")" || exit 1
HERE="$(pwd -P)"

# Keep any Python settings on this machine from affecting the bundled Python,
# including libraries installed for this user in ~/.local or ~/Library/Python.
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP
export PYTHONNOUSERSITE=1
export PYTHONUTF8=1
# Programs are never looked up in the current folder, and system tools are called by their full path.
PATH="$(printf '%s' "$PATH" | /usr/bin/tr ':' '\n' | /usr/bin/grep -v -e '^$' -e '^\.$' | /usr/bin/paste -s -d ':' -)"
export PATH

RUNTIME="$HERE/runtime"
PORTABLE="$RUNTIME/python/bin/python3"
REPORT="$HERE/probe_report.txt"
RC=1

if [ ! -x "$PORTABLE" ] && [ -f "$HERE/offline/python.tar.gz" ]; then
    echo "Extracting the bundled Python..."
    /bin/mkdir -p "$RUNTIME"
    /usr/bin/tar -xzf "$HERE/offline/python.tar.gz" -C "$RUNTIME"
    # A downloaded package carries a quarantine mark, and macOS would refuse the unsigned Python in it.
    /usr/bin/xattr -dr com.apple.quarantine "$RUNTIME/python" 2>/dev/null
fi

run_probe() {
    if [ -x "$PORTABLE" ] && "$PORTABLE" -c "import ctypes, sqlite3, ssl" >/dev/null 2>&1; then
        "$PORTABLE" "$HERE/probe.py" --runner portable
        RC=$?
        return 0
    fi
    echo "The bundled Python could not run. Trying the Python on this machine..."
    for name in python3.12 python3; do
        candidate="$(command -v "$name")" || continue
        # Without the developer tools, /usr/bin/python3 only offers to install them.
        if [ "$candidate" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if "$candidate" -c "import sys" >/dev/null 2>&1; then
            "$candidate" "$HERE/probe.py" --runner system
            RC=$?
            return 0
        fi
    done
    return 1
}

if ! run_probe; then
    {
        echo "WEB HANDLER - ENVIRONMENT REPORT"
        echo
        echo "RESULT: NO_PYTHON"
        echo "The bundled Python could not run, and no Python was found on this machine."
        echo "Bundled Python extracted:"
        if [ -x "$PORTABLE" ]; then echo yes; else echo no; fi
    } > "$REPORT"
    echo "No Python could run. Please send back probe_report.txt."
fi

echo
if [ "$1" != "--no-pause" ] && [ -t 0 ]; then
    read -r -p "Press Enter to close this window. " _
fi
exit "$RC"
