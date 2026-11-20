@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ComfyOS - Setup und Start

echo.
echo  ==========================================
echo       ComfyOS - STARTASSISTENT
echo  ==========================================
echo.

set "PYEXE="

REM 1) Look for an already installed Python 3.10+
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"
  )
  if not defined PYEXE (
    py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"
  )
  if not defined PYEXE (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"
  )
)

REM 2) Try python.exe on PATH
if not defined PYEXE (
  where python >nul 2>nul
  if not errorlevel 1 (
    for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
    if defined PYEXE (
      "%PYEXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
      if errorlevel 1 set "PYEXE="
    )
  )
)

REM 3) If missing, install Python 3.12 for this Windows user using winget
if not defined PYEXE (
  echo Python 3.10 oder neuer wurde nicht gefunden.
  echo Ich versuche jetzt, Python 3.12 automatisch zu installieren.
  echo.
  where winget >nul 2>nul
  if errorlevel 1 (
    echo Windows-Paketmanager winget wurde nicht gefunden.
    echo Bitte installiere Python einmalig von:
    echo https://www.python.org/downloads/windows/
    echo Waehle beim Setup "Add Python to PATH".
    echo Starte danach diese Datei erneut.
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
  )
  winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo Die automatische Python-Installation ist fehlgeschlagen.
    echo Bitte installiere Python manuell von https://www.python.org/downloads/windows/
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
  )
  REM winget may not refresh PATH in this already-open command window.
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
  if not defined PYEXE (
    for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
  )
  if not defined PYEXE (
    echo Python wurde installiert, aber in diesem Fenster noch nicht gefunden.
    echo Bitte schliesse dieses Fenster und starte start.bat erneut.
    pause
    exit /b 1
  )
)

echo Verwende Python:
"%PYEXE%" --version
echo.

REM Create app-local virtual environment (does not modify system Python packages)
if not exist ".venv\Scripts\python.exe" (
  echo Richte die App-Umgebung ein ...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 goto error
)

echo Installiere bzw. aktualisiere benoetigte App-Komponenten ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto error

if not defined COMFYOS_PORT set "COMFYOS_PORT=8000"
if not defined COMFYOS_HOST set "COMFYOS_HOST=0.0.0.0"

echo.
echo  ComfyOS startet jetzt.
echo  Auf diesem Rechner:  http://127.0.0.1:%COMFYOS_PORT%
for /f "tokens=2 delims=:" %%I in ('ipconfig ^| findstr /R /C:"IPv4"') do (
  for /f "tokens=* delims= " %%J in ("%%I") do echo  Im Heimnetz:        http://%%J:%COMFYOS_PORT%
)
echo.
echo  Fragt die Windows-Firewall nach: Zugriff im PRIVATEN Netzwerk erlauben,
echo  sonst ist ComfyOS am Handy nicht erreichbar.
echo  Zum Beenden dieses Fenster schliessen oder STRG+C druecken.
echo.
start "" http://127.0.0.1:%COMFYOS_PORT%

:run
REM The whole block is read in before the start - so an update can replace this file
REM safely. Exit code 3 = restart after an update.
(
  ".venv\Scripts\python.exe" backend\run.py
  if errorlevel 3 if not errorlevel 4 goto restart
  goto end
)

:restart
echo.
echo  Neustart nach Update ...
".venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt
goto run

:error
echo.
echo Beim Einrichten ist ein Fehler aufgetreten.
echo Pruefe bitte deine Internetverbindung und versuche es erneut.
pause
:end
endlocal
