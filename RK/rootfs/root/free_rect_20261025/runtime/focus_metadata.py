import math

READABLE = {"front_upright", "front_inverted", "back"}


def validate_region(spec):
    if spec["size"] != [512, 288] or spec["native_size"] != [1920, 1080]:
        raise ValueError("Only the current FPGA native ROI geometry is supported")
    x, y = spec["origin"]
    if (
        type(x) is not int
        or type(y) is not int
        or not (0 <= x <= 1408 and 0 <= y <= 792)
    ):
        raise ValueError("Invalid native ROI origin")
    lo, hi = spec["body_center_y"]
    margin = spec["complete_body_margin"]
    if not y <= lo < hi <= y + 288 or not 0 <= margin <= 32:
        raise ValueError("Invalid body acceptance region")
    return x, y


def native_objects(objects, spec):
    x, y = spec["origin"]
    margin = spec["complete_body_margin"]
    lo, hi = spec["body_center_y"]
    rows = []
    for obj in objects:
        a, b, c, d = obj["bbox"]
        if not all(math.isfinite(v) for v in (a, b, c, d)):
            continue
        if not (margin <= a < c <= 512 - margin and margin <= b < d <= 288 - margin):
            continue
        if not lo <= (b + d) / 2 + y <= hi:
            continue
        rows.append(dict(obj, bbox=[a + x, b + y, c + x, d + y]))
    return rows


def fresh(source, previous):
    return previous is None or 0 < ((source - previous) & 0xFFFFFFFF) < 0x80000000
