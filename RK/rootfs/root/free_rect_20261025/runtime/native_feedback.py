from pathlib import Path
import hashlib
import json
import math
import os
import time

_saved = 0
_stats = dict(saved=0, duplicates=0, association_only=0, limited=0, errors=0)


def save_feedback(root, frame, failures, meta):
    global _saved
    rows = []
    for row in failures:
        c = row.get("classification", {})
        score = c.get("confidence")
        if (
            isinstance(score, (int, float))
            and math.isfinite(score)
            and c.get("accepted") is False
        ):
            rows.append(row)
    if not rows:
        _stats["association_only"] += 1
        return
    boot = (
        rows[0].get("boot_id")
        or Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    )
    source = int(frame.source_frame_id)
    if any(
        int(r["roi_frame_id"]) != source or r.get("boot_id", boot) != boot for r in rows
    ):
        raise ValueError("Feedback pixels and classification source do not match")
    folder = Path("/userdata/vision_int8_20261026_1338/feedback")
    folder.mkdir(parents=True, exist_ok=True)
    ident = boot + "_" + str(source)
    target = folder / (ident + ".json")
    if target.exists():
        _stats["duplicates"] += 1
        return
    if _saved >= 20:
        _stats["limited"] += 1
        return
    raw = frame.rgb.tobytes()
    name = ident + ".rgb"
    record = dict(
        meta,
        file=name,
        size=[frame.rgb.shape[1], frame.rgb.shape[0]],
        origin=[frame.x, frame.y],
        boot_id=boot,
        source_frame_id=source,
        sha256=hashlib.sha256(raw).hexdigest(),
        failures=rows,
        pixel_policy="native_crop_only_v1",
        feedback_kind="classification_low_confidence",
        dedup_key=ident,
        writer_pid=os.getpid(),
    )
    temp = folder / (name + ".upload")
    temp.write_bytes(raw)
    temp.replace(folder / name)
    temp = target.with_suffix(".json.upload")
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)
    _saved += 1
    _stats["saved"] += 1


def stats():
    return dict(_stats)
