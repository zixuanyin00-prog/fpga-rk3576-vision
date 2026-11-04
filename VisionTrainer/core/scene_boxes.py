import copy
import hashlib
import json
import numpy as np
from PIL import Image
from . import store as db, box_assistant, feedback
from .native_frames import load_native, windows


def scene_identity(p, batch):
    frames = [
        f
        for f in p["frames"]
        if f.get("batch") == batch and f["role"] == "train" and not f.get("excluded")
    ]
    if not frames:
        raise ValueError("请选择当前场景采样批次")
    identities = {
        json.dumps(
            [
                f.get("origin"),
                f.get("original_size"),
                f.get("imaging_identity"),
                f.get("calibration", {}).get("rectangle"),
            ],
            sort_keys=True,
        )
        for f in frames
    }
    if len(identities) != 1:
        raise ValueError("本批存在不同位置或成像配置，请分开训练")
    return dict(
        project=p["id"],
        batch=batch,
        name=p["name"],
        capture_identity=json.loads(next(iter(identities))),
    )


def snapshot(p, batch):
    scene = scene_identity(p, batch)
    rows = []
    seen = set()
    for f in p["frames"]:
        if (
            f.get("batch") != batch
            or f["role"] != "train"
            or f.get("excluded")
            or not f.get("reviewed")
            or not f.get("all_objects_reviewed")
            or f.get("quality_review") != "pass"
        ):
            continue
        if any(
            o.get("needs_review") or o.get("proposed") or o["label"] == -2
            for o in f["objects"]
        ):
            continue
        image, source = load_native(db.project_path(p["id"]), f)
        if source["sha256"] in seen:
            continue
        seen.add(source["sha256"])
        rows.append(copy.deepcopy(f))
    if len(rows) < 3:
        raise ValueError(
            "请先完整确认当前批至少3张图（建议3～5张），包括所有漏框和误框"
        )
    if sum(o["label"] >= 0 for f in rows for o in f["objects"]) < 6:
        raise ValueError(
            "已确认图至少需要6个完整元件小框；空图和大采样矩形不能代替小框"
        )
    return scene, rows


def native_training_data(root, rows):
    data = []
    owners = []
    for i, f in enumerate(rows):
        image, _ = load_native(root, f)
        width, height = image.size
        for x, y, x1, y1 in windows(image.size, include_full=False):
            tile = Image.new("RGB", (512, 288))
            tile.paste(image.crop((x, y, x1, y1)))
            objects = []
            for o in f["objects"]:
                a, b, c, d = [
                    o["bbox"][k] * [width / 512, height / 288][k % 2] for k in range(4)
                ]
                clip = [max(a, x) - x, max(b, y) - y, min(c, x1) - x, min(d, y1) - y]
                if clip[2] <= clip[0] or clip[3] <= clip[1] or o["label"] == -3:
                    continue
                objects.append(
                    dict(
                        bbox=clip,
                        ignore=o["label"] == -1 or a < x or b < y or c > x1 or d > y1,
                    )
                )

            if x1 - x < 512:
                objects.append(dict(bbox=[x1 - x, 0, 512, 288], ignore=True))
            if y1 - y < 288:
                objects.append(dict(bbox=[0, y1 - y, x1 - x, 288], ignore=True))
            data.append((np.asarray(tile), objects))
            owners.append(i)
    return data, owners


def candidates():
    found = []
    for path in (box_assistant.HOME / "runs").glob("*/result.json"):
        meta = db.read(path)
        if (
            meta.get("workflow_generation") != "scene-native-v3"
            or not meta.get("scene")
            or not (path.parent / "boxes.pt").exists()
        ):
            continue
        found.append(
            dict(
                run=path.parent.name,
                created=meta["created"],
                training_started_beijing=meta.get("training_started_beijing"),
                trained_at=meta["created"],
                sha256=meta["sha256"],
                architecture=meta["architecture"],
                scene=meta["scene"],
                ready=True,
            )
        )
    return sorted(found, key=lambda r: r["created"], reverse=True)


def independent_frames(p, training):
    used = set().union(*(feedback.family(f) for f in training)) if training else set()
    return [
        f
        for f in p["frames"]
        if f["role"] in ("test", "validation")
        and f.get("reviewed")
        and f.get("all_objects_reviewed")
        and not f.get("excluded")
        and not f.get("feedback_transferred_at")
        and not f.get("independent_test_ineligible")
        and not feedback.family(f) & used
    ]
