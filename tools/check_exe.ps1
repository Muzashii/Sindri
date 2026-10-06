param([string]$Executable = 'dist/Sindri.exe', [switch]$CleanEnvironment = $true, [switch]$RedirectOutput)
$ErrorActionPreference = 'Stop'
$exePath = (Resolve-Path -LiteralPath $Executable).Path
$runDir = Join-Path $PWD ('docs/exe-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $runDir | Out-Null
$names = @('QT_QPA_PLATFORM', 'SINDRI_SETTINGS_FILE', 'SINDRI_DATA_DIR', 'LOCALAPPDATA', 'PATH', 'PYTHONPATH', 'PYTHONHOME')
$oldEnv = @{}
foreach ($name in $names) { $oldEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$launched = $null
$before = @(Get-Process Sindri -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
try {
    if ($CleanEnvironment) {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    }
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:SINDRI_SETTINGS_FILE = Join-Path $runDir 'settings.ini'
    $env:SINDRI_DATA_DIR = Join-Path $runDir 'data'
    $env:LOCALAPPDATA = Join-Path $runDir 'local'
    $startOptions = @{FilePath=$exePath; WindowStyle='Hidden'; PassThru=$true}
    if ($RedirectOutput) {
        $startOptions.RedirectStandardError = Join-Path $runDir 'stderr.txt'
        $startOptions.RedirectStandardOutput = Join-Path $runDir 'stdout.txt'
    }
    $launched = Start-Process @startOptions
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline -and -not $launched.HasExited -and -not (Test-Path -LiteralPath $env:SINDRI_SETTINGS_FILE)) {
        Start-Sleep -Seconds 1
        $launched.Refresh()
    }
    Start-Sleep -Seconds 3
    $launched.Refresh()
    $errorLog = Join-Path $env:LOCALAPPDATA 'Sindri/sindri_erro.log'
    $ready = (Test-Path -LiteralPath $env:SINDRI_SETTINGS_FILE) -and -not $launched.HasExited -and -not (Test-Path -LiteralPath $errorLog)
    $result = [ordered]@{
        executable = $exePath
        size_bytes = (Get-Item -LiteralPath $exePath).Length
        sha256 = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
        startup_ok = $ready
        verification = 'Inicialização offscreen, preferências isoladas, processo ativo e ausência de log de erro; não testa corte nem intranet real.'
    }
    $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir 'result.json') -Encoding utf8
    $result | ConvertTo-Json
    if (-not $ready) {
        if (Test-Path -LiteralPath $errorLog) { Get-Content -LiteralPath $errorLog }
        if ($RedirectOutput) { Get-Content -LiteralPath (Join-Path $runDir 'stderr.txt') }
        throw 'O executável não concluiu a verificação de inicialização.'
    }
} finally {
    if ($null -ne $launched) {
        $owned = @(Get-CimInstance Win32_Process -Filter "Name = 'Sindri.exe'" | Where-Object {
            $_.ExecutablePath -eq $exePath -and $_.ProcessId -notin $before -and
            ($_.ProcessId -eq $launched.Id -or $_.ParentProcessId -eq $launched.Id)
        })
        foreach ($child in $owned | Where-Object { $_.ProcessId -ne $launched.Id }) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
        if (-not $launched.WaitForExit(15000)) {
            Stop-Process -Id $launched.Id -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $oldEnv[$name], 'Process') }
}
