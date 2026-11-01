@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Keep any Python settings on this machine from affecting the bundled Python.
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"

set "RUNTIME=%~dp0runtime"
set "PORTABLE=%RUNTIME%\python\python.exe"
set "RC=1"

if exist "%PORTABLE%" goto try_portable
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
echo Extracting the bundled Python...
rem tar cannot handle non-ASCII absolute paths, so extract with relative ones.
pushd "%RUNTIME%"
"%SystemRoot%\System32\tar.exe" -xf "..\offline\python.zip"
popd

:try_portable
"%PORTABLE%" -c "import ctypes, sqlite3, ssl" >nul 2>&1
if errorlevel 1 goto try_py
"%PORTABLE%" "%~dp0probe.py" --runner portable
set "RC=%ERRORLEVEL%"
goto done

:try_py
echo The bundled Python could not run. Trying the Python on this machine...
py -3 -c "import sys" >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%~dp0probe.py" --runner system
set "RC=%ERRORLEVEL%"
goto done

:try_python
python -c "import sys" >nul 2>&1
if errorlevel 1 goto no_python
python "%~dp0probe.py" --runner system
set "RC=%ERRORLEVEL%"
goto done

:no_python
> "%~dp0probe_report.txt" echo WEB HANDLER - ENVIRONMENT REPORT
>> "%~dp0probe_report.txt" echo.
>> "%~dp0probe_report.txt" echo RESULT: NO_PYTHON
>> "%~dp0probe_report.txt" echo The bundled Python could not run, and no Python was found on this machine.
>> "%~dp0probe_report.txt" echo Bundled Python extracted:
if exist "%PORTABLE%" (>> "%~dp0probe_report.txt" echo yes) else (>> "%~dp0probe_report.txt" echo no)
echo No Python could run. Please send back probe_report.txt.

:done
echo.
if /i not "%~1"=="--no-pause" pause
exit /b %RC%
