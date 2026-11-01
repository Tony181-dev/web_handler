WEB HANDLER

Automate work on websites and Excel, without writing code.
Tiếng Việt: xem README.vi.txt.

LANGUAGE
The app starts in English. Choose another language at the top right of the app, and it remembers your choice.

INSTALL (no internet or admin rights needed; pick one of three ways)
- Installer: run WebHandler-Setup-<version>.exe, then open Web Handler from the Desktop or the Start menu.
  Windows may warn you because the file is not digitally signed. If your company blocks exe files, use the next way.
- Script: unpack the zip file and double-click Install.bat.
- Without installing: unpack the zip file somewhere you can write, double-click Start.bat, then click "Install missing" on the Setup screen.

Quick try: click "Open sample flow" to run on the built-in demo site.

CLOSING THE APP
Close the app's black window. A running flow, the app's browser and all its helper processes close with it.
If a run is in progress, the row being worked on is marked "Unclear". Your own Chrome and Excel are not affected.

BUILDING YOUR OWN FLOW
1. Click "New flow" and pick a template or "Blank flow". Then drag blocks from the left column, or click "Record" to do the work once by hand. Point at a block to see what it does.
2. Select a block and paste the HTML of the element: F12, right-click the element, Copy, Copy outerHTML.
3. Drag a box around the blocks to repeat, leaving out the login, then click "Create loop".
4. Click "Select Excel", choose the rows in the bar below, click "Run once", then "Run".
   For example, with links in column A and content in column B: choose Excel column A for the Open link block, and column B for the Fill block.
5. To start over, click "Clear all": it deletes every block, the loop and the Excel setup of the flow.

KEEPING YOUR DATA SAFE
- The original Excel file is never changed. Results go to a <name>_result_<time> file next to it.
- Passwords stay in Windows Credential Manager, never in the flow file or the log.
- "Run once" still writes for real on the website, for one row.
- "Check on live page" really runs the blocks before it, with the first Excel row. Only the block being checked is not carried out.
- The "Log" button shows the system log. The log holds no passwords or filled-in values, and lives in the data folder.
- The data folder holds your flows, error screenshots and login sessions. Do not send that folder to anyone.
- The tool does not get past captchas or MFA. Use a "Manual step" block for those.
- Make sure the rules of the system you use allow automation before using this tool.

UNINSTALL
- Installed with the installer: remove Web Handler in Settings, Apps. It asks whether to delete your data.
- Installed with the script or not installed: close the app, then delete the folder and the shortcuts.
Saved secrets stay in Windows Credential Manager, under WebHandler/.
