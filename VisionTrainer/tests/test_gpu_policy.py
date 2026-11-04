from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from core.gpu import require_cuda, worker_python
from service import Service


class GPUPolicyTests(unittest.TestCase):
    def test_no_cpu_fallback(self):
        with patch("torch.cuda.is_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "禁止回退 CPU"):
                require_cuda()

    def test_missing_runtime_fails(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "core.gpu.GPU_PYTHON", Path(temp) / "missing.exe"
        ):
            with self.assertRaisesRegex(RuntimeError, "GPU 运行环境缺失"):
                worker_python()

    def test_all_model_workers_override_cpu_interpreter(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service()
            for worker in (
                "worker.py",
                "foundation_worker.py",
                "box_worker.py",
                "pc_test_worker.py",
            ):
                process = MagicMock()
                process.wait.return_value = 0
                with patch(
                    "service.worker_python", return_value="gpu-python.exe"
                ), patch("service.subprocess.Popen", return_value=process) as popen:
                    service.subprocess(
                        "test", Path(temp) / "job.json", worker, python="cpu-python.exe"
                    )
                    self.assertEqual(popen.call_args.args[0][0], "gpu-python.exe")


if __name__ == "__main__":
    unittest.main()
