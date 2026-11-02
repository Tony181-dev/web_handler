@echo off
setlocal
chcp 65001 >nul
title Web Handler - Build
cd /d "%~dp0"

rem Builds dist\WebHandler-Setup-<version>.exe, the one file to send to other people.
rem Needs internet the first time, Python 3 and Inno Setup 6. Options go to tools\build_package.py.
set "PYTHON="
py -3 -c "import sys" >nul 2>&1 && set "PYTHON=py -3"
if defined PYTHON goto build
python -c "import sys" >nul 2>&1 && set "PYTHON=python"
if defined PYTHON goto build
for /d %%F in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%F\python.exe" set "PYTHON="%%F\python.exe""
if defined PYTHON goto build
echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.
goto failed

:build
%PYTHON% tools\build_package.py %*
if errorlevel 1 goto failed
echo.
echo Done. Send the WebHandler-Setup file in the dist folder to the other person.
echo They only need to run it: no internet, no admin rights.
if defined WEB_HANDLER_NO_PAUSE exit /b 0
start "" "%~dp0dist"
pause
exit /b 0

:failed
echo.
echo The build did not finish.
if not defined WEB_HANDLER_NO_PAUSE pause
exit /b 1
