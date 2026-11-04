from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import hashlib
import json
import os
import shutil
import time
from object_outcomes import FOLDER as OUTCOME_FOLDER

FOLDER = Path("/userdata/vision_int8_20261026_1338/unrecognized_v3")
CAPTURE_POLICY = "persistent_v3"
MAX_BYTES = 2 * 1024**3
MIN_FREE = 1024**3
MAX_FPS = 2.0
WARNING_MAX_BYTES = 256 * 1024**2
WARNING_INTERVAL = 5.0
_last_saved = -1.0
_last_status = 0.0
_used = None
_font = None
_last_warning = -1.0
_warning_used = 0
_stats = dict(
    saved=0,
    duplicates=0,
    rate_limited=0,
    storage_limited=0,
    errors=0,
    warning_rate_limited=0,
    warning_storage_limited=0,
)


def select_rows(rows):
    result = []
    for row in rows:
        classification = row.get("classification", {})
        tracking = row.get("tracking", {})
        status = tracking.get("status")
        reason = (
            "classification_low_confidence"
            if classification.get("accepted") is False
            else (
                "association_uncertain"
                if status == "association_uncertain"
                else (
                    "pose_unconfirmed"
                    if status == "classification_pending"
                    or tracking.get("cumulative_allocation") == "unrecognized"
                    or row.get("classification_temporal", {}).get(
                        "single_frame_label_disagrees"
                    )
                    else "crowded_pending" if status == "crowded_pending" else None
                )
            )
        )

        if reason and tracking.get("failure_observations", 0) >= 3:
            group = (
                "confirmed_track_warning"
                if tracking.get("counted")
                else (
                    "persistent_unrecognized"
                    if tracking.get("observed_registered")
                    else "association_conflict"
                )
            )
            result.append(
                dict(
                    row,
                    capture_reason=reason,
                    evidence_group=group,
                    capture_policy=CAPTURE_POLICY,
                )
            )
    return result


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".upload")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def status(error=None, force=False):
    global _last_status
    now = time.monotonic()
    if not force and now - _last_status < 1.0:
        return
    _last_status = now
    write_json(
        FOLDER / "status.json",
        dict(
            _stats,
            folder=str(FOLDER),
            recorded_monotonic=now,
            max_save_fps=MAX_FPS,
            quota_bytes=MAX_BYTES,
            stored_bytes=_used,
            error=error,
            warning_quota_bytes=WARNING_MAX_BYTES,
            stored_warning_bytes=_warning_used,
            warning_interval_seconds=WARNING_INTERVAL,
            policy="one physical source frame per folder; immutable original and separate annotation; no automatic deletion",
        ),
    )


