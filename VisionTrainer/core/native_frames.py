from pathlib import Path
import hashlib
import math
from PIL import Image
from .decoder import iou


def load_native(root, frame):
    if frame.get("original_file"):
        name = frame["original_file"]
        size = frame.get("original_size")
        digest = frame.get("original_sha256")
    elif (
        frame.get("native_roi") is True
        and frame.get("model_input_is_native") is not False
        and frame.get("input_transform", {}).get("kind") != "stretch_bilinear"
    ):

        name = frame["file"]
        size = [512, 288]
        digest = frame.get("sha256")
    else:
        raise ValueError(
            "缺少1080p源帧的原生裁剪图；不能用缩放后的训练输入代替，请重新采样"
        )
    if (
        not isinstance(size, (list, tuple))
        or len(size) != 2
        or any(type(n) is not int for n in size)
        or not 1 <= size[0] <= 1920
        or not 1 <= size[1] <= 1080
    ):
        raise ValueError("原生裁剪尺寸无效")
    base = Path(root).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise ValueError("原生裁剪路径无效")
    raw = path.read_bytes()
    if (
        len(raw) != size[0] * size[1] * 3
        or not digest
        or hashlib.sha256(raw).hexdigest() != digest
    ):
        raise ValueError("原生裁剪图像尺寸或哈希校验失败")
    return Image.frombytes("RGB", tuple(size), raw), dict(
        file=name,
        size=list(size),
        sha256=digest,
        source_frame_id=frame.get("source_frame_id"),
        origin=frame.get("origin"),
        source_frame_size=[1920, 1080],
        source_pixels_resized=False,
    )


def windows(size, tile=(512, 288), include_full=True):
    width, height = size
    tw, th = min(width, tile[0]), min(height, tile[1])

    def starts(length, span):
        return sorted(
            set(range(0, max(1, length - span + 1), max(1, span // 2)))
            | {length - span}
        )

    tiles = [
        (x, y, x + tw, y + th) for y in starts(height, th) for x in starts(width, tw)
    ]
    return list(
        dict.fromkeys(([(0, 0, width, height)] if include_full else []) + tiles)
    )


def checked_windows(rows, size):
    width, height = size
    result = []
    for row in rows:
        if (
            len(row) != 4
            or any(type(n) is not int for n in row)
            or not 0 <= row[0] < row[2] <= width
            or not 0 <= row[1] < row[3] <= height
        ):
            raise ValueError("画框窗口必须位于原生裁剪图内")
        result.append(tuple(row))
    if not result:
        raise ValueError("画框窗口不能为空")
    return result


def finalize(rows, size):

    from .box_geometry import deduplicate

    width, height = size
    valid = []
    for row in rows:
        b = row["bbox"]
        if (
            len(b) != 4
            or not all(math.isfinite(v) for v in b)
            or not 0 <= b[0] < b[2] <= width
            or not 0 <= b[1] < b[3] <= height
        ):
            continue
        valid.append(row)
    keep = deduplicate(valid)
    result = []
    for row in keep:
        b = row["bbox"]
        area = (b[2] - b[0]) * (b[3] - b[1])
        inside = 0
        for other in keep:
            if other is row:
                continue
            c = other["bbox"]
            cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
            if (
                (c[2] - c[0]) * (c[3] - c[1]) < area * 0.65
                and b[0] < cx < b[2]
                and b[1] < cy < b[3]
            ):
                inside += 1
        if inside >= 2:
            continue
        result.append(
            dict(
                row,
                bbox=[
                    b[0] * 512 / width,
                    b[1] * 288 / height,
                    b[2] * 512 / width,
                    b[3] * 288 / height,
                ],
                edge_review=bool(
                    b[0] < 4 or b[1] < 4 or b[2] > width - 4 or b[3] > height - 4
                ),
                proposal_bbox_native=list(b),
                proposal_native_size=[width, height],
                bbox_coordinate_size=[512, 288],
            )
        )
    return result
