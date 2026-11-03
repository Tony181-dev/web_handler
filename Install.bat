@echo off
setlocal
chcp 65001 >nul

rem One file for both ends:
rem - In the source folder it builds the package: dist\WebHandler-Setup-<version>.exe and the zip to send,
rem   and brings dist\WebHandler up to date, keeping its data. Options go to tools\build_package.py.
rem   Needs internet the first time, Python 3 and Inno Setup 6.
rem - In the unpacked zip it installs, for machines where the Setup exe is blocked.
rem   Usage: Install.bat [target folder]. No admin rights needed. The data folder is never touched.
set "SOURCE=%~dp0"
if exist "%SOURCE%tools\build_package.py" goto build

title Web Handler - Install
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

:build
title Web Handler - Build
cd /d "%SOURCE%"
set "PYTHON="
py -3 -c "import sys" >nul 2>&1 && set "PYTHON=py -3"
if defined PYTHON goto run_build
python -c "import sys" >nul 2>&1 && set "PYTHON=python"
if defined PYTHON goto run_build
for /d %%F in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%F\python.exe" set "PYTHON="%%F\python.exe""
if defined PYTHON goto run_build
echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.
goto build_failed

:run_build
%PYTHON% tools\build_package.py %*
if errorlevel 1 goto build_failed
echo.
echo Done. dist\WebHandler is up to date: close the app window and open it again with dist\WebHandler\Start.bat.
echo To share it, send the WebHandler-Setup file in the dist folder. The other person only runs it:
echo no internet, no admin rights.
if defined WEB_HANDLER_NO_PAUSE exit /b 0
start "" "%~dp0dist"
pause
exit /b 0

:build_failed
echo.
echo The build did not finish.
if not defined WEB_HANDLER_NO_PAUSE pause
exit /b 1
