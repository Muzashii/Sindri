@echo off
setlocal
cd /d "%~dp0"
title Sindri - gerar executavel
REM Gera o executavel unico dist\Sindri.exe (Windows 10/11)

set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ERRO] Python 3.11+ nao encontrado. Instale em https://www.python.org/downloads/
    echo marcando "Add python.exe to PATH".
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

"%VPY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name Sindri --icon assets\sindri.ico ^
  --collect-data ezdxf ^
  --collect-submodules ezdxf ^
  --hidden-import pyclipper ^
  --hidden-import shapely ^
  --exclude-module tkinter ^
  --exclude-module matplotlib ^
  sindri.py
if errorlevel 1 goto :fail

echo.
echo Pronto: dist\Sindri.exe
pause
exit /b 0

:fail
echo.
echo [ERRO] Falha ao gerar o executavel. Leia as mensagens acima.
pause
exit /b 1
