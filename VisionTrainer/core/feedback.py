import copy
import hashlib
import json
import math
import uuid
from collections import Counter
from . import store as db


def schema(count=3):
    if type(count) is not int or not 1 <= count <= 30:
        raise ValueError("初始姿态数量应为1至30")
    ids = ["pose_" + uuid.uuid4().hex for _ in range(count)]
    return dict(
        kind="poses",
        numbers=[],
        labels=ids,
        class_ids=ids.copy(),
        titles=["姿态" + str(i + 1) for i in range(count)],
    )


def class_ids(s):
    return s.get("class_ids") or [
        "legacy_" + hashlib.sha256(x.encode()).hexdigest()[:24] for x in s["labels"]
    ]


def add_pose(p, request_id):
    if not request_id:
        raise ValueError("缺少操作编号")
    previous = p.setdefault("pose_requests", {}).get(request_id)
    if previous:
        return previous
    s = p["schema"]
    s["class_ids"] = class_ids(s)
    identity = "pose_" + uuid.uuid4().hex
    n = 1
    while "姿态" + str(n) in s["titles"]:
        n += 1
    s["labels"].append(identity)
    s["class_ids"].append(identity)
    s["titles"].append("姿态" + str(n))
    result = dict(id=identity, index=len(s["labels"]) - 1, title=s["titles"][-1])
    p["pose_requests"][request_id] = result
    db.save(p)
    return result


def normalize(p, objects, reviewed):
    ids = class_ids(p["schema"])
    out = []
    for original in objects:
        o = copy.deepcopy(original)
        b = o.get("bbox", [])
        label = o.get("label")
        if (
            len(b) != 4
            or any(type(x) not in (float, int) or not math.isfinite(x) for x in b)
            or not 0 <= b[0] < b[2] <= 512
            or not 0 <= b[1] < b[3] <= 288
        ):
            raise ValueError("框必须位于原图内且有面积")
        if o.get("class_id"):
            if o["class_id"] not in ids:
                raise ValueError("类别ID不属于本项目")
            label = ids.index(o["class_id"])
        if type(label) is not int or label not in [-3, -2, -1] + list(range(len(ids))):
            raise ValueError("无效类别")
        pending = label == -2 or bool(o.get("needs_review") or o.get("proposed"))
        if reviewed and pending:
            raise ValueError("还有候选目标未核对，请逐个确认或删框")
        o.update(
            bbox=[float(x) for x in b],
            label=label,
            needs_review=pending,
            class_id=ids[label] if label >= 0 else None,
            supervision=(
                "positive"
                if label >= 0
                else (
                    "negative"
                    if label == -3
                    else "ignore" if label == -1 else "candidate"
                )
            ),
        )
        out.append(o)

    if reviewed:
        for i, a in enumerate(out):
            for b in out[i + 1 :]:
                overlap = min(a["bbox"][2], b["bbox"][2]) > max(
                    a["bbox"][0], b["bbox"][0]
                ) and min(a["bbox"][3], b["bbox"][3]) > max(a["bbox"][1], b["bbox"][1])
                if (
                    overlap
                    and (a["label"] < 0 or b["label"] < 0)
                    and a["label"] != b["label"]
                ):
                    raise ValueError(
                        "正样本、负样本或忽略区域重叠，请调整或删除冲突框后确认"
                    )
    return out


def family(f):
    keys = {("hash", f["sha256"])}
    for k in ("parent_sha256", "original_sha256"):
        if f.get(k):
            keys.add(("hash", f[k]))
    for k in ("original_id", "passage_id"):
        if f.get(k):
            keys.add((k, str(f[k])))
    if f.get("boot_id") and f.get("source_frame_id") is not None:
        keys.add(("source", f["boot_id"], f["source_frame_id"]))
    return keys


def contaminated(p, f):
    used = (
        set().union(
            *(
                family(x)
                for x in p["frames"]
                if x.get("role") == "train" or x.get("feedback_transferred_at")
            )
        )
        if p["frames"]
        else set()
    )
    return bool(family(f) & used)


def scene_key(p, f):

    if f.get("native_roi") and not all(
        f.get(k) for k in ("origin", "capture_session", "imaging_identity")
    ):
        raise ValueError("原生图缺少场景身份，不能自动合并")
    return json.dumps(
        [
            p["id"],
            f.get("calibration", {}).get("rectangle", f.get("origin")),
            f.get("capture_session") or f.get("scene_alias") or f.get("batch"),
            f.get("imaging_identity"),
            f.get("scene_spec"),
        ],
        sort_keys=True,
    )


def confirm_scene(p, batch):
    anchor = next(
        (
            f
            for f in p["frames"]
            if f.get("batch") == batch and f.get("role") == "train"
        ),
        None,
    )
    if not anchor:
        raise ValueError("请明确选择同场景训练批次")

    scope = anchor.get("scene_alias") or batch
    for f in p["frames"]:
        if not f.get("native_roi") and (
            f.get("feedback_pending") or f.get("batch") == batch
        ):
            f["scene_alias"] = scope
            f["scene_confirmation"] = dict(
                anchor_batch=batch,
                confirmed_at=db.now(),
                method="explicit_user_same_scene",
            )
    db.save(p)


def summary(p):
    rows = [
        f for f in p["frames"] if f.get("feedback_pending") and not f.get("excluded")
    ]
    counts = Counter(
        o["label"]
        for f in rows
        for o in f.get("objects", [])
        if not o.get("needs_review") and not o.get("proposed")
    )
    return dict(
        originals=len({f["sha256"] for f in rows}),
        new_originals=len(
            {
                f["sha256"]
                for f in rows
                if f.get("role") != "train"
                and not any(
                    u.get("state") == "complete" for u in f.get("training_usage", [])
                )
            }
        ),
        ready=sum(bool(f.get("reviewed")) for f in rows),
        classes=[
            dict(id=c, title=p["schema"]["titles"][i], count=counts[i])
            for i, c in enumerate(class_ids(p["schema"]))
        ],
        negatives=counts[-3],
        ignored=counts[-1],
    )


