@echo off
setlocal
cd /d "%~dp0"
title Sindri - reparar bibliotecas
echo ==========================================
echo   Sindri - reparar bibliotecas
echo ==========================================
echo Pasta: %~dp0
echo.
if exist "libs\usar_conda.txt" (
    echo Usando o Anaconda: reinstalando as bibliotecas da pasta libs...
    del "libs\usar_conda.txt"
    if exist "libs" rmdir /s /q "libs"
)
if not exist ".venv\Scripts\python.exe" (
    echo Preparando de novo...
    call "%~dp0executar.bat"
    exit /b
)
echo Voltando as bibliotecas para as versoes normais do Sindri...
if exist ".venv\compat_tentado.txt" del ".venv\compat_tentado.txt"
".venv\Scripts\python.exe" -m pip install --upgrade --force-reinstall -r requirements.txt
echo.
echo Pronto. Abrindo o Sindri...
call "%~dp0executar.bat"
