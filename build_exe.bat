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
if not exist ".venv-build\Scripts\python.exe" (
    %PY% -m venv .venv-build
    if errorlevel 1 goto :fail
)
set "VPY=%~dp0.venv-build\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :fail

"%VPY%" -m app.build_windows
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
