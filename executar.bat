@echo off
setlocal
cd /d "%~dp0"
title Sindri
echo ==========================================
echo   Sindri
echo ==========================================
echo Pasta: %~dp0
echo.
set "CHECK=from app.runtime_check import check; check()"
set "LOG=%TEMP%\sindri_check.txt"

REM Se ja ficou combinado usar o Anaconda (o Windows nao bloqueia o numpy dele), vai direto
if exist "libs\usar_conda.txt" goto :run_conda

REM ---- 1. Procurar o Python (py launcher primeiro, depois python) ----
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto :try_conda
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do echo Python encontrado: %%v

REM ---- 2. Ambiente virtual ----
if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual em .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
set "VPY=%~dp0.venv\Scripts\python.exe"
set "VPYW=%~dp0.venv\Scripts\pythonw.exe"

REM ---- 3. Bibliotecas: testa de verdade e conserta sozinho ----
echo Verificando bibliotecas...
"%VPY%" -c "%CHECK%" >"%LOG%" 2>&1
if not errorlevel 1 goto :venv_ok
call :is_blocked && goto :blocked_retry

echo.
echo Instalando bibliotecas - na primeira vez pode levar alguns minutos...
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt
"%VPY%" -c "%CHECK%" >"%LOG%" 2>&1
if not errorlevel 1 goto :venv_ok
call :is_blocked && goto :blocked_retry

echo.
echo Bibliotecas danificadas - reinstalando...
"%VPY%" -m pip install --force-reinstall --no-cache-dir -r requirements.txt
"%VPY%" -c "%CHECK%" >"%LOG%" 2>&1
if not errorlevel 1 goto :venv_ok
call :is_blocked && goto :blocked_retry
goto :fail_check

:blocked_retry
REM O bloqueio do Windows e intermitente: tenta algumas vezes antes de desistir.
set /a TRIES=0
:retry_loop
set /a TRIES+=1
if %TRIES% GTR 3 goto :try_conda
echo O Windows bloqueou uma biblioteca. Nova tentativa %TRIES% de 3 em 5 segundos...
timeout /t 5 /nobreak >nul
"%VPY%" -c "%CHECK%" >"%LOG%" 2>&1
if not errorlevel 1 goto :venv_ok
goto :retry_loop

:venv_ok
REM ---- 4. Atalho na area de trabalho e no menu Iniciar (so na primeira vez) ----
set "DN_TARGET=%VPYW%"
set "DN_ARGS="%~dp0sindri.py""
set "DN_STYLE=1"
if not exist ".venv\atalho_sindri.txt" (
    call :shortcut
    echo tudo certo> ".venv\atalho_sindri.txt"
)
echo Abrindo o Sindri...
start "" "%VPYW%" "%~dp0sindri.py" %*
exit /b 0

REM ======================= Plano B: Anaconda =======================
:try_conda
set "CDIR="
for %%D in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3") do (
    if not defined CDIR if exist "%%~D\python.exe" set "CDIR=%%~D"
)
if not defined CDIR goto :blocked
set "CPY=%CDIR%\python.exe"
"%CPY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto :blocked
echo.
echo Tentando usar o Anaconda ja instalado neste computador...
"%CPY%" -c "import numpy" >"%LOG%" 2>&1
if errorlevel 1 goto :blocked
REM numpy (e o que mais ja existir) vem do Anaconda; o resto vai para a pasta libs
set "PYTHONPATH=%~dp0libs"
"%CPY%" -c "%CHECK%" >"%LOG%" 2>&1
if not errorlevel 1 goto :conda_ok
echo Instalando o que falta na pasta libs - na primeira vez pode levar alguns minutos...
"%CPY%" -m pip install --upgrade --target "%~dp0libs" --no-deps pyclipper ezdxf pypdf pyparsing fonttools typing_extensions PySide6 PySide6_Essentials PySide6_Addons shiboken6
"%CPY%" -c "import shapely.geometry; import shapely; assert int(shapely.__version__[0]) >= 2" >nul 2>nul
if errorlevel 1 "%CPY%" -m pip install --upgrade --target "%~dp0libs" --no-deps shapely
"%CPY%" -c "%CHECK%" >"%LOG%" 2>&1
if errorlevel 1 goto :blocked
:conda_ok
if not exist "libs" mkdir "libs"
> "libs\usar_conda.txt" echo %CDIR%
set "DN_TARGET=%~dp0executar.bat"
set "DN_ARGS="
set "DN_STYLE=7"
call :shortcut
echo Funcionou com o Anaconda.

