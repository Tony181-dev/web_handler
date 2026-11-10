#!/bin/bash
# One file for both ends, on macOS:
# - In the source folder it builds the package: dist/WebHandler-<version>-macOS-<arch>.zip to send,
#   and brings dist/WebHandler up to date, keeping its data. Options go to tools/build_package.py.
#   Needs internet the first time and Python 3.
# - In the unpacked zip it installs. Usage: Install.command [target folder]. No admin rights needed.
#   The data folder is never touched. Uninstall.command, copied next to it, removes it again.

cd "$(dirname "$0")" || exit 1
SOURCE="$(pwd -P)"
# Programs are never looked up in the current folder, and system tools are called by their full path.
PATH="$(printf '%s' "$PATH" | /usr/bin/tr ':' '\n' | /usr/bin/grep -v -e '^$' -e '^\.$' | /usr/bin/paste -s -d ':' -)"
export PATH

# Only a person at the window is asked to press Enter at the end.
ASK=0
if [ -z "$WEB_HANDLER_NO_PAUSE" ] && [ -t 0 ]; then ASK=1; fi
finish() {
    if [ "$ASK" = 1 ]; then
        echo
        read -r -p "Press Enter to close this window. " _
    fi
    exit "$1"
}

# A launcher in ~/Applications that opens a file of the install folder in a Terminal window.
# The folder is kept in a file of its own, never written into the script, so no name can break it.
make_launcher() {
    local app="$1" script="$2" identifier="$3"
    /bin/rm -rf "$app"
    /bin/mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" || return 1
    printf '%s' "$TARGET" > "$app/Contents/Resources/folder" || return 1
    printf '%s' "$script" > "$app/Contents/Resources/script" || return 1
    /bin/cat > "$app/Contents/MacOS/launcher" <<'LAUNCHER' || return 1
#!/bin/sh
resources="$(dirname "$0")/../Resources"
exec /usr/bin/open -a Terminal "$(cat "$resources/folder")/$(cat "$resources/script")"
LAUNCHER
    /bin/chmod 755 "$app/Contents/MacOS/launcher"
    /bin/cat > "$app/Contents/Info.plist" <<PLIST || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIdentifier</key><string>$identifier</string>
    <key>CFBundleName</key><string>$(basename "$app" .app)</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
}

find_python() {
    for name in python3.12 python3; do
        candidate="$(command -v "$name")" || continue
        # Without the developer tools, /usr/bin/python3 only offers to install them.
        if [ "$candidate" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if "$candidate" -c "import sys; sys.exit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
            PYTHON="$candidate"
            return 0
        fi
    done
    return 1
}

if [ -f "$SOURCE/tools/build_package.py" ]; then
    echo "Web Handler - Build"
    if ! find_python; then
        echo "Python 3 was not found. Install it from https://www.python.org/downloads/macos/ and run this again."
        echo
        echo "The build did not finish."
        finish 1
    fi
    if ! "$PYTHON" "$SOURCE/tools/build_package.py" "$@"; then
        echo
        echo "The build did not finish."
        finish 1
    fi
    echo
    echo "Done. dist/WebHandler is up to date: close the app window and open it again with dist/WebHandler/Start.command."
    echo "To share it, send the WebHandler zip file in the dist folder. The other person unpacks it and"
    echo "double-clicks Install.command: no internet, no admin rights."
    if [ "$ASK" = 1 ]; then /usr/bin/open "$SOURCE/dist"; fi
    finish 0
fi

echo "Web Handler - Install"
TARGET="${1:-$HOME/Library/Application Support/WebHandler}"
export WEB_HANDLER_NO_PAUSE=1
APPLICATIONS="$HOME/Applications"

if [ ! -f "$SOURCE/app/main.py" ]; then
    echo "Run this file from the unpacked Web Handler folder."
    finish 1
fi

echo "Installing to: \"$TARGET\""
# Copying mirrors the app and offline folders, deleting anything else in them, so the target must be
# an earlier Web Handler install or a folder with nothing in it.
if [ -f "$TARGET/Start.command" ] && [ -f "$TARGET/app/main.py" ]; then
    echo "Closing the running app, if any..."
    /bin/bash "$TARGET/Start.command" --stop
elif [ -e "$TARGET" ] && [ -n "$(/bin/ls -A "$TARGET" 2>/dev/null)" ]; then
    echo "This folder already holds other files: \"$TARGET\""
    echo "Installing there could delete them. Choose an empty folder or an earlier Web Handler folder."
    finish 1
fi

copy_failed() {
    echo "Could not copy the files to \"$TARGET\"."
    finish 1
}
/bin/mkdir -p "$TARGET" || copy_failed
/usr/bin/rsync -a --delete --exclude __pycache__ "$SOURCE/app/" "$TARGET/app/" || copy_failed
/usr/bin/rsync -a --delete "$SOURCE/offline/" "$TARGET/offline/" || copy_failed
for name in Start.command Uninstall.command README.txt README.vi.txt; do
    /bin/cp -f "$SOURCE/$name" "$TARGET/$name" || copy_failed
done
/bin/chmod 755 "$TARGET/Start.command" "$TARGET/Uninstall.command"
# The unpacked zip carries the quarantine mark of its download, which would make macOS refuse to open the copy.
/usr/bin/xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null

echo "Installing libraries. This takes a few minutes..."
if ! /bin/bash "$TARGET/Start.command" --install; then
    echo "The app will finish this on its Setup screen when you first open it."
fi

if /bin/mkdir -p "$APPLICATIONS" \
        && make_launcher "$APPLICATIONS/Web Handler.app" Start.command local.webhandler.open \
        && make_launcher "$APPLICATIONS/Uninstall Web Handler.app" Uninstall.command local.webhandler.uninstall; then
    echo
    echo "Done. Open Web Handler from Launchpad, Spotlight or the Applications folder in your home folder."
    echo "To remove it later: \"Uninstall Web Handler\" there, or Uninstall.command in \"$TARGET\"."
else
    echo "Could not create the launchers. Open the app with Start.command in \"$TARGET\"."
fi
finish 0
