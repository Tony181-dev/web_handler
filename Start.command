#!/bin/bash
# Opens Web Handler on macOS. Keep this Terminal window open while using the app: closing it closes the app.
# Options go to app/main.py: --stop, --install and --forget-secrets are for the installers.

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
STATUS=1

pause_on_failure() {
    # The installers run this file with no one at the window, where a pause would wait forever.
    if [ "$STATUS" -ne 0 ] && [ -z "$WEB_HANDLER_NO_PAUSE" ] && [ -t 0 ]; then
        echo
        read -r -p "Press Enter to close this window. " _
    fi
}

if [ ! -x "$PORTABLE" ] && [ -f "$HERE/offline/python.tar.gz" ]; then
    echo "First start: extracting the bundled Python. This takes a moment..."
    /bin/mkdir -p "$RUNTIME"
    /usr/bin/tar -xzf "$HERE/offline/python.tar.gz" -C "$RUNTIME"
    # A downloaded package carries a quarantine mark, and macOS would refuse the unsigned Python in it.
    /usr/bin/xattr -dr com.apple.quarantine "$RUNTIME/python" 2>/dev/null
fi

if [ -x "$PORTABLE" ] && "$PORTABLE" -c "import ctypes, ssl" >/dev/null 2>&1; then
    "$PORTABLE" "$HERE/app/main.py" "$@"
    STATUS=$?
    pause_on_failure
    exit "$STATUS"
fi

echo "The bundled Python could not run. Trying the Python on this machine..."
for name in python3.12 python3; do
    candidate="$(command -v "$name")" || continue
    # Without the developer tools, /usr/bin/python3 only offers to install them.
    if [ "$candidate" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
        continue
    fi
    if "$candidate" -c "import sys; sys.exit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
        "$candidate" "$HERE/app/main.py" "$@"
        STATUS=$?
        pause_on_failure
        exit "$STATUS"
    fi
done

echo
echo "Web Handler could not start: the bundled Python was blocked or damaged,"
echo "and no Python 3.10 or newer was found on this machine."
echo "Install Python 3.12 from https://www.python.org/downloads/macos/ or ask your IT department."
STATUS=1
pause_on_failure
exit 1
