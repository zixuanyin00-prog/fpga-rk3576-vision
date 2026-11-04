import cv2
import numpy as np

LABELS = ["1000_front_up", "1000_front_down", "1001_front", "back"]


def iou(a, b):
    a = np.array(a)
    b = np.array(b)
    inter = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2])).prod()
    return float(
        inter
        / max(
            1e-9,
            np.maximum(0, a[2:] - a[:2]).prod()
            + np.maximum(0, b[2:] - b[:2]).prod()
            - inter,
        )
    )


def decode(
    outputs, object_threshold=0.3, state_threshold=0.65, labels=None, center_mask=None
):
    labels = LABELS if labels is None else labels
    p, logits = outputs
    p = p[0]
    logits = logits[0]
    if len(logits) != len(labels):
        raise ValueError("Model output and label schema do not match")
    maxima = cv2.dilate(p[0], np.ones((3, 3), np.uint8))
    valid = (p[0] >= maxima) & (p[0] >= object_threshold)
    if center_mask is not None:
        valid &= center_mask
    ys, xs = np.where(valid)
    found = []
    for y, x in zip(ys, xs):
        w, h = p[1:3, y, x] * [512, 288]
        cx, cy = (np.array([x, y]) + p[3:5, y, x]) * 4
        a = logits[:, y, x]
        s = np.exp(a - a.max())
        s /= s.sum()
        ci = int(s.argmax())
        found.append(
            dict(
                bbox=[
                    float(cx - w / 2),
                    float(cy - h / 2),
                    float(cx + w / 2),
                    float(cy + h / 2),
                ],
                objectness=float(p[0, y, x]),
                state_scores=s.tolist(),
                raw_label=labels[ci],
                label=labels[ci] if s[ci] >= state_threshold else "uncertain",
                confidence=float(s[ci]),
            )
        )
    selected = []
    for obj in sorted(found, key=lambda d: d["objectness"], reverse=True):
        if all(iou(obj["bbox"], q["bbox"]) < 0.35 for q in selected):
            selected.append(obj)
        if len(selected) >= 20:
            break
    return selected
