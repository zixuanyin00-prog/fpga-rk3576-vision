from pathlib import Path
import argparse
import copy
import time
import numpy as np
import torch
from console_store import read, write, sha, now, image, windows
from core.gpu import require_cuda, record_device
from core.decoder import decode, iou


def merge(rows):
    kept = []
    for row in sorted(rows, key=lambda d: d.get("objectness", 0), reverse=True):
        if all(iou(row["bbox"], q["bbox"]) < 0.35 for q in kept):
            kept.append(row)
    return kept


def train(job, out, device):
    from core.training import train_scene, prepare_native_scene_job
    from core.data import tensor
    from core.model import ResistorNet

    config = dict(
        labels=job["schema"]["labels"],
        titles=job["schema"]["titles"],
        class_ids=job["schema"]["labels"],
        class_mapping=job["schema"],
        input_size=[512, 288],
        input_transform="native_tiles_v1",
        mean=[123.675, 116.28, 103.53],
        std=[58.395, 57.12, 57.375],
        object_threshold=0.15,
        state_threshold=0.85,
        complete_body_margin=0,
        window_model_id="native-" + out.name,
        pixel_policy="native_crop_only_v1",
        window_stride=[256, 144],
        bbox_coordinates="native_tile_xyxy_plus_origin",
    )
    job = prepare_native_scene_job(job)
    manifest = job["manifest"]
    manifest["training_started_beijing"] = now()
    write(out / "training_manifest.json", manifest)
    options = dict(
        method="native_scene_v2",
        augmentation=True,
        translation_x=48,
        state_ohem=True,
        eval_every=100,
    )
    options.update(job.get("training_options", {}))
    job["training_options"] = options
    write(out / "resolved_training_job.json", job)
    net, history, resolved = train_scene(job, Path(job["root"]), out, config)
    if job["manifest"]["scene"]["profile"].get("image_processing"):
        config["image_processing"] = job["manifest"]["scene"]["profile"][
            "image_processing"
        ]
    torch.save(net.state_dict(), out / "detector.pt")
    write(out / "config.json", config)
    write(
        out / "progress.json",
        dict(stage="exporting", message="导出ONNX；数值执行仍在GPU"),
    )
    dummy = torch.zeros(1, 3, 288, 512, device=device)
    torch.onnx.export(
        net,
        dummy,
        str(out / "detector.onnx"),
        input_names=["image"],
        output_names=["probability_box", "state_logits"],
        opset_version=12,
        dynamo=False,
    )

    class PositionBranch(torch.nn.Module):
        def __init__(self, source):
            super().__init__()
            self.source = source

        def forward(self, x):
            return self.source(x)[0]

    torch.onnx.export(
        PositionBranch(net).eval(),
        dummy,
        str(out / "locator.onnx"),
        input_names=["image"],
        output_names=["positions"],
        opset_version=12,
        dynamo=False,
    )
    hashes = {
        p.name: sha(p.read_bytes())
        for p in out.iterdir()
        if p.suffix in (".pt", ".onnx") or p.name == "config.json"
    }
    manifest.update(
        training_completed_beijing=now(),
        output_hashes=hashes,
        training_method=resolved,
        board_tested=False,
        accepted=False,
    )
    write(out / "training_manifest.json", manifest)
    write(
        out / "result.json",
        dict(
            history=history,
            training_config=resolved,
            accepted=False,
            board_tested=False,
        ),
    )
    write(
        out / "progress.json", dict(stage="complete", message="GPU训练完成，等待转换")
    )


