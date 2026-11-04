from pathlib import Path
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "console_data"
LOCK = threading.RLock()
MAX_ROI_PIXELS = 518_400


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uid() + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(temp, path)
            return
        except PermissionError as exc:

            if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(0.01 * (attempt + 1))


def safe(base, relative):
    base = Path(base).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError("文件路径越界")
    return path


def folder(identity):
    if not re.fullmatch(r"[a-f0-9]{32}", str(identity)):
        raise ValueError("流程编号无效")
    return DATA / identity


def load(identity):
    return read(folder(identity) / "flow.json")


def save(flow):
    flow["updated_at"] = now()
    write(folder(flow["id"]) / "flow.json", flow)


def letter(index):
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def create(name):
    flow = dict(
        id=uid(),
        name=str(name or "新场景")[:80],
        created_at=now(),
        stage=1,
        rectangle=[704, 396, 512, 288],
        labels=[dict(id=uid(), name=letter(i), active=True) for i in range(4)],
        frames=[],
        runs=[],
        tests=[],
        deployments=[],
        profile=None,
        helper_selection=None,
        pixel_policy="native_crop_only_v1",
        revision=0,
    )
    with LOCK:
        save(flow)
    return flow


def listing():
    if not DATA.exists():
        return []
    rows = []
    for path in DATA.glob("*/flow.json"):
        try:
            p = read(path)
            rows.append(
                {k: p[k] for k in ("id", "name", "created_at", "updated_at", "stage")}
            )
        except (OSError, ValueError, KeyError):
            continue
    return sorted(rows, key=lambda r: r["updated_at"], reverse=True)


def rectangle(value, training=False):
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(v) is not int for v in value)
    ):
        raise ValueError("选区需要整数 X、Y、宽、高")
    x, y, w, h = value
    if not (
        0 <= x < 1920 and 0 <= y < 1080 and 8 <= w <= 1920 - x and 8 <= h <= 1080 - y
    ):
        raise ValueError("选区必须位于1920×1080原图内")
    if w * h > MAX_ROI_PIXELS:
        raise ValueError(
            f"原生RGB选区最多518,400像素；当前{w}×{h}={w*h:,}像素，请缩小选区"
        )
    if training and (w < 512 or h < 288):
        raise ValueError("原生模型窗口为512×288，采集范围至少512×288；不会放大小图")
    return value.copy()


def windows(width, height):
    from native_windows import windows as shared_windows

    return shared_windows(width, height)


def image(flow, frame):
    path = safe(folder(flow["id"]), frame["file"])
    raw = path.read_bytes()
    w, h = frame["size"]
    if len(raw) != w * h * 3 or sha(raw) != frame["sha256"]:
        raise ValueError("原始图像尺寸或哈希不一致")
    from PIL import Image

    return Image.frombytes("RGB", (w, h), raw)


def annotate(flow, frame_id, objects, reviewed, page):
    f = next(f for f in flow["frames"] if f["id"] == frame_id)
    if f.get("excluded"):
        raise ValueError("此图已排除，请先恢复后再修改标注")
    active = {v["id"] for v in flow["labels"] if v["active"]}
    w, h = f["size"]
    normalized = []
    for o in objects:
        b = o.get("bbox", [])
        label = o.get("class_id")
        if (
            len(b) != 4
            or not all(type(n) in (float, int) and math.isfinite(n) for n in b)
            or not (0 <= b[0] < b[2] <= w and 0 <= b[1] < b[3] <= h)
        ):
            raise ValueError("元件框必须位于原图内并有面积")
        if label not in active and label not in (None, "ignore"):
            raise ValueError("姿态已删除，请重新指定")
        if reviewed and label is None:
            raise ValueError("请为所有框指定姿态，或删除误框")
        normalized.append(
            dict(id=str(o.get("id") or uid()), bbox=b, class_id=label, proposed=False)
        )

    f.setdefault("history", []).append(
        dict(
            at=now(),
            page=page,
            objects=f.get("objects", []),
            reviewed=f.get("reviewed", False),
        )
    )
    f.update(
        objects=normalized, reviewed=bool(reviewed), revision=f.get("revision", 0) + 1
    )
    save(flow)
    return f
