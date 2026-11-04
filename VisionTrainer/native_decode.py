import cv2
import numpy as np

LABELS = ["1000_front_up", "1000_front_down", "1001_front", "back"]
_PEAK_KERNEL = np.ones((3, 3), np.uint8)


def iou(a, b):

    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return float(inter / max(1e-9, area_a + area_b - inter))


def _select(boxes, scores, limit=None, threshold=0.35):

    order = np.argsort(-scores, kind="stable")
    extents = np.maximum(0, boxes[:, 2:] - boxes[:, :2])
    areas = extents.prod(axis=1)
    selected = []
    while order.size:
        first = int(order[0])
        selected.append(first)
        if limit is not None and len(selected) >= limit:
            break
        rest = order[1:]
        if not rest.size:
            break
        overlap = np.maximum(
            0,
            np.minimum(boxes[first, 2:], boxes[rest, 2:])
            - np.maximum(boxes[first, :2], boxes[rest, :2]),
        ).prod(axis=1)
        union = np.maximum(1e-9, areas[first] + areas[rest] - overlap)
        order = rest[overlap / union < threshold]
    return selected


def decode(
    outputs, object_threshold=0.3, state_threshold=0.65, labels=None, center_mask=None
):
    labels = LABELS if labels is None else labels
    p, logits = outputs
    p, logits = p[0], logits[0]
    if len(logits) != len(labels):
        raise ValueError("Model output and label schema do not match")
    maxima = cv2.dilate(p[0], _PEAK_KERNEL)
    valid = (p[0] >= maxima) & (p[0] >= object_threshold)
    if center_mask is not None:
        valid &= center_mask
    ys, xs = np.where(valid)
    if not ys.size:
        return []

    sizes = p[1:3, ys, xs].T * np.array([512, 288])
    centers = (np.column_stack((xs, ys)) + p[3:5, ys, xs].T) * 4
    boxes = np.concatenate((centers - sizes / 2, centers + sizes / 2), axis=1)
    scores = p[0, ys, xs]
    selected = _select(boxes, scores, limit=20)
    found = []
    for index in selected:
        a = logits[:, ys[index], xs[index]]
        s = np.exp(a - a.max())
        s /= s.sum()
        ci = int(s.argmax())
        found.append(
            dict(
                bbox=boxes[index].tolist(),
                objectness=float(scores[index]),
                state_scores=s.tolist(),
                raw_label=labels[ci],
                label=labels[ci] if s[ci] >= state_threshold else "uncertain",
                confidence=float(s[ci]),
            )
        )
    return found


def merge(rows):

    if not rows:
        return []
    boxes = np.asarray([row["bbox"] for row in rows], dtype=np.float64)
    scores = np.asarray([row["objectness"] for row in rows], dtype=np.float64)
    return [rows[index] for index in _select(boxes, scores)]
