from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import math

DEFAULT_TARGET = 15
BEIJING = timezone(timedelta(hours=8))


def beijing_now():
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def settings(frames=15, stride=1):

    if isinstance(frames, bool) or str(frames) != "15":
        raise ValueError(
            "本候选仅允许15张有效原图；扩大批次须先提供失败证据并取得用户同意"
        )
    if isinstance(stride, bool) or str(stride) != "1":
        raise ValueError("15张批次必须逐张标注，stride必须为1")
    return DEFAULT_TARGET, 1


def accepted_frames(frames, labels):
    rows = []
    seen = set()
    hashes = set()
    scene = None
    for frame in frames:
        if frame.get("excluded") or frame.get("quality_review") == "reject":
            continue
        if not (
            frame.get("native_roi") is True
            and frame.get("reviewed") is True
            and frame.get("quality_review") == "pass"
            and frame.get("all_objects_reviewed") is True
        ):
            continue
        key = (frame.get("boot_id"), frame.get("source_frame_id"))
        if not key[0] or type(key[1]) is not int or not frame.get("sha256"):
            raise ValueError("缺少原生源帧或启动身份；导入图不能冒充现场独立原图")
        if key in seen or frame["sha256"] in hashes:
            raise ValueError("重复源帧或重复原图，不计为独立训练图")
        identity = (
            tuple(
                frame.get("calibration", {}).get("rectangle", frame.get("origin", []))
            ),
            frame.get("capture_session"),
            frame.get("imaging_identity"),
        )
        if not identity[0] or not identity[1] or not identity[2]:
            raise ValueError("缺少固定机位、ROI或成像身份")
        if scene is not None and identity != scene:
            raise ValueError("采集中机位、ROI或成像配置发生变化")
        scene = identity
        for obj in frame.get("objects", []):
            box = obj.get("bbox", [])
            label = obj.get("label")
            if (
                len(box) != 4
                or any(
                    not isinstance(x, (int, float)) or not math.isfinite(x) for x in box
                )
                or not 0 <= box[0] < box[2] <= 512
                or not 0 <= box[1] < box[3] <= 288
                or type(label) is not int
                or label not in [-3, -1] + list(range(len(labels)))
                or obj.get("proposed")
                or obj.get("needs_review")
            ):
                raise ValueError("标注未完整复核或坐标/类别无效")
        seen.add(key)
        hashes.add(frame["sha256"])
        rows.append(frame.copy())
    return rows


def prepare(project, batch, initialization, root, validation_count=3):
    if not batch:
        raise ValueError("请显式选择本次训练批次；不会自动合并历史批次")
    if initialization != "random":
        raise ValueError(
            "请选择随机初始化；历史权重须另行按实际训练北京时间选择，当前候选不支持隐式沿用"
        )
    frames = [
        f
        for f in project["frames"]
        if f.get("batch") == batch and f.get("role") == "train"
    ]
    rows = accepted_frames(frames, project["schema"]["labels"])
    if len(rows) != 15:
        raise ValueError(
            f"本批有效原图{len(rows)}/15；需不同源帧、清晰度通过、所有目标人工复核，不自动扩采或拼历史"
        )
    evidence = project.get("batch_scene", {}).get(batch, {})
    required = (
        "camera_position",
        "exposure_ms",
        "gain",
        "isp_identity",
        "confirmed_at_beijing",
    )
    if not all(evidence.get(k) is not None and evidence.get(k) != "" for k in required):
        raise ValueError("请先确认本批机位、曝光、增益与ISP身份")
    for key in ("exposure_ms", "gain"):
        if (
            isinstance(evidence[key], bool)
            or not isinstance(evidence[key], (int, float))
            or not math.isfinite(evidence[key])
            or evidence[key] < 0
            or key == "exposure_ms"
            and evidence[key] == 0
        ):
            raise ValueError("曝光必须为正数，模拟增益dB可以为0；两者均需实际核对")
    for frame in rows:
        path = (root / frame["file"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("原图路径越界")
        raw = path.read_bytes()
        if (
            len(raw) != 512 * 288 * 3
            or hashlib.sha256(raw).hexdigest() != frame["sha256"]
        ):
            raise ValueError("原生图尺寸或哈希不匹配")
        if frame.get("original_file"):
            original = (root / frame["original_file"]).resolve()
            if not original.is_relative_to(root.resolve()):
                raise ValueError("原始矩形路径越界")
            w, h = frame["original_size"]
            raw = original.read_bytes()
            if (
                len(raw) != w * h * 3
                or hashlib.sha256(raw).hexdigest() != frame["original_sha256"]
            ):
                raise ValueError("原始矩形图像尺寸或哈希不匹配")

    rows.sort(
        key=lambda f: hashlib.sha256((batch + "/" + f["id"]).encode()).hexdigest()
    )
    val = rows[:validation_count]
    train = rows[validation_count:]
    counts = Counter(
        project["schema"]["labels"][o["label"]]
        for f in rows
        for o in f.get("objects", [])
        if o["label"] >= 0
    )
    train_counts = Counter(
        project["schema"]["labels"][o["label"]]
        for f in train
        for o in f.get("objects", [])
        if o["label"] >= 0
    )
    missing = [
        label
        for label in project["schema"]["labels"]
        if label != "unreadable" and not train_counts[label]
    ]
    if missing:
        raise ValueError(
            "训练子集缺少类别实例："
            + ",".join(missing)
            + "；请调整本批覆盖或显式设计划分，禁止自动补历史图"
        )
    manifest = dict(
        schema_version=1,
        batch=batch,
        original_images=15,
        unique_source_frames=15,
        source_image_hashes={
            f["id"]: f.get("original_sha256", f["sha256"]) for f in rows
        },
        class_instances=dict(counts),
        train_class_instances=dict(train_counts),
        train_originals=len(train),
        validation_originals=len(val),
        split={f["id"]: ("validation" if f in val else "train") for f in rows},
        grouping="original_source_frame_before_augmentation",
        augmentation_is_not_new_original=True,
        roi=rows[0]
        .get("calibration", {})
        .get("rectangle", rows[0]["origin"] + [512, 288]),
        scene=evidence,
        initialization=dict(kind="random", historical_weights_used=False),
        training_started_beijing=None,
        training_completed_beijing=None,
        output_hashes={},
        test_originals=0,
        external_acceptance_samples_not_in_training=True,
        accepted=False,
        board_tested=False,
    )
    return train, val, manifest


def output_contract(labels, hashes):

    return dict(
        kind="joint_position_and_state_heads",
        separate_coarse_fine_pair_available=False,
        deployable_as_existing_two_model_pair=False,
        labels=labels,
        input_size_wh=[512, 288],
        input_layout="NCHW",
        input_color="RGB",
        mean=[123.675, 116.28, 103.53],
        std=[58.395, 57.12, 57.375],
        normalization="PC/ONNX expects normalized float; RKNN config embeds same mean/std for raw input",
        outputs=["probability_box", "state_logits"],
        output_layout="NCHW",
        grid_stride=4,
        bbox_coordinates="ROI-local xyxy native pixels; add ROI origin exactly once for source coordinates",
        hashes=hashes,
        verified_against_board=False,
    )
