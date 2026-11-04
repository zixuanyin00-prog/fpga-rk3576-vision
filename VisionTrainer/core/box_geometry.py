from .decoder import iou


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )


def padded(box, margin, size=(512, 288)):
    a, b, c, d = box
    dx = (c - a) * margin
    dy = (d - b) * margin
    return [max(0, a - dx), max(0, b - dy), min(size[0], c + dx), min(size[1], d + dy)]


def duplicate(a, b):
    if iou(a, b) >= 0.45:
        return True
    small, large = sorted((a, b), key=area)
    overlap = intersection(a, b) / max(1, area(small))
    centered = all(
        abs((a[k] + a[k + 2] - b[k] - b[k + 2]) / 2)
        <= 0.25 * max(a[k + 2] - a[k], b[k + 2] - b[k])
        for k in (0, 1)
    )
    return overlap >= 0.85 and area(small) >= 0.25 * area(large) and centered


def deduplicate(rows):

    candidates = []
    for row in rows:
        b = row["bbox"]
        children = []
        for other in rows:
            c = other["bbox"]
            if other is row or not 0.03 * area(b) < area(c) < 0.65 * area(b):
                continue
            if intersection(b, c) / max(1, area(c)) >= 0.9 and not any(
                duplicate(c, x) for x in children
            ):
                children.append(c)
        if len(children) < 2:
            candidates.append(row)
    keep = []
    for row in sorted(
        candidates,
        key=lambda r: (
            not r.get("tile_boundary", False),
            r.get("score", r.get("objectness", 0)),
        ),
        reverse=True,
    ):
        if not any(duplicate(row["bbox"], old["bbox"]) for old in keep):
            keep.append(row)
    return keep
