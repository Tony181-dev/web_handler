@echo off
setlocal
chcp 65001 >nul
title Web Handler - Uninstall

rem Removes the Web Handler in this folder and the shortcuts that open it. No admin rights needed.
rem Installed with the Setup exe, it opens that installer's own uninstaller instead.
rem Usage: Uninstall.bat [--yes] [--delete-data]
rem   --yes          remove without asking. The data folder is kept unless --delete-data is given too.
rem   --delete-data  also delete the data folder (flows, run history, login sessions) and the saved secrets.
rem Only the files Web Handler installs are deleted: anything else put in this folder stays.

rem Programs are only looked up on PATH, never in the current folder, and Windows tools by their full path.
set "NoDefaultCurrentDirectoryInExePath=1"
set "SYS=%SystemRoot%\System32"
rem Taken before the options are read: shift moves %0 along with them.
set "SELF=%~f0"
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set "WEB_HANDLER_NO_PAUSE=1"
set "ASK=1"
set "DELETE_DATA=0"

:options
if "%~1"=="" goto options_done
if /i "%~1"=="--yes" set "ASK=0"
if /i "%~1"=="--delete-data" set "DELETE_DATA=1"
shift
goto options
:options_done

if exist "%HERE%\tools\build_package.py" goto source_folder
if not exist "%HERE%\Start.bat" goto not_ours
if not exist "%HERE%\app\main.py" goto not_ours
if exist "%HERE%\unins000.exe" goto setup_uninstaller

rem Paths are always quoted when shown: a folder name may hold & or other characters cmd would run.
echo This removes Web Handler from: "%HERE%"
if "%ASK%"=="0" goto stop_app
"%SYS%\choice.exe" /c YN /n /m "Remove it? [Y/N] "
if errorlevel 2 goto cancelled
if not exist "%HERE%\data\" goto stop_app
echo.
echo Your flows, run history and login sessions are in: "%HERE%\data"
"%SYS%\choice.exe" /c YN /n /m "Delete them too, with the saved secrets? Choose N to keep them. [Y/N] "
if errorlevel 2 goto stop_app
set "DELETE_DATA=1"

:stop_app
rem Never opened: nothing is running, and starting Python now would only unpack it.
if exist "%HERE%\offline\python.zip" if not exist "%HERE%\runtime\" goto remove_shortcuts
echo Closing the app, if it is running...
call "%HERE%\Start.bat" --stop
if errorlevel 1 goto still_running
if not "%DELETE_DATA%"=="1" goto remove_shortcuts
call "%HERE%\Start.bat" --forget-secrets
if errorlevel 1 echo Remove the entries starting with WebHandler/ in Windows Credential Manager by hand.

:remove_shortcuts
rem Only shortcuts that open this folder: another Web Handler on this machine keeps its own.
"%SYS%\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$s = New-Object -ComObject WScript.Shell; $here = $env:HERE + '\'; foreach ($folder in [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) { foreach ($name in 'Web Handler.lnk', 'Uninstall Web Handler.lnk') { $path = Join-Path $folder $name; if ((Test-Path -LiteralPath $path) -and $s.CreateShortcut($path).TargetPath.StartsWith($here, [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $path } } }"
if errorlevel 1 echo Could not remove the shortcuts. Delete "Web Handler" from the Desktop and the Start menu by hand.

echo Removing the files...
cd /d "%SYS%"
for %%N in (app offline runtime) do if exist "%HERE%\%%N\" rmdir /s /q "%HERE%\%%N"
for %%N in (Start.bat Install.bat README.txt README.vi.txt) do if exist "%HERE%\%%N" del /f /q "%HERE%\%%N"
if "%DELETE_DATA%"=="1" if exist "%HERE%\data\" rmdir /s /q "%HERE%\data"
if exist "%HERE%\runtime\" goto in_use
if exist "%HERE%\app\" goto in_use
if "%DELETE_DATA%"=="1" if exist "%HERE%\data\" goto in_use

echo.
echo Web Handler has been removed.
if exist "%HERE%\data\" echo Your data is kept in: "%HERE%\data"
if not "%DELETE_DATA%"=="1" echo Saved secrets stay in Windows Credential Manager, under WebHandler/.
if "%ASK%"=="1" pause
rem Last, this file itself and the folder if nothing else is left in it. The line is read in full before
rem (goto) ends the script, so the rest of it still runs, from outside the folder. A folder that
rem still holds the kept data stays, and that is not a failure: (call ) sets the exit code back to 0.
(goto) 2>nul & cd /d "%SYS%" & del /f /q "%SELF%" &(rmdir "%HERE%" 2>nul || (call ))

:setup_uninstaller
echo Web Handler was installed with the Setup exe. Opening its uninstaller...
start "" "%HERE%\unins000.exe"
exit /b 0

:cancelled
echo Nothing was removed.
exit /b 1

:still_running
echo Could not close the running Web Handler. Close its window, then run this again.
echo Nothing was removed.
goto stop_failed

:in_use
echo Some files could not be removed because they are in use: "%HERE%"
echo Restart Windows, then run this again or delete what is left by hand.
goto stop_failed

:source_folder
echo This is the source folder, not an installed Web Handler. Nothing was removed.
goto stop_failed

:not_ours
echo This folder does not hold a Web Handler: "%HERE%"
echo Nothing was removed.

:stop_failed
if "%ASK%"=="1" pause
exit /b 1
