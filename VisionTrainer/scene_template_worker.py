from pathlib import Path
import argparse
import hashlib
import torch
from core import store as db, scene_template
from core.gpu import require_cuda, record_device
from core.native_frames import load_native
from core.batch15 import beijing_now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["train", "suggest"])
    parser.add_argument("job")
    args = parser.parse_args()
    job = db.read(args.job)
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    device = require_cuda()
    execution = record_device(out, device)
    if args.mode == "train":
        from core.box_comparison import metrics

        started = beijing_now()
        weights = scene_template.fit(job["root"], job["frames"], device)
        calibration = []
        for threshold in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85):
            hit = miss = false = 0
            for f in job["frames"]:
                image, _ = load_native(job["root"], f)
                other = dict(
                    weights,
                    templates=[
                        t for t in weights["templates"] if t["source_frame"] != f["id"]
                    ],
                )
                if not other["templates"]:
                    raise ValueError("至少需要两张有完整元件小框的确认图")
                result = metrics(
                    f["objects"], scene_template.locate(image, other, device, threshold)
                )
                hit += result["matched"]
                miss += result["missed"]
                false += result["false_boxes"]
            calibration.append(
                dict(threshold=threshold, matched=hit, missed=miss, false_boxes=false)
            )
        best = min(
            calibration,
            key=lambda r: (
                r["missed"] + 2 * r["false_boxes"],
                r["false_boxes"],
                r["missed"],
                r["threshold"],
            ),
        )
        weights["threshold"] = best["threshold"]
        torch.save(weights, out / "boxes.pt")
        result = dict(
            architecture=scene_template.ARCHITECTURE,
            initialization="confirmed_native_examples",
            pretrained_weights_used=False,
            recognition_checkpoint_used=False,
            training_started_beijing=started,
            created=beijing_now(),
            sha256=hashlib.sha256((out / "boxes.pt").read_bytes()).hexdigest(),
            scene=job["scene"],
            source_frames=len(job["frames"]),
            templates=len(weights["templates"]),
            execution=execution,
            helper_input="native_1080p_source_crop",
            automatic_activation=False,
            training_calibration=calibration,
            threshold=weights["threshold"],
            calibration_method="leave_one_training_image_out; test images never used",
            training_manifest=[
                dict(
                    id=f["id"],
                    sha256=f["sha256"],
                    original_sha256=f.get("original_sha256"),
                )
                for f in job["frames"]
            ],
        )
        db.write(out / "result.json", result)
        return
    model = Path(job["model"])
    meta = db.read(model.parent / "result.json")
    if (
        meta["architecture"] != scene_template.ARCHITECTURE
        or job.get("selected_model_sha256") != meta["sha256"]
        or job.get("selected_trained_at") != meta["created"]
        or hashlib.sha256(model.read_bytes()).hexdigest() != meta["sha256"]
    ):
        raise ValueError("场景模板助手与本次选型不一致")
    weights = torch.load(model, map_location="cpu", weights_only=True)
    proposals = {}
    evidence = []
    for i, f in enumerate(job["frames"]):
        image, source = load_native(job["root"], f)
        proposals[f["id"]] = scene_template.locate(image, weights, device)
        evidence.append(dict(frame=f["id"], native_source=source))
        db.write(
            out / "progress.json",
            dict(stage="原生场景模板定位", step=i + 1, steps=len(job["frames"])),
        )
    db.write(out / "proposals.json", proposals)
    db.write(
        out / "evidence.json",
        dict(execution=execution, selection=job.get("selection"), rows=evidence),
    )


if __name__ == "__main__":
    main()
