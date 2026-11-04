$ErrorActionPreference = 'Stop'
$gpuRoot = $PSScriptRoot
$gpuPython = Join-Path $gpuRoot '.venv-gpu\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $gpuPython)) {
    python -m venv --system-site-packages (Join-Path $gpuRoot '.venv-gpu')
    if ($LASTEXITCODE -ne 0) { throw 'GPU environment creation failed' }
}
& $gpuPython -m pip install 'torch==2.11.0+cu128' 'torchvision==0.26.0+cu128' --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed' }
& $gpuPython -m pip install -r (Join-Path $gpuRoot 'requirements.lock.txt') 'transformers==4.57.6'
if ($LASTEXITCODE -ne 0) { throw 'Worker dependencies installation failed' }
Push-Location $gpuRoot
try { & $gpuPython -c "from core.gpu import require_cuda,device_info; print(device_info(require_cuda()))" }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw 'CUDA verification failed' }
