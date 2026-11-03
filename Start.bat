@echo off
setlocal
chcp 65001 >nul
title Web Handler
cd /d "%~dp0"

rem Keep any Python settings on this machine from affecting the bundled Python,
rem including libraries installed for this user in %APPDATA%\Python.
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
rem py and python are only looked up on PATH, never in this folder.
set "NoDefaultCurrentDirectoryInExePath=1"

set "RUNTIME=%~dp0runtime"
set "PORTABLE=%RUNTIME%\python\python.exe"

if exist "%PORTABLE%" goto try_portable
if not exist "%~dp0offline\python.zip" goto try_py
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
echo First start: extracting the bundled Python. This takes a moment...
rem tar cannot handle non-ASCII absolute paths, so extract with relative ones.
pushd "%RUNTIME%"
"%SystemRoot%\System32\tar.exe" -xf "..\offline\python.zip"
popd

:try_portable
"%PORTABLE%" -c "import ctypes, ssl" >nul 2>&1
if errorlevel 1 goto try_py
"%PORTABLE%" "%~dp0app\main.py" %*
goto done

:try_py
echo The bundled Python could not run. Trying the Python on this machine...
py -3 -c "import sys" >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%~dp0app\main.py" %*
goto done

:try_python
python -c "import sys" >nul 2>&1
if errorlevel 1 goto no_python
python "%~dp0app\main.py" %*
goto done

:no_python
echo.
echo Web Handler could not start: the bundled Python was blocked or damaged,
echo and no Python was found on this machine.
echo Ask your IT department whether programs may run from this folder.
rem The installers run this file with no window, where a pause would wait forever.
if not defined WEB_HANDLER_NO_PAUSE pause
exit /b 1

:done
if errorlevel 1 if not defined WEB_HANDLER_NO_PAUSE pause
exit /b %ERRORLEVEL%
