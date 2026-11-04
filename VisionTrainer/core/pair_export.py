from pathlib import Path
import hashlib
import json


def validate_job(job):
    manifest = job.get("manifest", {})
    if (
        job.get("initialization") != "random"
        or job.get("initial") is not None
        or job.get("historical_replay") is not False
        or not manifest.get("original_images")
        or manifest.get("unique_original_images", manifest.get("unique_source_frames"))
        != manifest.get("original_images")
        or manifest.get("initialization", {}).get("historical_weights_used")
        is not False
    ):
        raise ValueError("配套导出只能使用本次显式随机初始化、去重原图训练的网络")


def export_from_trained_net(net, job, root, out, config):
    validate_job(job)

    import copy
    import numpy as np
    import torch
    from torch import nn
    from .data import tensor

    class Locator(nn.Module):
        def __init__(self, original):
            super().__init__()
            self.net = copy.deepcopy(original)
            final = self.net.head[-1]
            head = nn.Conv2d(final.in_channels, 5, 1).to(
                device=final.weight.device, dtype=final.weight.dtype
            )
            with torch.no_grad():
                head.weight.copy_(final.weight[:5])
                head.bias.copy_(final.bias[:5])
            self.net.head[-1] = head

        def forward(self, x):
            return self.net.head(self.net.encode(x)).sigmoid()

    locator = Locator(net).eval()
    parity = []
    for frame in job["train"][:3]:
        raw = (root / frame["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != frame["sha256"]:
            raise ValueError("导出同源原图哈希失配")
        x = tensor(np.frombuffer(raw, np.uint8).reshape(288, 512, 3))[None].to(
            next(net.parameters()).device
        )
        with torch.no_grad():
            error = float((net(x)[0] - locator(x)).abs().max())
        if error > 1e-6:
            raise ValueError("位置分支与精判网络位置输出不一致")
        parity.append(dict(frame=frame["id"], max_absolute_error=error))
    if not parity:
        raise ValueError("无本批训练原图，禁止导出")
    torch.onnx.export(
        locator.cpu(),
        torch.zeros(1, 3, 288, 512),
        str(out / "locator.onnx"),
        input_names=["images"],
        output_names=["positions"],
        opset_version=12,
        dynamo=False,
    )
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    coarse = dict(
        config,
        labels=["target"],
        role="positions_objectness_only",
        input_domain="fpga_native_512x288",
        parent_checkpoint_sha256=sha(out / "detector.pt"),
        parent_fine_onnx_sha256=sha(out / "detector.onnx"),
        coarse_onnx_sha256=sha(out / "locator.onnx"),
        batch=job["manifest"]["batch"],
        source_originals=job["manifest"]["original_images"],
        derived_only_from_this_new_run=True,
        pc_branch_parity=parity,
        training_initialization="random",
        board_tested=False,
    )
    (out / "locator_config.json").write_text(
        json.dumps(coarse, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return coarse