def save(root, frame, rows, meta):
    global _last_saved, _used, _font, _last_warning, _warning_used
    rows = select_rows(rows)
    if not rows:
        return
    FOLDER.mkdir(parents=True, exist_ok=True)
    boot = (
        meta.get("boot_id")
        or Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    )
    source = int(frame.source_frame_id)
    if any(
        int(row["roi_frame_id"]) != source or row.get("boot_id", boot) != boot
        for row in rows
    ):
        raise ValueError("Unrecognized boxes and saved pixels have different sources")
    ident = boot + "_f" + str(source).zfill(10)
    folder = FOLDER / ident
    if folder.exists():
        _stats["duplicates"] += 1
        status()
        return
    now = time.monotonic()
    groups = {row["evidence_group"] for row in rows}
    primary = next(
        group
        for group in (
            "persistent_unrecognized",
            "association_conflict",
            "confirmed_track_warning",
        )
        if group in groups
    )
    warning_only = primary == "confirmed_track_warning"
    if warning_only and now - _last_warning < WARNING_INTERVAL:
        _stats["warning_rate_limited"] += 1
        status()
        return
    if now - _last_saved < 1 / MAX_FPS:
        _stats["rate_limited"] += 1
        status()
        return
    if _used is None:
        _used = sum(p.stat().st_size for p in FOLDER.rglob("*") if p.is_file())
        index = FOLDER / "index.jsonl"
        if index.is_file():
            for line in index.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record.get("evidence_group") == "confirmed_track_warning":
                    _warning_used += record.get("stored_bytes", 0)
    reserve = int(frame.rgb.nbytes) * 3 + 65536
    if warning_only and _warning_used + reserve > WARNING_MAX_BYTES:
        _stats["warning_storage_limited"] += 1
        status()
        return
    if _used + reserve > MAX_BYTES or shutil.disk_usage(FOLDER).free < MIN_FREE:
        _stats["storage_limited"] += 1
        status("Storage limit reached; existing samples preserved")
        return
    folder.mkdir()
    _last_saved = now
    raw = frame.rgb.tobytes()
    digest = hashlib.sha256(raw).hexdigest()
    (folder / "frame.rgb").write_bytes(raw)
    width, height = frame.rgb.shape[1], frame.rgb.shape[0]
    image = Image.frombytes("RGB", (width, height), raw)
    image.save(folder / "frame.png", compress_level=1)
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    if _font is None:
        candidates = (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        )
        _font = ImageFont.truetype(next(p for p in candidates if Path(p).is_file()), 16)
    titles = meta.get("class_titles", {})
    reasons = dict(
        classification_low_confidence="低置信度",
        association_uncertain="关联不确定",
        pose_unconfirmed="姿态待确认",
        crowded_pending="重叠待确认",
    )
    for row in rows:
        box = row.get("raw_box", row["box"])
        left = max(0, min(width - 1, int(box[0] - frame.x)))
        top = max(0, min(height - 1, int(box[1] - frame.y)))
        right = max(left, min(width - 1, int(box[2] - frame.x)))
        bottom = max(top, min(height - 1, int(box[3] - frame.y)))
        draw.rectangle((left, top, right, bottom), outline=(255, 160, 70), width=3)
        classification = row.get("classification", {})
        label = classification.get("raw_label", classification.get("label", ""))
        text = f"{titles.get(label,label)} {classification.get('confidence',0)*100:.1f}% · {reasons[row['capture_reason']]}"
        temporal = row.get("classification_temporal", {})
        if temporal.get("accepted"):
            text += f" · 多帧{titles.get(temporal['label'],temporal['label'])} {temporal['support']}/{temporal['reliable_observations']}"
        while text and _font.getlength(text) > width - 8:
            text = text[:-1]
        text_width = int(_font.getlength(text)) + 8
        x = min(left, max(0, width - text_width))
        y = max(0, top - 25)
        draw.rounded_rectangle(
            (x, y, x + text_width, min(height - 1, y + 24)), radius=5, fill=(16, 24, 37)
        )
        draw.text((x + 4, y + 2), text, font=_font, fill=(255, 227, 183))
    annotated.save(folder / "annotated.png", compress_level=1)
    record = dict(
        meta,
        boot_id=boot,
        source_frame_id=source,
        size=[width, height],
        origin=[frame.x, frame.y],
        sha256=digest,
        file="frame.rgb",
        image="frame.png",
        annotated_image="annotated.png",
        unrecognized_objects=rows,
        failures=rows,
        feedback_kind="observed_unrecognized",
        capture_policy=CAPTURE_POLICY,
        evidence_group=primary,
        evidence_groups=sorted(groups),
        minimum_failure_observations=3,
        lifecycle_object_ids=sorted(
            {row["object_id"] for row in rows if row.get("object_id")}
        ),
        lifecycle_outcome_folder=str(OUTCOME_FOLDER),
        raw_pixels_unchanged=True,
        annotation_is_separate=True,
        dedup_key=ident,
        captured_monotonic=now,
        writer_pid=os.getpid(),
        writer_tid=getattr(os, "gettid", lambda: None)(),
        scope="observable classification/identity failures; not ground truth for completely missed objects",
    )
    write_json(folder / "metadata.json", record)
    sample_bytes = sum(p.stat().st_size for p in folder.iterdir())
    manifest = dict(
        folder=ident,
        source_frame_id=source,
        boot_id=boot,
        sha256=digest,
        captured_monotonic=now,
        model_sha256=meta.get("model_sha256"),
        evidence_group=primary,
        capture_policy=CAPTURE_POLICY,
        stored_bytes=sample_bytes,
        files=["frame.rgb", "frame.png", "annotated.png", "metadata.json"],
    )
    with (FOLDER / "index.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(manifest, ensure_ascii=False) + "\n")
    write_json(FOLDER / "latest.json", manifest)

    write_json(
        Path(root) / "console_failures/latest.json",
        dict(
            file=str(folder / "frame.rgb"),
            record=str(folder / "metadata.json"),
            image=str(folder / "frame.png"),
        ),
    )
    _used += sample_bytes
    if warning_only:
        _last_warning = now
        _warning_used += sample_bytes
    _stats["saved"] += 1
    status(force=True)


def report_error(error):
    _stats["errors"] += 1
    try:
        status(str(error), force=True)
    except OSError:
        pass
