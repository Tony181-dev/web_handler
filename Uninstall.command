#!/bin/bash
# Removes the Web Handler in this folder and the launchers that open it, on macOS. No admin rights needed.
# Usage: Uninstall.command [--yes] [--delete-data]
#   --yes          remove without asking. The data folder is kept unless --delete-data is given too.
#   --delete-data  also delete the data folder (flows, run history, login sessions) and the saved secrets.
# Only the files Web Handler installs are deleted: anything else put in this folder stays.

SELF="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"
HERE="$(dirname "$SELF")"
# Programs are never looked up in the current folder, and system tools are called by their full path.
PATH="$(printf '%s' "$PATH" | /usr/bin/tr ':' '\n' | /usr/bin/grep -v -e '^$' -e '^\.$' | /usr/bin/paste -s -d ':' -)"
export PATH
export WEB_HANDLER_NO_PAUSE=1
ASK=1
DELETE_DATA=0
for option in "$@"; do
    case "$option" in
        --yes) ASK=0 ;;
        --delete-data) DELETE_DATA=1 ;;
    esac
done

finish() {
    if [ "$ASK" = 1 ] && [ -t 0 ]; then
        echo
        read -r -p "Press Enter to close this window. " _
    fi
    exit "$1"
}

# Answers y or n. Anything else asks again.
confirm() {
    local answer
    while true; do
        read -r -p "$1 [y/n] " answer || return 1
        case "$answer" in
            [Yy]) return 0 ;;
            [Nn]) return 1 ;;
        esac
    done
}

if [ -f "$HERE/tools/build_package.py" ]; then
    echo "This is the source folder, not an installed Web Handler. Nothing was removed."
    finish 1
fi
if [ ! -f "$HERE/Start.command" ] || [ ! -f "$HERE/app/main.py" ]; then
    echo "This folder does not hold a Web Handler: \"$HERE\""
    echo "Nothing was removed."
    finish 1
fi

echo "This removes Web Handler from: \"$HERE\""
if [ "$ASK" = 1 ]; then
    if ! confirm "Remove it?"; then
        echo "Nothing was removed."
        finish 1
    fi
    if [ -d "$HERE/data" ]; then
        echo
        echo "Your flows, run history and login sessions are in: \"$HERE/data\""
        if confirm "Delete them too, with the saved secrets? Choose n to keep them."; then
            DELETE_DATA=1
        fi
    fi
fi

# Never opened: nothing is running, and starting Python now would only unpack it.
if [ ! -f "$HERE/offline/python.tar.gz" ] || [ -d "$HERE/runtime" ]; then
    echo "Closing the app, if it is running..."
    if ! /bin/bash "$HERE/Start.command" --stop; then
        echo "Could not close the running Web Handler. Close its window, then run this again."
        echo "Nothing was removed."
        finish 1
    fi
    if [ "$DELETE_DATA" = 1 ] && ! /bin/bash "$HERE/Start.command" --forget-secrets; then
        echo "Remove the entries starting with WebHandler/ in Keychain Access by hand."
    fi
fi

# Only launchers that open this folder: another Web Handler on this machine keeps its own.
for app in "$HOME/Applications/Web Handler.app" "$HOME/Applications/Uninstall Web Handler.app"; do
    if [ -f "$app/Contents/Resources/folder" ] && [ "$(/bin/cat "$app/Contents/Resources/folder")" = "$HERE" ]; then
        /bin/rm -rf "$app" || echo "Could not remove \"$app\". Move it to the Trash by hand."
    fi
done

echo "Removing the files..."
cd / || exit 1
for name in app offline runtime; do
    if [ -e "$HERE/$name" ]; then /bin/rm -rf "$HERE/$name"; fi
done
for name in Start.command Install.command README.txt README.vi.txt; do
    /bin/rm -f "$HERE/$name"
done
if [ "$DELETE_DATA" = 1 ] && [ -e "$HERE/data" ]; then /bin/rm -rf "$HERE/data"; fi
if [ -e "$HERE/runtime" ] || [ -e "$HERE/app" ] || { [ "$DELETE_DATA" = 1 ] && [ -e "$HERE/data" ]; }; then
    echo "Some files could not be removed: \"$HERE\""
    echo "Restart the Mac, then run this again or delete what is left by hand."
    finish 1
fi

echo
echo "Web Handler has been removed."
if [ -d "$HERE/data" ]; then echo "Your data is kept in: \"$HERE/data\""; fi
if [ "$DELETE_DATA" != 1 ]; then echo "Saved secrets stay in the macOS Keychain, under WebHandler/."; fi
# Last, this file itself and the folder if nothing else is left in it. A folder that still holds the
# kept data stays, and that is not a failure.
/bin/rm -f "$SELF"
# Finder's own view settings are not the person's files.
/bin/rm -f "$HERE/.DS_Store"
/bin/rmdir "$HERE" 2>/dev/null
finish 0
