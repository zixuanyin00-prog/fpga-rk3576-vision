import math

SENSOR = (1920, 1080)


def rectangle(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("矩形需填写 X、Y、宽、高")
    if any(type(v) is not int for v in value):
        raise ValueError("矩形坐标和尺寸必须为整数")
    x, y, w, h = value
    if not (
        0 <= x < 1920 and 0 <= y < 1080 and 8 <= w <= 1920 - x and 8 <= h <= 1080 - y
    ):
        raise ValueError("矩形必须在1920×1080画面内，宽高至少8像素")
    return list(value)


def settings(values):
    if not isinstance(values, dict) or set(values) != {
        "rectangle",
        "confidence_percent",
        "track_hits",
    }:
        raise ValueError("请同时提交推理合法矩形、置信度阈值和ID位置追踪次数")
    rect = rectangle(values["rectangle"])
    confidence = values["confidence_percent"]
    hits = values["track_hits"]
    if (
        type(confidence) not in (int, float)
        or not math.isfinite(confidence)
        or not 0 < confidence <= 100
    ):
        raise ValueError("置信度阈值必须大于0且不超过100%")
    if type(hits) is not int or not 1 <= hits <= 30:
        raise ValueError("ID位置追踪次数必须为1–30的整数")
    return dict(rectangle=rect, confidence_percent=confidence, track_hits=hits)


def contains(rect, box):
    x, y, w, h = rectangle(rect)
    return (
        len(box) == 4
        and all(math.isfinite(v) for v in box)
        and x <= box[0] < box[2] <= x + w
        and y <= box[1] < box[3] <= y + h
    )