def helper_train(job, out, device):
    from core.box_model import ScratchBoxNet, tensor, targets, loss_for, ARCHITECTURE

    torch.manual_seed(357626)
    rng = np.random.default_rng(357626)
    net = ScratchBoxNet().to(device)
    optimizer = torch.optim.AdamW(net.parameters(), lr=0.001, weight_decay=0.0001)
    samples = []
    for frame in job["train"]:
        raw = (Path(job["root"]) / frame["file"]).read_bytes()
        if sha(raw) != frame["sha256"]:
            raise ValueError("画框训练原图哈希失配")
        pixels = np.frombuffer(raw, np.uint8).reshape(288, 512, 3)
        obs = [dict(bbox=o["bbox"], ignore=o["label"] < 0) for o in frame["objects"]]
        samples.append((tensor(pixels), targets(obs)))
    started = now()
    for step in range(job["steps"]):
        batch = [samples[i] for i in rng.choice(len(samples), 4)]
        z = net(torch.stack([v[0] for v in batch]).to(device))
        loss = loss_for(z, [v[1] for v in batch])
        if not torch.isfinite(loss):
            raise ValueError("画框模型损失非有限值")
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 5)
        optimizer.step()
        if step % 10 == 0:
            write(
                out / "progress.json",
                dict(
                    step=step + 1,
                    steps=job["steps"],
                    stage="training",
                    loss=float(loss.detach()),
                ),
            )
    torch.save(net.eval().state_dict(), out / "boxes.pt")
    write(
        out / "result.json",
        dict(
            architecture=ARCHITECTURE,
            initialization="random",
            pretrained_weights_used=False,
            recognition_checkpoint_used=False,
            training_started_beijing=started,
            created=now(),
            sha256=sha((out / "boxes.pt").read_bytes()),
            scene=job["manifest"]["scene"],
            pixel_policy="native_crop_only_v1",
            native_detector_only=True,
        ),
    )


def infer(job, out, device, helper=False):
    from core.data import tensor

    path = Path(job["model"])
    if sha(path.read_bytes()) != job["selected"]["sha256"]:
        raise ValueError("本次明确选择的模型文件哈希改变")
    if helper:
        from core.box_model import ScratchBoxNet, predict, ARCHITECTURE
        from core.box_v2 import BoxNetV2, ARCHITECTURE as V2

        meta = read(path.parent / "result.json")
        if (
            meta.get("initialization") != "random"
            or meta.get("pretrained_weights_used") is not False
            or meta.get("architecture") not in (ARCHITECTURE, V2)
        ):
            raise ValueError("只能使用独立随机初始化的本地画框模型")
        weights = torch.load(path, map_location=device, weights_only=True)
        net = BoxNetV2() if meta["architecture"] == V2 else ScratchBoxNet()
        net.load_state_dict(
            weights["detector"] if meta["architecture"] == V2 else weights
        )
        net.to(device).eval()
    else:
        from core.model import ResistorNet

        config = read(job["config"])
        if config.get("input_transform") != "native_tiles_v1":
            raise ValueError("此控制台只测试本次原生裁剪模型")
        net = ResistorNet(states=len(config["labels"])).to(device).eval()
        net.load_state_dict(torch.load(path, map_location=device, weights_only=True))
    results = []
    with torch.inference_mode():
        for n, f in enumerate(job["frames"]):
            raw = (Path(job["root"]) / f["file"]).read_bytes()
            w, h = f["size"]
            if len(raw) != w * h * 3 or sha(raw) != f["sha256"]:
                raise ValueError("新图像完整性校验失败")
            full = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
            rows = []
            for x, y, tw, th in windows(w, h):
                crop = full[y : y + th, x : x + tw].copy()
                if helper:
                    found = predict(net, crop, threshold=0.35)
                else:
                    outputs = [
                        v.cpu().numpy() for v in net(tensor(crop)[None].to(device))
                    ]
                    found = decode(
                        outputs,
                        labels=config["labels"],
                        object_threshold=config["object_threshold"],
                        state_threshold=config["state_threshold"],
                    )
                for d in found:
                    a, b, c, e = d["bbox"]
                    from native_windows import accepts_tile_box

                    if not accepts_tile_box(d["bbox"], (x, y, tw, th), (0, 0, w, h)):
                        continue
                    rows.append(dict(d, bbox=[a + x, b + y, c + x, e + y]))
            results.append(
                dict(
                    frame=f["id"],
                    source_frame_id=f["source_frame_id"],
                    sha256=f["sha256"],
                    detections=merge(rows),
                )
            )
            write(
                out / "progress.json",
                dict(stage="inference", step=n + 1, steps=len(job["frames"])),
            )
    write(
        out / "predictions.json",
        dict(
            rows=results,
            backend="CUDA",
            selected=job["selected"],
            pixel_policy="native_crop_only_v1",
            helper_detector_only=helper,
            accuracy=None,
            accuracy_note="由用户核对新图，未标注不能计算准确率",
        ),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["train", "helper_train", "infer", "assist"])
    parser.add_argument("job")
    args = parser.parse_args()
    job = read(args.job)
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    device = require_cuda()
    record_device(out, device)
    if args.mode == "train":
        train(job, out, device)
    elif args.mode == "helper_train":
        helper_train(job, out, device)
    else:
        infer(job, out, device, args.mode == "assist")


if __name__ == "__main__":
    main()
