from pathlib import Path
import datetime
import hashlib
import json
import re
import uuid
import os
import time
from . import changes

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def now():
    return datetime.datetime.now().astimezone().isoformat()


def uid():
    return (
        datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    )


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(6):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if os.name != "nt" or attempt == 5:
                raise
            time.sleep(0.005 * (attempt + 1))
    if path.name in ("project.json", "settings.json"):
        changes.publish()


def project_path(id):
    if not re.fullmatch(r"[a-zA-Z0-9_]+", id):
        raise ValueError("无效项目编号")
    path = DATA / id
    if not (path / "project.json").exists():
        raise ValueError("项目不存在")
    return path


def schema(numbers):
    digits = list(dict.fromkeys(str(n).strip() for n in numbers if str(n).strip()))
    if len(digits) > 30 or any(not re.fullmatch(r"\d{1,12}", n) for n in digits):
        raise ValueError("数字类别应为1至12位数字，最多30种")
    labels = []
    titles = []
    for n in digits or [""]:
        for direction, title in [("upright", "正面上"), ("inverted", "正面下")]:
            labels.append("front_" + direction + (":" + n if n else ""))
            titles.append(title + (" · " + n if n else ""))
    return dict(
        numbers=digits,
        labels=labels + ["back", "unreadable"],
        titles=titles + ["反面", "无法辨认"],
    )


def create(name, numbers=(), pose_count=3):
    from .feedback import schema as poses

    p = dict(
        id=uid(),
        name=name.strip() or "新环境",
        created=now(),
        schema=poses(pose_count),
        frames=[],
        runs=[],
        auto_train=False,
    )
    write(DATA / p["id"] / "project.json", p)
    return p


def save(p):
    write(project_path(p["id"]) / "project.json", p)


def load(id):
    return read(project_path(id) / "project.json")


def listing():
    DATA.mkdir(exist_ok=True)
    return sorted(
        [read(p) for p in DATA.glob("*/project.json")],
        key=lambda p: p["created"],
        reverse=True,
    )


def frame(p, id):
    return next(f for f in p["frames"] if f["id"] == id)


def annotate(p, id, objects, reviewed, expected=None):
    from .feedback import normalize

    f = frame(p, id)
    normalized = []
    if f.get("excluded"):
        raise ValueError("此批采错区域，已保留为诊断图，不参与训练")
    normalized = normalize(p, objects, reviewed)
    if expected is not None and expected != f.get("annotation_revision", 0):
        if normalized == f.get("objects") and bool(reviewed) == f.get("reviewed"):
            return f
        raise ValueError(
            "标注已被其他窗口更新。刷新页面可清除未提交草稿并同步最新标注，也可手动合并。"
        )
    if normalized == f.get("objects") and bool(reviewed) == f.get("reviewed"):
        return f
    f.setdefault("annotation_history", []).append(
        dict(
            objects=f.get("objects", []),
            reviewed=f.get("reviewed", False),
            saved_at=now(),
            revision=f.get("annotation_revision", 0),
        )
    )
    f.update(
        objects=normalized,
        reviewed=bool(reviewed),
        annotated_at=now(),
        annotation_revision=f.get("annotation_revision", 0) + 1,
    )
    save(p)
    return f


def training_ready(p):
    from . import batch15

    return False, "请显式选择本批15张、确认质量及初始化后点击训练"
    frames = [f for f in p["frames"] if f["role"] == "train" and not f.get("excluded")]
    if not frames or not all(f.get("reviewed") for f in frames):
        return False, "请逐张确认训练图；没有目标的图也需确认"
    counts = [0] * len(p["schema"]["labels"])
    for f in frames:
        for o in f.get("objects", []):
            if o["label"] >= 0:
                counts[o["label"]] += 1
    lacking = [p["schema"]["titles"][i] for i, c in enumerate(counts[:-1]) if c < 5]
    if lacking:
        return False, "以下类别至少需要5个已确认目标：" + "、".join(lacking)
    if sum(counts[:-1]) < 30:
        return False, "训练至少需要30个完整目标，建议每类30个以上"
    return True, "可以训练"


def add_frames(p, items):
    from .feedback import family

    known = {f["sha256"]: f["role"] for f in p["frames"]}
    groups = {
        f["source_group"]: f["role"] for f in p["frames"] if f.get("source_group")
    }
    baseline = ROOT / "assets/baseline_training_annotations.json"
    trained = (
        {f["sha256"] for f in read(baseline)["frames"]} if baseline.exists() else set()
    )
    for f in items:
        if any(
            family(f) & family(other) and f["role"] != other["role"]
            for other in p["frames"]
        ):
            raise ValueError("同源图及其派生图不能跨训练与独立测试集合")
        if f.get("source_group") in groups and groups[f["source_group"]] != f["role"]:
            raise ValueError("同一采集目录不能拆分到训练与验收；请另采独立批次")
        if f["role"] != "train" and f["sha256"] in trained:
            raise ValueError("该图已用于初始模型训练，不能作为独立验证或验收样本")
        if f["sha256"] in known:
            if known[f["sha256"]] != f["role"]:
                raise ValueError("训练/验证/验收集包含相同原图，已拒绝导入")
            raise ValueError("发现重复样本；请重新采集不同源帧")
        known[f["sha256"]] = f["role"]
        if f.get("source_group"):
            groups[f["source_group"]] = f["role"]
    p["frames"].extend(items)
    save(p)


def snapshot(p, role):
    from .feedback import contaminated

    root = project_path(p["id"])
    rows = []
    for f in p["frames"]:
        if f["role"] != role or f.get("excluded"):
            continue
        if role != "train" and (
            f.get("independent_test_ineligible") or contaminated(p, f)
        ):
            continue
        if not f.get("reviewed"):
            raise ValueError("尚有未确认标注")
        path = root / f["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != f["sha256"]:
            raise ValueError("原图完整性校验失败")
        rows.append(f.copy())
    return rows
