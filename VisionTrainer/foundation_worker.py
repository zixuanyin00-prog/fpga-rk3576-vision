from pathlib import Path
import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
import argparse
import json
import hashlib
import time
import torch
from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
from core import store as db
from core.native_frames import load_native, windows, checked_windows, finalize
from core.gpu import require_cuda, record_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["suggest"])
    parser.add_argument("job")
    args = parser.parse_args()
    job = db.read(args.job)
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    model_file = Path(job["model"])
    folder = model_file.parent
    meta = db.read(folder / "provenance.json")
    selection = job["selection"]
    if (
        selection.get("provider") != "huggingface"
        or selection.get("revision") != meta["revision"]
        or selection.get("model_id") != meta["model_id"]
    ):
        raise ValueError("预训练画框模型与本次选型不一致")
    for name, expected in meta["files"].items():
        if name == "README.md":
            continue
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
            raise ValueError("预训练画框文件校验失败：" + name)
    if selection.get("sha256") != meta["files"]["model.safetensors"]:
        raise ValueError("预训练画框权重与本次选型不一致")
    torch.set_num_threads(min(8, max(1, os.cpu_count() or 4)))
    device = require_cuda()
    execution = record_device(out, device)
    processor = AutoProcessor.from_pretrained(
        folder, local_files_only=True, trust_remote_code=False
    )
    model = (
        AutoModelForZeroShotObjectDetection.from_pretrained(
            folder, local_files_only=True, trust_remote_code=False, use_safetensors=True
        )
        .to(device)
        .eval()
    )
    proposals = {}
    evidence = []
    prompt = job.get("prompt", "an industrial workpiece.")
    for index, f in enumerate(job["frames"]):
        started = time.monotonic()
        image, source = load_native(job["root"], f)
        width, height = image.size
        rows = []
        regions = checked_windows(job.get("windows") or windows(image.size), image.size)
        for ox, oy, rx, by in regions:

            crop = image.crop((ox, oy, rx, by))
            inputs = processor(images=crop, text=prompt, return_tensors="pt").to(device)
            with torch.inference_mode():
                outputs = model(**inputs)
            result = processor.post_process_grounded_object_detection(
                outputs,
                inputs.input_ids,
                threshold=0.15,
                text_threshold=0.15,
                target_sizes=[(by - oy, rx - ox)],
            )[0]
            for box, score in zip(result["boxes"].tolist(), result["scores"].tolist()):
                x, y, x1, y1 = box

                boundary = bool(
                    ox > 0
                    and x < 3
                    or oy > 0
                    and y < 3
                    or rx < width
                    and x1 > rx - ox - 3
                    or by < height
                    and y1 > by - oy - 3
                )
                b = [
                    max(0.0, x + ox),
                    max(0.0, y + oy),
                    min(float(width), x1 + ox),
                    min(float(height), y1 + oy),
                ]
                if b[2] - b[0] < 4 or b[3] - b[1] < 4:
                    continue
                rows.append(
                    dict(
                        bbox=b,
                        score=score,
                        label=-2,
                        proposed=True,
                        needs_review=True,
                        tile_boundary=boundary,
                        proposal_source="grounding-dino-base",
                        proposal_model_sha256=selection["sha256"],
                    )
                )
        keep = finalize(rows, image.size)
        db.write(
            out / ("raw_" + f["id"] + ".json"), dict(size=list(image.size), rows=rows)
        )
        proposals[f["id"]] = keep
        db.write(out / "proposals_partial.json", proposals)
        evidence.append(
            dict(
                frame=f["id"],
                boxes=len(keep),
                seconds=time.monotonic() - started,
                prompt=prompt,
                native_source=source,
                windows=regions,
            )
        )
        db.write(
            out / "progress.json",
            dict(
                stage="GPU 本地预训练画框",
                execution=execution,
                step=index + 1,
                steps=len(job["frames"]),
            ),
        )
    db.write(out / "proposals.json", proposals)
    db.write(
        out / "evidence.json",
        dict(
            selection=selection,
            execution=execution,
            offline=True,
            image_upload=False,
            rows=evidence,
            proposals_require_class_assignment=True,
            helper_input="native_1080p_source_crop",
            processor_preprocessing=processor.image_processor.to_dict(),
        ),
    )


if __name__ == "__main__":
    main()