:run_conda
set "PYTHONPATH=%~dp0libs"
if not defined CDIR set /p CDIR=<"libs\usar_conda.txt"
if "%CDIR%"=="ok" set "CDIR=%USERPROFILE%\anaconda3"
set "CPYW=%CDIR%\pythonw.exe"
if not exist "%CPYW%" (
    del "libs\usar_conda.txt" >nul 2>nul
    goto :blocked
)
echo Abrindo o Sindri...
start "" "%CPYW%" "%~dp0sindri.py" %*
exit /b 0

REM ======================= sub-rotinas =======================
:is_blocked
findstr /i /c:"Controle de Aplicativo" /c:"Application Control" "%LOG%" >nul
exit /b %errorlevel%

:shortcut
set "DN_DIR=%~dp0"
set "DN_ICON=%~dp0assets\sindri.ico"
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'Sindri.lnk')); $s.TargetPath=$env:DN_TARGET; $s.Arguments=$env:DN_ARGS; $s.WorkingDirectory=$env:DN_DIR; $s.IconLocation=$env:DN_ICON; $s.WindowStyle=[int]$env:DN_STYLE; $s.Description='Sindri - encaixe automatico de pecas DXF'; $s.Save(); $o=Join-Path $d 'DXF Nest.lnk'; if(Test-Path $o){ Remove-Item $o } }" >nul 2>nul
if not errorlevel 1 echo Atalho "Sindri" criado na area de trabalho e no menu Iniciar.
exit /b 0

:blocked
echo.
if not defined VPY if not defined CDIR goto :nopython
echo ==========================================================================
echo [BLOQUEADO PELO WINDOWS] O Windows impediu o Python de carregar uma
echo biblioteca do programa ("Uma politica de Controle de Aplicativo bloqueou
echo este arquivo"). Detalhe:
findstr /i /c:"DLL load failed" /c:".pyd" /c:".dll" /c:"Error" "%LOG%"
echo.
echo  - Computador pessoal: e o "Controle Inteligente de Aplicativos" do Windows 11.
echo    Seguranca do Windows ^> Controle de aplicativos e do navegador ^>
echo    Configuracoes do Controle Inteligente de Aplicativos ^> Desativado.
echo    ATENCAO: depois de desligado, so volta reinstalando o Windows.
echo  - Computador da instituicao/empresa: a politica e da TI. Peca para liberar
echo    a pasta do Sindri ^(ou o Python^) na politica de controle de aplicativos.
echo ==========================================================================
echo.
pause
exit /b 1

:fail_check
echo.
echo [ERRO] As bibliotecas nao funcionaram. Detalhes:
type "%LOG%"
echo.
echo Rode o reparar.bat ou apague a pasta .venv e rode este arquivo de novo.
pause
exit /b 1

:nopython
echo.
echo [ERRO] Python 3.11 ou mais novo nao foi encontrado.
echo   1. Baixe em https://www.python.org/downloads/
echo   2. Na instalacao, MARQUE a opcao "Add python.exe to PATH".
echo   3. Rode este arquivo de novo.
echo.
echo Obs.: se ao digitar "python" abre a Microsoft Store, desative em
echo Configuracoes ^> Aplicativos ^> Aliases de execucao de aplicativo ^> python.exe
echo.
pause
exit /b 1

:fail
echo.
echo [ERRO] Algo deu errado. Leia as mensagens acima.
echo.
pause
exit /b 1
