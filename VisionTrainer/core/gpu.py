import os
from pathlib import Path

GPU_PYTHON = (
    Path(__file__).resolve().parents[1] / ".venv-gpu" / "Scripts" / "python.exe"
)


def worker_python():
    if not GPU_PYTHON.is_file():
        raise RuntimeError("GPU 运行环境缺失，请先运行 setup_gpu.ps1；禁止回退 CPU")
    return str(GPU_PYTHON)


def require_cuda():

    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "画框与训练必须使用 NVIDIA GPU；当前 CUDA 不可用，禁止回退 CPU。请运行 setup_gpu.ps1 并检查显卡驱动。"
        )
    device = torch.device("cuda:0")
    try:

        probe = torch.ones(2, 2, device=device)
        (probe @ probe).sum().item()
        torch.cuda.synchronize(device)
    except Exception as exc:
        raise RuntimeError("GPU 运算检查失败，禁止回退 CPU：" + str(exc)) from exc
    return device


def device_info(device):
    import torch

    return dict(
        device=str(device),
        gpu_name=torch.cuda.get_device_name(device),
        torch=torch.__version__,
        cuda=torch.version.cuda,
        cpu_fallback=False,
    )


def record_device(out, device):
    from .store import write

    info = device_info(device)
    write(Path(out) / "device.json", info)
    print("GPU: " + str(info), flush=True)
    return info
