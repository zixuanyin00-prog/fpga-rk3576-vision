from pathlib import Path
import gc
import hashlib
import json
import sys
import time
from unittest.mock import patch
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.gpu import require_cuda, record_device
from core.store import write


def main():
    out = ROOT / "evidence/gpu_20261024" / ("smoke_" + time.strftime("%H%M%S"))
    out.mkdir(parents=True)
    device = require_cuda()
    report = dict(
        execution=record_device(out, device),
        synthetic_only=True,
        historical_weights_loaded=False,
    )
    torch.set_num_threads(2)
    with patch("torch.cuda.is_available", return_value=False):
        try:
            require_cuda()
        except RuntimeError:
            report["cpu_fallback_rejected"] = True
        else:
            raise AssertionError("CPU fallback permitted")
    frames = []
    for i in range(15):
        im = np.full((288, 512, 3), 32 + i, np.uint8)
        im[100:164, 120 + i : 216 + i] = 180
        path = out / f"{i}.rgb"
        raw = im.tobytes()
        path.write_bytes(raw)
        frames.append(
            dict(
                id=str(i),
                file=path.name,
                path=str(path),
                sha256=hashlib.sha256(raw).hexdigest(),
                role="train",
                objects=[dict(bbox=[120 + i, 100, 216 + i, 164], label=i % 3)],
                source="historical_reviewed_train",
            )
        )
    job = dict(
        root=str(out),
        output=str(out / "training"),
        seed=12,
        steps=2,
        initialization="random",
        initial=None,
        historical_replay=False,
        train=frames,
        validation=[],
        schema=dict(
            labels=["front_upright", "front_inverted", "back", "unreadable"],
            titles=["正面上", "正面下", "反面", "不清楚"],
            numbers=[],
        ),
        training_options=dict(batch_size=2, eval_every=2),
        manifest=dict(
            original_images=15,
            unique_source_frames=15,
            initialization=dict(historical_weights_used=False),
            batch="synthetic_gpu_plumbing_only",
        ),
    )
    train_out = Path(job["output"])
    train_out.mkdir()

    observed = []

    def check(module, args):
        tensors = [x for x in args if isinstance(x, torch.Tensor)]
        if tensors:
            observed.append((type(module).__name__, str(tensors[0].device)))

    from core.model import ResistorNet
    from worker import train

    handle = torch.nn.modules.module.register_module_forward_pre_hook(check)
    train(job, out, train_out)
    handle.remove()
    result = json.loads((train_out / "result.json").read_text(encoding="utf-8"))
    assert result["training_config"]["execution"]["device"] == "cuda:0"
    assert all(
        (train_out / name).is_file()
        for name in ["detector.pt", "detector.onnx", "locator.onnx"]
    )
    assert any(name == "ResistorNet" and dev == "cuda:0" for name, dev in observed)
    report["recognition_training_export_evaluation"] = dict(
        passed=True, steps=2, output=str(train_out)
    )
    print("PASS recognition training, GPU evaluation, ONNX export", flush=True)
    gc.collect()
    torch.cuda.empty_cache()
    torch.use_deterministic_algorithms(False)
    from core.box_model import ScratchBoxNet, targets, loss_for, predict
    from core.box_v2 import BoxNetV2, SpatialVerifier, PatchVerifier, locate

    for cls in (ScratchBoxNet, BoxNetV2):
        net = cls().to(device)
        x = torch.randn(2, 3, 288, 512, device=device)
        truth = targets(
            [dict(bbox=[120, 100, 216, 164])], stride=8 if cls is ScratchBoxNet else 4
        )
        loss = loss_for(net(x), [truth, truth])
        loss.backward()
        assert next(net.parameters()).grad.is_cuda and torch.isfinite(loss)
        predict(net.eval(), np.zeros((288, 512, 3), np.uint8))
        report[cls.__name__] = dict(
            backward_device=str(next(net.parameters()).grad.device),
            loss=float(loss.detach()),
        )
    for cls in (SpatialVerifier, PatchVerifier):
        verifier = cls().to(device)
        loss = torch.nn.functional.binary_cross_entropy_with_logits(
            verifier(torch.randn(2, 3, 64, 96, device=device)),
            torch.ones(2, device=device),
        )
        loss.backward()
        assert next(verifier.parameters()).grad.is_cuda
        with patch(
            "core.box_v2.annotation_predictions",
            return_value=[dict(bbox=[120, 100, 216, 164], objectness=0.9)],
        ):
            locate(net.eval(), verifier.eval(), np.zeros((288, 512, 3), np.uint8))
    report["box_detector_and_verifiers"] = True
    del net, verifier, x, loss
    gc.collect()
    torch.cuda.empty_cache()
    print("PASS scratch box detectors and verifiers on GPU", flush=True)

    from transformers import (
        AutoConfig,
        AutoProcessor,
        AutoModelForZeroShotObjectDetection,
    )
    from PIL import Image

    folder = ROOT / "box_foundation/models/grounding-dino-base"
    cfg = AutoConfig.from_pretrained(
        folder, local_files_only=True, trust_remote_code=False
    )
    model = (
        AutoModelForZeroShotObjectDetection.from_config(cfg, trust_remote_code=False)
        .to(device)
        .eval()
    )
    processor = AutoProcessor.from_pretrained(
        folder, local_files_only=True, trust_remote_code=False
    )
    inputs = processor(
        images=Image.new("RGB", (512, 288)),
        text="a chip resistor.",
        return_tensors="pt",
    ).to(device)
    with torch.inference_mode():
        outputs = model(**inputs)
    assert outputs.logits.is_cuda and outputs.pred_boxes.is_cuda
    processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        threshold=0.25,
        text_threshold=0.20,
        target_sizes=[(288, 512)],
    )
    report["grounding_dino"] = dict(
        random_initialization=True,
        output_device=str(outputs.logits.device),
        input_shape=list(inputs.pixel_values.shape),
        parameters=sum(p.numel() for p in model.parameters()),
    )
    report["passed"] = True
    write(out / "result.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
