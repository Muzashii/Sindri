@echo off
setlocal
cd /d "%~dp0"
title Sindri - reparar bibliotecas
echo ==========================================
echo   Sindri - reparar bibliotecas
echo ==========================================
echo Pasta: %~dp0
echo.
if not exist ".venv\Scripts\python.exe" (
    echo Ainda nao ha instalacao nesta pasta. Rode o executar.bat.
    pause
    exit /b 1
)
echo Voltando as bibliotecas para as versoes normais do Sindri...
if exist ".venv\compat_tentado.txt" del ".venv\compat_tentado.txt"
if exist "libs\usar_conda.txt" del "libs\usar_conda.txt"
".venv\Scripts\python.exe" -m pip install --upgrade --force-reinstall -r requirements.txt
echo.
echo Pronto. Abrindo o Sindri...
call "%~dp0executar.bat"
