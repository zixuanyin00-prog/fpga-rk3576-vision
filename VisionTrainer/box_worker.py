from pathlib import Path
import argparse
import hashlib
import time
import numpy as np
import torch
from core import store as db
from core.box_model import ScratchBoxNet, ARCHITECTURE, augment, loss_for, predict
from core.proposals import annotation_predictions
from core.decoder import iou
from core.native_frames import load_native, windows, finalize
from PIL import Image
from core import box_v2
from core.box_assistant import development_indices
from core.gpu import require_cuda, record_device


def replay_metrics(model, meta, data, held):
    device = require_cuda()
    weights = torch.load(model, map_location="cpu", weights_only=True)
    v2 = meta["architecture"] == box_v2.ARCHITECTURE
    net = (box_v2.BoxNetV2() if v2 else ScratchBoxNet()).eval()
    net.load_state_dict(weights["detector"] if v2 else weights)
    verifier = box_v2.make_verifier(meta).eval() if v2 else None
    if verifier is not None:
        verifier.load_state_dict(weights["verifier"])
    net.to(device)
    if verifier is not None:
        verifier.to(device)
    matched = targets = extra = 0
    for i in held:
        im, objects = data[i]
        found = (
            box_v2.locate_configured(net, verifier, im, meta)
            if v2
            else annotation_predictions(lambda x: predict(net, x), im)
        )
        found = [
            r
            for r in found
            if r["bbox"][0] >= 4
            and r["bbox"][1] >= 4
            and r["bbox"][2] <= 508
            and r["bbox"][3] <= 284
        ]
        truth = [o for o in objects if not o.get("ignore")]
        used = set()
        targets += len(truth)
        for g in truth:
            choices = sorted(
                (iou(g["bbox"], d["bbox"]), j)
                for j, d in enumerate(found)
                if j not in used
            )
            if choices and choices[-1][0] >= 0.5:
                matched += 1
                used.add(choices[-1][1])
        extra += sum(
            j not in used
            and not any(
                o.get("ignore") and iou(o["bbox"], d["bbox"]) >= 0.2 for o in objects
            )
            for j, d in enumerate(found)
        )
    return dict(matched=matched, targets=targets, extra=extra)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["train", "suggest"])
    parser.add_argument("job")
    args = parser.parse_args()
    job = db.read(args.job)
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    device = require_cuda()
    execution = record_device(out, device)
    if args.mode == "suggest":
        meta = db.read(Path(job["model"]).parent / "result.json")
        if (
            meta.get("architecture") not in (ARCHITECTURE, box_v2.ARCHITECTURE)
            or meta.get("initialization") != "random"
            or meta.get("pretrained_weights_used") is not False
        ):
            raise ValueError("无效的专用画框模型来源")
        if (
            hashlib.sha256(Path(job["model"]).read_bytes()).hexdigest()
            != meta["sha256"]
        ):
            raise ValueError("专用画框模型校验失败")
        if (
            job.get("selected_model_sha256") != meta["sha256"]
            or job.get("selected_trained_at") != meta["created"]
        ):
            raise ValueError("画框模型与本次选型不一致")
        v2 = meta["architecture"] == box_v2.ARCHITECTURE
        weights = torch.load(job["model"], map_location="cpu", weights_only=True)
        net = (box_v2.BoxNetV2() if v2 else ScratchBoxNet()).eval()
        net.load_state_dict(weights["detector"] if v2 else weights)
        verifier = box_v2.make_verifier(meta).eval() if v2 else None
        if verifier is not None:
            verifier.load_state_dict(weights["verifier"])
        net.to(device)
        if verifier is not None:
            verifier.to(device)
        proposals = {}
        evidence = []
        for f in job["frames"]:
            image, source = load_native(job["root"], f)
            width, height = image.size
            rows = []
            regions = windows(image.size, include_full=False)
            for ox, oy, rx, by in regions:

                crop = image.crop((ox, oy, rx, by))
                tile = Image.new("RGB", (512, 288))
                tile.paste(crop, (0, 0))
                pixels = np.asarray(tile)
                found = (
                    box_v2.locate_configured(net, verifier, pixels, meta)
                    if v2
                    else annotation_predictions(lambda im: predict(net, im), pixels)
                )
                for row in found:
                    a, b, c, d = row["bbox"]
                    a, b, c, d = max(0, a), max(0, b), min(rx - ox, c), min(by - oy, d)
                    if c - a < 4 or d - b < 4:
                        continue
                    boundary = bool(
                        ox > 0
                        and a < 3
                        or oy > 0
                        and b < 3
                        or rx < width
                        and c > rx - ox - 3
                        or by < height
                        and d > by - oy - 3
                    )
                    rows.append(
                        dict(
                            row,
                            bbox=[a + ox, b + oy, c + ox, d + oy],
                            label=-2,
                            proposed=True,
                            needs_review=True,
                            tile_boundary=boundary or row.get("tile_boundary", False),
                            proposal_source=meta["architecture"],
                            proposal_model_sha256=meta["sha256"],
                        )
                    )
            from core.box_geometry import padded

            margin = meta.get("inference", {}).get("annotation_margin", 0)
            rows = [
                dict(
                    row,
                    bbox=padded(row["bbox"], margin, image.size),
                    annotation_margin=margin,
                )
                for row in rows
            ]
            proposals[f["id"]] = finalize(rows, image.size)
            db.write(
                out / ("raw_" + f["id"] + ".json"),
                dict(size=list(image.size), rows=rows),
            )
            evidence.append(dict(frame=f["id"], native_source=source, windows=regions))
        db.write(
            out / "evidence.json",
            dict(
                helper_input="native_1080p_source_crop",
                legacy_tensor_preprocessing="native tiles padded to 512x288 without resizing",
                rows=evidence,
            ),
        )
        db.write(out / "proposals.json", proposals)
        return
    from core.batch15 import beijing_now

    training_started = beijing_now()
    torch.manual_seed(job["seed"])
    rng = np.random.default_rng(job["seed"])
    net = box_v2.BoxNetV2().to(device)
    optimizer = torch.optim.AdamW(net.parameters(), lr=0.001, weight_decay=0.0001)
    rows = job["frames"]
    data = []
    if job.get("scene"):
        from core.scene_boxes import native_training_data

        data, owners = native_training_data(job["root"], rows)
        held_owners = set(range(4, len(rows), 5)) if len(rows) >= 6 else set()
        held = [i for i, owner in enumerate(owners) if owner in held_owners]
    else:
        for f in rows:
            raw = Path(f["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != f["sha256"]:
                raise ValueError("画框训练原图完整性校验失败")
            data.append(
                (np.frombuffer(raw, np.uint8).reshape(288, 512, 3), f["objects"])
            )
        held = development_indices(rows)

    train = [i for i in range(len(data)) if i not in held]
    assert train
    if not any(not o.get("ignore") for i in train for o in data[i][1]):
        raise ValueError("训练分组缺少完整小框，请补充已确认原图")
    start = time.monotonic()
    history = []
    for step in range(job["steps"]):
        batch = [
            augment(
                *data[int(i)],
                rng,
                plain=(step % 3 == 0),
                stride=4,
                scene=bool(job.get("scene"))
            )
            for i in rng.choice(train, size=4)
        ]
        if step % 2 and not job.get("scene"):
            negative = box_v2.background(*data[int(rng.choice(train))], rng)
            if negative is not None:
                batch[-1] = negative
        z = net(torch.stack([b[0] for b in batch]).to(device))
        loss = loss_for(z, [b[1] for b in batch])
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 5)
        optimizer.step()
        if step == int(job["steps"] * 0.75):
            for group in optimizer.param_groups:
                group["lr"] = 0.0002
        if step % 10 == 0 or step + 1 == job["steps"]:
            row = dict(
                step=step + 1,
                steps=job["steps"],
                loss=float(loss.detach()),
                seconds=time.monotonic() - start,
            )
            history.append(row)
            db.write(out / "progress.json", row)
    net.eval()
    verifier = box_v2.SpatialVerifier().to(device)
    opt = torch.optim.AdamW(verifier.parameters(), lr=0.0007, weight_decay=0.0003)
    examples = box_v2.verifier_examples([data[i] for i in train], rng, count=60)
    patches = torch.stack([x for x, y in examples])
    labels = torch.tensor([y for x, y in examples])
    positive = (labels == 1).nonzero().flatten().numpy()
    negative = (labels == 0).nonzero().flatten().numpy()
    if not len(positive) or not len(negative):
        raise ValueError("缺少完整目标或已核对背景，无法训练复核器")
    verifier_steps = int(job.get("verifier_steps", 700))
    for step in range(verifier_steps):
        ids = np.r_[rng.choice(positive, 24), rng.choice(negative, 24)]
        loss = torch.nn.functional.binary_cross_entropy_with_logits(
            verifier(patches[ids].to(device)), labels[ids].to(device)
        )
        opt.zero_grad()
        loss.backward()
        opt.step()
        if step % 10 == 0 or step + 1 == verifier_steps:
            db.write(
                out / "progress.json",
                dict(
                    step=step + 1,
                    steps=verifier_steps,
                    stage="背景复核",
                    loss=float(loss.detach()),
                    seconds=time.monotonic() - start,
                ),
            )
    verifier.eval()
    torch.save(
        dict(detector=net.state_dict(), verifier=verifier.state_dict()),
        out / "boxes.pt",
    )
    matched = truth_count = extra = 0
    for i in held:
        found = box_v2.locate(net, verifier, data[i][0], joint_threshold=0.65)
        truth = [o for o in data[i][1] if not o.get("ignore")]
        used = set()
        truth_count += len(truth)
        for g in truth:
            choices = sorted(
                (iou(g["bbox"], d["bbox"]), j)
                for j, d in enumerate(found)
                if j not in used
            )
            if choices and choices[-1][0] >= 0.5:
                matched += 1
                used.add(choices[-1][1])
        extra += sum(
            j not in used
            and not any(
                o.get("ignore") and iou(o["bbox"], d["bbox"]) >= 0.2 for o in data[i][1]
            )
            for j, d in enumerate(found)
        )
    result = dict(
        architecture=box_v2.ARCHITECTURE,
        initialization="random",
        pretrained_weights_used=False,
        recognition_checkpoint_used=False,
        class_agnostic=True,
        created=db.now(),
        sha256=hashlib.sha256((out / "boxes.pt").read_bytes()).hexdigest(),
        train_frames=len(train),
        development_frames=len(held),
        train_targets=sum(sum(not o.get("ignore") for o in data[i][1]) for i in train),
        steps=job["steps"],
        verifier_steps=verifier_steps,
        verifier_positive_examples=len(positive),
        verifier_negative_examples=len(negative),
        seconds=time.monotonic() - start,
        parameters=sum(p.numel() for p in net.parameters())
        + sum(p.numel() for p in verifier.parameters()),
        history=history,
        development=dict(
            matched=matched,
            targets=truth_count,
            extra=extra,
            iou=0.5,
            independent_scene_acceptance=False,
        ),
        recognition_accuracy_accepted=False,
    )
    result.update(
        execution=execution,
        verifier_layout="spatial4x6",
        inference=dict(
            detector_threshold=0.30, verifier_threshold=0.35, joint_threshold=0.65
        ),
        held_indices=held,
        promotion_allowed=True,
    )
    result.update(
        created=beijing_now(),
        training_started_beijing=training_started,
        scene=job.get("scene"),
        source_frames=len(rows),
        helper_input=job.get("helper_input"),
        automatic_activation=False,
        training_manifest=[
            dict(
                id=f.get("id"),
                sha256=f["sha256"],
                original_sha256=f.get("original_sha256"),
                annotation_revision=f.get("annotation_revision", 0),
            )
            for f in rows
        ],
    )
    if job.get("scene"):
        result["workflow_generation"] = "scene-native-v3"

        calibration = []
        for threshold in (0.25, 0.35, 0.45, 0.55, 0.65):
            hit = miss = false = 0
            for i in train:
                found = box_v2.locate(
                    net,
                    verifier,
                    data[i][0],
                    threshold=0.25,
                    joint_threshold=threshold,
                    native_only=True,
                )
                targets = [o for o in data[i][1] if not o.get("ignore")]
                used = set()
                for g in targets:
                    choices = sorted(
                        (iou(g["bbox"], d["bbox"]), j)
                        for j, d in enumerate(found)
                        if j not in used
                    )
                    if choices and choices[-1][0] >= 0.5:
                        hit += 1
                        used.add(choices[-1][1])
                    else:
                        miss += 1
                false += sum(
                    j not in used
                    and not any(
                        o.get("ignore") and iou(o["bbox"], d["bbox"]) >= 0.2
                        for o in data[i][1]
                    )
                    for j, d in enumerate(found)
                )
            calibration.append(
                dict(threshold=threshold, matched=hit, missed=miss, false_boxes=false)
            )
        best = min(
            calibration,
            key=lambda r: (
                r["missed"] + r["false_boxes"],
                r["false_boxes"],
                r["threshold"],
            ),
        )
        result["training_calibration"] = calibration
        result["inference"] = dict(
            detector_threshold=0.25,
            verifier_threshold=0.35,
            joint_threshold=best["threshold"],
            native_tiles_only=True,
        )
        from core.box_comparison import metrics
        from core.box_geometry import padded

        found_train = {
            i: box_v2.locate_configured(net, verifier, data[i][0], result)
            for i in train
        }
        margins = []
        for margin in (0, 0.03, 0.06, 0.09, 0.12):
            cost = 0
            for i in train:
                truth = [
                    dict(o, label=-1 if o.get("ignore") else 0) for o in data[i][1]
                ]
                cost += metrics(
                    truth,
                    [dict(o, bbox=padded(o["bbox"], margin)) for o in found_train[i]],
                )["estimated_geometry_operations"]
            margins.append(dict(margin=margin, estimated_geometry_operations=cost))
        result["margin_calibration"] = margins
        result["inference"]["annotation_margin"] = min(
            margins, key=lambda r: (r["estimated_geometry_operations"], r["margin"])
        )["margin"]
        result["created"] = beijing_now()
    if job.get("previous_model"):
        previous = Path(job["previous_model"])
        meta = db.read(previous.parent / "result.json")
        if hashlib.sha256(previous.read_bytes()).hexdigest() != meta["sha256"]:
            raise ValueError("上一画框模型完整性校验失败")
        before = replay_metrics(previous, meta, data, held)
        result["previous_development"] = before
        result["promotion_allowed"] = (
            matched >= before["matched"] and extra <= before["extra"]
        )
        result["promotion_note"] = (
            "开发回放仅用于防止退步；漏标会影响结果，不能代替独立验收"
        )
    db.write(out / "result.json", result)


if __name__ == "__main__":
    main()
