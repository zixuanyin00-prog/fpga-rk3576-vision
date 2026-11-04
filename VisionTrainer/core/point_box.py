import math
import cv2
import numpy as np


def propose(image, point, existing=()):
    width, height = image.size
    if len(point) != 2 or not all(
        type(v) in (int, float) and math.isfinite(v) for v in point
    ):
        raise ValueError("点击坐标无效")
    if not 0 <= point[0] <= 512 or not 0 <= point[1] <= 288:
        raise ValueError("点击须位于采样图内")
    x = min(width - 1, round(point[0] * width / 512))
    y = min(height - 1, round(point[1] * height / 288))
    sizes = [
        [
            (o["bbox"][2] - o["bbox"][0]) * width / 512,
            (o["bbox"][3] - o["bbox"][1]) * height / 288,
        ]
        for o in existing
        if o.get("label", -2) >= 0
        and not o.get("needs_review")
        and not o.get("proposed")
    ]
    ew, eh = (
        np.median(sizes, axis=0)
        if sizes
        else (min(110, width * 0.23), min(75, height * 0.4))
    )
    ew, eh = max(12, min(ew, width * 0.6)), max(12, min(eh, height * 0.7))
    x0, y0 = max(0, int(x - ew)), max(0, int(y - eh))
    x1, y1 = min(width, int(x + ew + 1)), min(height, int(y + eh + 1))
    pixels = np.asarray(image)[y0:y1, x0:x1]
    lab = cv2.cvtColor(pixels, cv2.COLOR_RGB2LAB).astype(np.float32)
    sx, sy = x - x0, y - y0
    seed = np.median(
        lab[max(0, sy - 2) : sy + 3, max(0, sx - 2) : sx + 3].reshape(-1, 3), axis=0
    )
    distance = np.linalg.norm(lab - seed, axis=2)
    choices = []
    for threshold in (12, 20, 30, 44, 60):
        mask = (distance < threshold).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        label = labels[sy, sx]
        if not label:
            continue
        a, b, w, h, count = stats[label].tolist()
        if (
            w < max(6, 0.45 * ew)
            or h < max(6, 0.45 * eh)
            or count < 25
            or count > 0.8 * mask.size
            or w > 1.8 * ew
            or h > 1.8 * eh
        ):
            continue
        score = (
            abs(math.log(w / ew)) + abs(math.log(h / eh)) + 0.3 * (1 - count / (w * h))
        )
        choices.append(
            (
                score,
                [
                    max(0, x0 + a - 2),
                    max(0, y0 + b - 2),
                    min(width, x0 + a + w + 2),
                    min(height, y0 + b + h + 2),
                ],
            )
        )
    fallback = not choices
    box = (
        min(choices, key=lambda r: r[0])[1]
        if choices
        else [
            max(0, x - ew / 2),
            max(0, y - eh / 2),
            min(width, x + ew / 2),
            min(height, y + eh / 2),
        ]
    )
    return dict(
        bbox=[
            box[0] * 512 / width,
            box[1] * 288 / height,
            box[2] * 512 / width,
            box[3] * 288 / height,
        ],
        label=-2,
        proposed=True,
        needs_review=True,
        edge_review=box[0] < 4
        or box[1] < 4
        or box[2] > width - 4
        or box[3] > height - 4,
        proposal_source="click_native_segmentation",
        point_native=[x, y],
        proposal_bbox_native=box,
        proposal_native_size=[width, height],
        uncertain=fallback,
        hint=(
            "边界不清，已生成待调整小框"
            if fallback
            else "已按点击处原图边界生成小框，请检查完整范围"
        ),
    )
