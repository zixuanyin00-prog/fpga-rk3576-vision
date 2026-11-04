from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
import numpy as np
import torch
from torch.nn import functional as F
from core.model import ResistorNet
from core import batch15
from core.data import augment, targets, tensor
from core.decoder import decode, iou
from core.store import ROOT, read, write, now
from core.gpu import require_cuda, record_device


def load_net(path, labels, source_labels=None):
    net = ResistorNet(states=len(labels)).eval()
    weights = torch.load(path, map_location="cpu", weights_only=True)
    if source_labels is not None:

        state = net.state_dict()
        for key in ("head.8.weight", "head.8.bias"):
            original = weights[key]
            target = state[key].clone()
            target[:5] = original[:5]
            for i, name in enumerate(labels):
                origin = name if name in source_labels else name.split(":")[0]
                if origin in source_labels:
                    target[5 + i] = original[5 + source_labels.index(origin)]
            weights[key] = target
    net.load_state_dict(weights)
    return net.to(require_cuda())


def predictions(net, raw, config, complete=True):
    with torch.no_grad():
        outputs = [
            v.cpu().numpy()
            for v in net(tensor(raw)[None].to(next(net.parameters()).device))
        ]
    found = decode(
        outputs,
        object_threshold=config["object_threshold"],
        state_threshold=config["state_threshold"],
        labels=config["labels"],
    )
    if complete:
        margin = config.get("complete_body_margin", 8)
        found = [
            d
            for d in found
            if margin <= d["bbox"][0] < d["bbox"][2] <= 512 - margin
            and margin <= d["bbox"][1] < d["bbox"][3] <= 288 - margin
        ]
    return found


def evaluate(net, snapshot, root, config):
    from core.training import score_frames

    outputs = []
    for frame in snapshot:
        raw = (root / frame["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != frame["sha256"]:
            raise ValueError("Source image hash mismatch")
        outputs.append(
            predictions(net, np.frombuffer(raw, np.uint8).reshape(288, 512, 3), config)
        )
    result = score_frames(
        snapshot, outputs, config, complete=False, overlap=0.5, decoded=True
    )
    result["complete_body_filter"] = True
    result["backend"] = "PC PyTorch " + str(next(net.parameters()).device) + " float32"
    return result


def train(job, root, out):
    labels = job["schema"]["labels"]
    torch.manual_seed(job["seed"])
    rng = np.random.default_rng(job["seed"])
    if (
        job.get("initialization") != "random"
        or job.get("initial") is not None
        or job.get("historical_replay") is not False
    ):
        raise ValueError("仅允许本次显式随机初始化且禁止历史回放")
    for rel in (
        "worker.py",
        "core/store.py",
        "core/batch15.py",
        "core/pair_export.py",
        "convert_rknn.py",
    ):
        dest = out / "training_source" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((Path(__file__).resolve().parent / rel).read_bytes())
    manifest = dict(job["manifest"])
    manifest["training_started_beijing"] = batch15.beijing_now()
    write(out / "training_manifest.json", manifest)
    config = dict(
        labels=labels,
        titles=job["schema"]["titles"],
        numbers=job["schema"]["numbers"],
        input_size=[512, 288],
        mean=[123.675, 116.28, 103.53],
        std=[58.395, 57.12, 57.375],
        object_threshold=0.15,
        state_threshold=0.85,
        window_model_id="trainer-" + out.name,
        native_roi=job.get("native_roi"),
        complete_body_margin=8,
        body_center_y_local=[8, 280],
        direction_convention="Direction follows manually reviewed raw-source orientation",
        cpu_pixel_processing=False,
    )
    if any(f.get("input_transform") for f in job["train"]):
        config.update(
            input_transform="stretch_bilinear",
            cpu_pixel_processing=True,
            bbox_coordinates="model_input_512x288; scale then add native rectangle origin",
        )
    from core.feedback import class_ids

    config.update(
        class_ids=class_ids(job["schema"]),
        class_mapping=job["schema"],
        class_count=len(labels),
    )
    from core.training import train_scene

    net, history, resolved = train_scene(job, root, out, config)
    sources = [f["id"] for f in job["train"]]
    torch.save(net.state_dict(), out / "detector.pt")
    write(out / "config.json", config)
    write(out / "progress.json", dict(stage="exporting"))

    import copy

    torch.onnx.export(
        copy.deepcopy(net).cpu(),
        torch.zeros(1, 3, 288, 512),
        str(out / "detector.onnx"),
        input_names=["image"],
        output_names=["probability_box", "state_logits"],
        opset_version=12,
        dynamo=False,
    )
    from core.pair_export import export_from_trained_net

    export_from_trained_net(net, job, root, out, config)
    result = dict(
        created=now(),
        parameters=sum(p.numel() for p in net.parameters()),
        initial_sha256=None,
        initialization="random",
        training_started_beijing=manifest["training_started_beijing"],
        history=history,
        source_ids=sources,
        validation_used_for_gradient=False,
        test_used_for_gradient=False,
        accepted=False,
        board_tested=False,
    )
    result["training_config"] = resolved
    result["selection"] = read(out / "selection.json")
    result["training"] = evaluate(net, job["train"], root, config)
    result["independent_test"] = dict(
        available=False,
        reason="No held-out manually reviewed independent passage set used in training",
        accepted=False,
    )
    if job["validation"]:
        result["validation"] = evaluate(net, job["validation"], root, config)
    else:
        result["validation"] = None
    result["artifacts"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir()
        if p.suffix in (".pt", ".onnx")
        or p.name in ("config.json", "locator_config.json")
    }
    manifest.update(
        training_completed_beijing=batch15.beijing_now(),
        output_hashes=result["artifacts"],
        training_method=resolved,
        checkpoint_selection=result["selection"],
    )
    write(out / "training_manifest.json", manifest)
    contract = batch15.output_contract(labels, result["artifacts"])
    contract.update(
        kind="same_run_position_branch_and_joint_fine",
        separate_coarse_fine_pair_available=True,
        deployable_as_existing_two_model_pair=False,
        pair_exported_pc=True,
        deployment_requires_current_user_model_time_selection=True,
        coarse=dict(
            onnx="locator.onnx", config="locator_config.json", output="positions"
        ),
        fine=dict(
            onnx="detector.onnx",
            config="config.json",
            outputs=["probability_box", "state_logits"],
        ),
        runtime_and_rknn_pair_validation_pending=True,
    )
    write(out / "output_contract.json", contract)
    write(out / "result.json", result)
    write(
        out / "progress.json", dict(stage="complete", message="模型已生成，待独立验证")
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["train", "evaluate"])
    p.add_argument("job")
    a = p.parse_args()
    job = read(a.job)
    root = Path(job["root"])
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    if a.mode == "train":
        train(job, root, out)
        return
    config = read(job["config"])
    net = load_net(job["model"], config["labels"])
    record_device(out, next(net.parameters()).device)
    write(
        out / ("evaluation_" + job["role"] + ".json"),
        evaluate(net, job["frames"], root, config),
    )


if __name__ == "__main__":
    main()
