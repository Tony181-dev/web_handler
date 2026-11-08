WEB HANDLER

Automate work on websites and Excel, without writing code.
Tiếng Việt: xem README.vi.txt.

LANGUAGE
The app starts in English. Choose another language at the top right of the app, and it remembers your choice.

INSTALL ON WINDOWS (no internet or admin rights needed; pick one of three ways)
- Installer: run WebHandler-Setup-<version>.exe, then open Web Handler from the Desktop or the Start menu.
  Windows may warn you because the file is not digitally signed. If your company blocks exe files, use the next way.
- Script: unpack the zip file and double-click Install.bat.
- Without installing: unpack the zip file somewhere you can write, double-click Start.bat, then click "Install missing" on the Setup screen.

INSTALL ON macOS (macOS 14 or later on Apple silicon, macOS 13 or later on Intel; no internet or admin rights needed)
Use WebHandler-<version>-macOS-arm64.zip on a Mac with Apple silicon (M1, M2...), -macOS-x86_64.zip on an Intel Mac.
- Script: unpack the zip file and double-click Install.command. It installs to ~/Library/Application Support/WebHandler.
  Then open Web Handler from Launchpad, Spotlight or the Applications folder in your home folder.
- Without installing: unpack the zip file somewhere you can write, double-click Start.command, then click "Install missing" on the Setup screen.
The package is not signed by Apple, so the first time macOS may refuse to open a .command file. Then open System Settings,
Privacy & Security, scroll down and click "Open Anyway". Or open Terminal and run:
  xattr -dr com.apple.quarantine <the unpacked folder>
macOS may ask to let Terminal access your Downloads or Desktop folder: allow it to install.
What is different on a Mac:
- Shortcuts use ⌘ for Ctrl (⌘Z, ⌘⇧Z, ⌘A). To add blocks to a selection, hold ⌘ while clicking or drawing a box.
- Open Chrome's DevTools with ⌘⌥I instead of F12. The delete key deletes the selected blocks.
- Passwords are kept in the macOS Keychain, under WebHandler/.

Quick try: click "Open sample flow" to run on the built-in demo site.

CLOSING THE APP
Close the app's black window (on macOS its Terminal window, or press Ctrl+C in it). A running flow, the app's browser and all its helper processes close with it.
If a run is in progress, the row being worked on is marked "Unclear". Your own Chrome and Excel are not affected.

BUILDING YOUR OWN FLOW
1. Click "New flow" and pick a template or "Blank flow". Then drag blocks from the left column, or click "Record" to do the work once by hand. Point at a block to see what it does.
   For logging in, use a Login block: save the password under Secrets first, then choose it in the block.
2. Select a block and paste the HTML of the element: F12, right-click the element, Copy, Copy outerHTML.
3. To repeat blocks, click the Loop block on the left and drag blocks into it. Or drag a box around existing blocks, leaving out the login, and click "Create loop".
4. Click "Select Excel", choose the rows in the bar below, click "Run once", then "Run".
   For example, with links in column A and content in column B: choose Excel column A for the Open link block, and column B for the Fill block.
5. To start over, click "Clear all": it deletes every block, the loop and the Excel setup of the flow.

KEEPING YOUR DATA SAFE
- The original Excel file is never changed. Results go to a <name>_result_<time> file next to it.
- Passwords stay in Windows Credential Manager (on macOS the Keychain), never in the flow file or the log.
- "Run once" still writes for real on the website, for one row.
- "Check on live page" really runs the blocks before it, with the first Excel row. Only the block being checked is not carried out.
- The "Log" button shows the system log. The log holds no passwords or filled-in values, and lives in the data folder.
- The data folder holds your flows, error screenshots and login sessions. Do not send that folder to anyone.
- The "Read OCR" block can read a simple captcha, and it can read it wrongly. It works on this machine: pictures are not saved or sent anywhere. For MFA and hard captchas, use a "Manual step" block.
- Make sure the rules of the system you use allow automation, including reading captchas, before using this tool.

SHARING IT WITH SOMEONE
Double-click Install.bat in the source folder. It builds the package and needs internet the first time, Python 3 and Inno Setup 6.
Send the WebHandler-Setup-<version>.exe it creates. The other person only runs that one file.
On macOS: double-click Install.command in the source folder (internet and Python 3 needed the first time).
It builds dist/WebHandler-<version>-macOS-<arm64 or x86_64>.zip, for the kind of Mac the build runs on.
Send that zip. The other person unpacks it and double-clicks Install.command.

UNINSTALL
Click "Uninstall Web Handler" in the Start menu, or double-click Uninstall.bat in the app folder.
It closes the app, removes its files and shortcuts, and asks whether to delete your data too:
flows, run history, login sessions and saved secrets. Choose No to keep them for a later install.
- Installed with the installer: Settings, Apps, Web Handler works too.
- Not installed (unpacked zip): Uninstall.bat in that folder removes it the same way.
Kept secrets stay in Windows Credential Manager, under WebHandler/.
On macOS: open "Uninstall Web Handler" in the Applications folder of your home folder, or double-click
Uninstall.command in the app folder (~/Library/Application Support/WebHandler). It asks the same, answer y or n.
Kept secrets stay in the Keychain, under WebHandler/ (see them with the Keychain Access app).
