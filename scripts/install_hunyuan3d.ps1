# Installs Hunyuan3D-2 for the 3D studio rung (photo -> 3D model), the exact versions that work (2026-11-13):
# the repo at a pinned commit, Python 3.11 venv, PyTorch CUDA 12.8 (RTX 50-series), then the frozen package list.
# transformers 5.x / diffusers 0.4x / huggingface_hub 0.36 break the Hunyuan3D-2 weights: the lock file pins the set that loads
# (installed --no-deps: it is the complete working environment; the demo UI (gradio) is not used by Video Studio).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install_hunyuan3d.ps1  [-Dest F:\code\hunyuan3d]
param([string]$Dest = "F:\code\hunyuan3d")
$ErrorActionPreference = "Continue"   # git / uv write progress to stderr: each step is checked by its exit code instead
function Step($what) { if ($LASTEXITCODE -ne 0) { Write-Error "install_hunyuan3d: $what failed (exit $LASTEXITCODE)"; exit 1 } }
$commit = "f8db63096c8282cb27354314d896feba5ba6ff8a"
$lock = Join-Path $PSScriptRoot "..\app\studio\hy3d_requirements.lock.txt"
if (-not (Test-Path $Dest)) { git clone -q https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git $Dest 2>&1 | Out-Null; Step "clone" }
git -C $Dest fetch -q --depth 1 origin $commit 2>&1 | Out-Null; git -C $Dest checkout -q $commit 2>&1 | Out-Null; Step "checkout $commit"
uv venv -q --python 3.11 (Join-Path $Dest "venv"); Step "venv"
$py = Join-Path $Dest "venv\Scripts\python.exe"
uv pip install --python $py torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128; Step "torch"
uv pip install --python $py --no-deps -r $lock --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match; Step "packages"
uv pip install --python $py --no-deps -e $Dest; Step "hy3dgen"
& $py -c "import torch; from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline; print('Hunyuan3D-2 ready, CUDA:', torch.cuda.is_available())"
# The model weights (tencent/Hunyuan3D-2, Hunyuan3D-2mv, ~8 GB) download on first use into the Hugging Face cache.
