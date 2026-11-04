from pathlib import Path
import argparse
import hashlib
import time
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont
from core import store as db
from core.data import tensor
from core.decoder import decode
from worker import load_net
from core.gpu import record_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["infer"])
    parser.add_argument("job")
    a = parser.parse_args()
    job = db.read(a.job)
    config = db.read(job["config"])
    out = Path(job["output"])
    root = Path(job["root"])
    selected = job["selected_model"]
    if (
        hashlib.sha256(Path(job["model"]).read_bytes()).hexdigest()
        != selected["model_sha256"]
    ):
        raise ValueError("识别模型与本次选型不一致")
    torch.set_num_threads(2)
    net = load_net(job["model"], config["labels"])
    rows = []
    execution = record_device(out, next(net.parameters()).device)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    for i, frame in enumerate(job["frames"]):
        raw = (root / frame["file"]).read_bytes()
        if (
            len(raw) != 512 * 288 * 3
            or hashlib.sha256(raw).hexdigest() != frame["sha256"]
        ):
            raise ValueError("测试原图校验失败")
        started = time.monotonic()
        with torch.inference_mode():
            outputs = [
                x.cpu().numpy()
                for x in net(
                    tensor(np.frombuffer(raw, np.uint8).reshape(288, 512, 3))[None].to(
                        next(net.parameters()).device
                    )
                )
            ]
        found = decode(
            outputs,
            object_threshold=config["object_threshold"],
            state_threshold=config["state_threshold"],
            labels=config["labels"],
        )
        raw_detections = found
        margin = config.get("complete_body_margin", 8)
        found = [
            x
            for x in found
            if margin <= x["bbox"][0] < x["bbox"][2] <= 512 - margin
            and margin <= x["bbox"][1] < x["bbox"][3] <= 288 - margin
        ]
        seconds = time.monotonic() - started
        image = Image.new("RGB", (1024, 624), "#12251e")
        image.paste(
            Image.frombytes("RGB", (512, 288), raw).resize((1024, 576)), (0, 48)
        )
        draw = ImageDraw.Draw(image)
        draw.text(
            (12, 10),
            f'PC 推理 {i+1}/10 · 源帧 {frame["source_frame_id"]} · 检出 {len(found)} 个',
            font=font,
            fill="white",
        )
        if not found:
            draw.text((16, 64), "未检出目标（0）", font=font, fill="#ffda68")
        for d in found:
            x, y, x1, y1 = [v * 2 for v in d["bbox"]]
            draw.rectangle((x, y + 48, x1, y1 + 48), outline="#1bf0a4", width=3)
            label = d["label"]
            name = (
                config.get("titles", config["labels"])[config["labels"].index(label)]
                if label in config["labels"]
                else "不确定"
            )
            text = f'{name} {d["confidence"]:.1%}'
            draw.text(
                (x, max(48, y + 24)),
                text,
                font=small,
                fill="#1bf0a4",
                stroke_width=1,
                stroke_fill="#12251e",
            )
        filename = f"prediction_{i:02}.png"
        image.save(out / filename)
        rows.append(
            dict(
                index=i,
                frame_id=frame["id"],
                source_frame_id=frame["source_frame_id"],
                sha256=frame["sha256"],
                image=filename,
                detections=found,
                raw_detections=raw_detections,
                class_mapping=config,
                count=len(found),
                max_objectness=float(outputs[0][0, 0].max()),
                seconds=seconds,
            )
        )
    result = dict(
        test_id=job["test_id"],
        created=db.now(),
        selected_model=selected,
        frames=len(rows),
        distinct_source_frames=len({x["source_frame_id"] for x in rows}),
        total_detections=sum(x["count"] for x in rows),
        same_imaging_parameters=job["same_imaging_parameters"],
        white_balance_policy=job.get(
            "white_balance_policy",
            (
                "automatic_preserved"
                if job["training_profile"]["isp"].get("wb_auto")
                else "manual_fixed"
            ),
        ),
        effective_white_balance_gains_verified=job.get(
            "effective_white_balance_gains_verified",
            False if job["training_profile"]["isp"].get("wb_auto") else None,
        ),
        backend="PC PyTorch CPU float32",
        rows=rows,
        accuracy=None,
        accuracy_note="Unlabeled fresh test images: no accuracy score",
        used_for_training=False,
        board_model_switched=False,
    )
    result.update(backend="PC PyTorch CUDA float32", execution=execution)
    db.write(out / "result.json", result)


if __name__ == "__main__":
    main()
