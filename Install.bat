@echo off
setlocal
chcp 65001 >nul
title Web Handler - Install

rem Installs from the unpacked zip, for machines where the Setup exe is blocked.
rem Usage: Install.bat [target folder]. No admin rights needed. The data folder is never touched.
set "SOURCE=%~dp0"
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=%LOCALAPPDATA%\Programs\WebHandler"
set "WEB_HANDLER_NO_PAUSE=1"

if not exist "%SOURCE%app\main.py" goto bad_source

echo Installing to: %TARGET%
if not exist "%TARGET%\Start.bat" goto copy_files
echo Closing the running app, if any...
call "%TARGET%\Start.bat" --stop

:copy_files
robocopy "%SOURCE%app" "%TARGET%\app" /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto failed
robocopy "%SOURCE%offline" "%TARGET%\offline" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto failed
copy /y "%SOURCE%Start.bat" "%TARGET%\Start.bat" >nul || goto failed
copy /y "%SOURCE%README.txt" "%TARGET%\README.txt" >nul || goto failed
copy /y "%SOURCE%README.vi.txt" "%TARGET%\README.vi.txt" >nul || goto failed

echo Installing libraries. This takes a few minutes...
call "%TARGET%\Start.bat" --install
if errorlevel 1 echo The app will finish this on its Setup screen when you first open it.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = New-Object -ComObject WScript.Shell; foreach ($folder in [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) { $l = $s.CreateShortcut((Join-Path $folder 'Web Handler.lnk')); $l.TargetPath = $env:TARGET + '\Start.bat'; $l.WorkingDirectory = $env:TARGET; $l.IconLocation = $env:SystemRoot + '\System32\shell32.dll,13'; $l.Save() }"
if errorlevel 1 echo Could not create shortcuts. Open the app with Start.bat in the target folder.

echo.
echo Done. Open Web Handler from the Desktop or the Start menu.
exit /b 0

:bad_source
echo Run this file from the unpacked Web Handler folder.
exit /b 1

:failed
echo Could not copy the files to %TARGET%.
exit /b 1
