param([switch]$Restart)
$ErrorActionPreference = 'Stop'
$consolePython = Join-Path $PSScriptRoot '.venv-gpu\Scripts\python.exe'
$consoleLauncher = Join-Path $PSScriptRoot 'launch_console.py'
if (-not (Test-Path -LiteralPath $consolePython)) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show('缺少控制台 .venv-gpu 环境。', '原点控制台', 'OK', 'Error') | Out-Null
    exit 1
}
if ($Restart) {
    & $consolePython $consoleLauncher --restart
} else {
    & $consolePython $consoleLauncher
}
exit $LASTEXITCODE