def prepare(p, batch, root):
    pending = [
        f for f in p["frames"] if f.get("feedback_pending") and not f.get("excluded")
    ]
    if not pending:
        raise ValueError("没有待训练反馈")
    anchor = next(
        (
            f
            for f in p["frames"]
            if f.get("batch") == batch and f.get("role") == "train"
        ),
        None,
    )
    if not anchor:
        raise ValueError("请明确选择同场景训练批次")
    key = scene_key(p, anchor)
    if any(scene_key(p, f) != key for f in pending):
        raise ValueError("待训练反馈与所选批次场景不同，请分项目或场景处理")
    if any(not f.get("reviewed") or not f.get("all_objects_reviewed") for f in pending):
        raise ValueError(
            "反馈中还有未完成整图检查的原图；请补漏框、检查其余候选并确认整图"
        )
    rows = []
    seen = set()
    for f in p["frames"]:
        if f.get("excluded") or not (
            f.get("role") == "train" or f.get("feedback_transferred_at")
        ):
            continue
        if scene_key(p, f) != key:
            continue
        if not (f.get("reviewed") and f.get("all_objects_reviewed")):
            continue
        row = copy.deepcopy(f)
        row["objects"] = normalize(p, row.get("objects", []), True)
        if seen & family(f):
            continue
        seen.update(family(f))
        path = (root / f["file"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("原图路径越界")
        raw = path.read_bytes()
        if len(raw) != 512 * 288 * 3 or hashlib.sha256(raw).hexdigest() != f["sha256"]:
            raise ValueError("原图完整性校验失败")
        if f.get("original_file"):
            original = (root / f["original_file"]).resolve()
            if not original.is_relative_to(root.resolve()):
                raise ValueError("原始矩形路径越界")
            w, h = f["original_size"]
            raw = original.read_bytes()
            if (
                len(raw) != w * h * 3
                or hashlib.sha256(raw).hexdigest() != f["original_sha256"]
            ):
                raise ValueError("原始矩形完整性校验失败")
        row["original_role"] = row["role"]
        row["role"] = "train"
        rows.append(row)
    counts = Counter(o["label"] for f in rows for o in f["objects"] if o["label"] >= 0)
    missing = [
        p["schema"]["titles"][i]
        for i in range(len(p["schema"]["labels"]))
        if p["schema"]["labels"][i] != "unreadable" and not counts[i]
    ]
    if missing:
        raise ValueError("本场景累计数据缺少已确认姿态：" + "、".join(missing))
    manifest = dict(
        kind="human_feedback_supervised",
        batch=batch,
        scene_key=key,
        scene=p.get("batch_scene", {}).get(batch, {}),
        original_images=len(rows),
        unique_original_images=len(rows),
        unique_source_frames=len(
            {
                (f["boot_id"], f["source_frame_id"])
                for f in rows
                if f.get("boot_id") and f.get("source_frame_id") is not None
            }
        )
        or None,
        train_originals=len(rows),
        validation_originals=0,
        feedback_ids=[f["id"] for f in pending],
        source_image_hashes={
            f["id"]: f.get("original_sha256", f["sha256"]) for f in rows
        },
        class_instances={p["schema"]["labels"][i]: v for i, v in counts.items()},
        class_mapping=copy.deepcopy(p["schema"]),
        initialization=dict(kind="random", historical_weights_used=False),
        augmentation_is_not_new_original=True,
        split={f["id"]: "train" for f in rows},
        roi=anchor.get("calibration", {}).get(
            "rectangle", list(anchor.get("origin", [0, 0])) + [512, 288]
        ),
        training_started_beijing=None,
        training_completed_beijing=None,
        output_hashes={},
        accepted=False,
        board_tested=False,
        independent_test_available=False,
        selection="training diagnostics only; capture fresh independent test images",
    )
    return rows, [], manifest


def transfer(p, frame_id):
    f = db.frame(p, frame_id)
    if not f.get("feedback_transferred_at"):
        f["feedback_transferred_at"] = db.now()
        f["feedback_original_role"] = f["role"]
    f["feedback_pending"] = True

    for other in p["frames"]:
        if family(other) & family(f):
            other["independent_test_ineligible"] = True
    db.save(p)
    return f


def import_prediction(p, run, test, frame_id):
    if run not in p["runs"]:
        raise ValueError("模型版本不存在")
    if not test or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
        for c in test
    ):
        raise ValueError("无效测试编号")
    folder = db.project_path(p["id"]) / "runs" / run
    result = db.read(folder / "tests" / test / "result.json")
    config = db.read(folder / "config.json")
    row = next(r for r in result["rows"] if r["frame_id"] == frame_id)
    f = db.frame(p, frame_id)
    if not f.get("feedback_source"):
        f["feedback_source"] = dict(
            run=run,
            test=test,
            selected_model=result["selected_model"],
            raw_output=copy.deepcopy(row),
            class_mapping=copy.deepcopy(config),
        )
        if not (f.get("annotated_at") or f.get("reviewed")):
            f["objects"] = [
                dict(
                    bbox=d["bbox"],
                    label=(
                        p["schema"]["labels"].index(d["label"])
                        if d["label"] in p["schema"]["labels"]
                        else -2
                    ),
                    proposed=True,
                    needs_review=True,
                )
                for d in row["detections"]
            ]
    db.save(p)
    return f
