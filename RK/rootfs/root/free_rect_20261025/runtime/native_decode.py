from pathlib import Path
import ctypes as C
import os
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


def _python_select(boxes, scores, limit=None, threshold=0.35):

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


def _python_decode(
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
    selected = _python_select(boxes, scores, limit=20)
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


def _python_merge(rows):

    if not rows:
        return []
    boxes = np.asarray([row["bbox"] for row in rows], dtype=np.float64)
    scores = np.asarray([row["objectness"] for row in rows], dtype=np.float64)
    return [rows[index] for index in _python_select(boxes, scores)]


_geometry_library = None


def _geometry():
    global _geometry_library
    if _geometry_library is None:
        lib = (C.PyDLL if os.environ.get("VISION_MINIMAL60") == "1" else C.CDLL)(
            str(Path(__file__).with_name("libnative512_geometry.so"))
        )
        lib.native512_geometry_api.restype = C.c_uint
        if lib.native512_geometry_api() != 1:
            raise RuntimeError("Selected512 geometry API mismatch")
        lib.native512_peaks.argtypes = [
            C.c_void_p,
            C.c_uint,
            C.c_uint,
            C.c_void_p,
            C.c_float,
            C.c_void_p,
            C.c_void_p,
            C.c_void_p,
        ]
        lib.native512_peaks.restype = C.c_int
        lib.native512_select.argtypes = [
            C.c_void_p,
            C.c_void_p,
            C.c_uint,
            C.c_uint,
            C.c_double,
            C.c_void_p,
            C.c_void_p,
        ]
        lib.native512_select.restype = C.c_int
        _geometry_library = lib
    return _geometry_library


def backend():
    _geometry()
    return dict(
        backend="native_float64_geometry_stable_nms_python_original_float32_softmax",
        api=1,
        probability_head_is_not_sigmoided=True,
        classification_math_unchanged=True,
    )


def _select(boxes, scores, limit=None, threshold=0.35):
    boxes = np.ascontiguousarray(boxes, dtype=np.float64)
    scores = np.ascontiguousarray(scores, dtype=np.float64)
    count = len(scores)
    if not count:
        return []
    selected = np.empty(count, np.uint32)
    selected_boxes = np.empty((count, 4), np.float64)
    rc = _geometry().native512_select(
        boxes.ctypes.data,
        scores.ctypes.data,
        count,
        0 if limit is None else limit,
        threshold,
        selected.ctypes.data,
        selected_boxes.ctypes.data,
    )
    if rc < 0:
        raise RuntimeError("Selected512 merge geometry failed: " + str(rc))
    return selected[:rc].tolist()


def decode(
    outputs, object_threshold=0.3, state_threshold=0.65, labels=None, center_mask=None
):
    labels = LABELS if labels is None else labels
    p, logits = outputs
    if p.dtype != np.float32:
        return _python_decode(
            outputs, object_threshold, state_threshold, labels, center_mask
        )
    p, logits = p[0], logits[0]
    if len(logits) != len(labels):
        raise ValueError("Model output and label schema do not match")
    p = np.ascontiguousarray(p)
    height, width = p.shape[1:]
    mask = None
    if center_mask is not None:
        mask_array = np.asarray(center_mask)
        if mask_array.dtype != np.bool_ or mask_array.shape != (height, width):
            return _python_decode(
                outputs, object_threshold, state_threshold, labels, center_mask
            )
        mask = np.ascontiguousarray(mask_array, dtype=np.uint8)
    selected = np.empty(20, np.uint32)
    boxes = np.empty((20, 4), np.float64)
    scores = np.empty(20, np.float64)
    rc = _geometry().native512_peaks(
        p.ctypes.data,
        height,
        width,
        mask.ctypes.data if mask is not None else None,
        np.float32(object_threshold),
        selected.ctypes.data,
        boxes.ctypes.data,
        scores.ctypes.data,
    )
    if rc < 0:
        raise RuntimeError("Selected512 peak geometry failed: " + str(rc))
    found = []
    for index in range(rc):
        y, x = divmod(int(selected[index]), width)
        a = logits[:, y, x]
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
