from pathlib import Path
import ctypes as C
import os
import threading
import numpy as np


class RawPostprocessor:
    def __init__(self, height, width, classes, library=None):
        self.shape = (1, 5 + classes, height, width)
        self.classes = classes
        library = library or os.environ.get(
            "VISION_POSTPROCESS_LIBRARY",
            Path(__file__).with_name("libnative_postprocess.so"),
        )
        self.lib = C.CDLL(str(library))
        self.lib.native_postprocess_open.argtypes = [C.c_uint32] * 3
        self.lib.native_postprocess_open.restype = C.c_void_p
        self.lib.native_postprocess_close.argtypes = [C.c_void_p]
        self.lib.native_postprocess_close.restype = None
        self.lib.native_postprocess_decode.argtypes = [
            C.c_void_p,
            C.c_void_p,
            C.c_uint32,
            C.c_double,
            C.c_void_p,
            C.POINTER(C.c_double),
        ]
        self.lib.native_postprocess_decode.restype = C.c_int
        self.handle = self.lib.native_postprocess_open(height, width, classes)
        if not self.handle:
            raise RuntimeError(
                "Native raw-head decoder allocation/shape validation failed"
            )
        self.rows = np.empty((20, 7 + classes), np.float64)
        self.last_c_ms = None
        self.lock = threading.Lock()

    def decode(self, raw, labels, object_threshold=0.15, state_threshold=0.85):
        raw = np.asarray(raw)
        if raw.dtype != np.float32 or raw.shape != self.shape:
            raise ValueError(f"Expected dequantized float32 raw NCHW {self.shape}")
        if len(labels) != self.classes:
            raise ValueError("Raw-head class schema does not match labels")
        if not 0 <= object_threshold <= 1 or not 0 <= state_threshold <= 1:
            raise ValueError("Detection thresholds must lie in [0,1]")
        raw = np.ascontiguousarray(raw)
        with self.lock:
            if not self.handle:
                raise RuntimeError("Native raw-head decoder is closed")
            elapsed = C.c_double()
            count = self.lib.native_postprocess_decode(
                self.handle,
                raw.ctypes.data,
                raw.size,
                object_threshold,
                self.rows.ctypes.data,
                C.byref(elapsed),
            )
            if count < 0:
                messages = {
                    -1: "Invalid decoder buffers",
                    -2: "Raw-head element count mismatch",
                    -3: "Selected model produced non-finite raw-head values",
                }
                raise ValueError(
                    messages.get(count, f"Native raw-head decoder failed ({count})")
                )
            self.last_c_ms = elapsed.value
            records = self.rows[:count].tolist()
        result = []
        for row in records:
            index = int(row[6])
            confidence = row[5]
            result.append(
                dict(
                    bbox=row[:4],
                    objectness=row[4],
                    confidence=confidence,
                    raw_label=labels[index],
                    state_scores=row[7:],
                    label=(
                        labels[index] if confidence >= state_threshold else "uncertain"
                    ),
                )
            )
        return result

    def close(self):
        with self.lock:
            if self.handle:
                self.lib.native_postprocess_close(self.handle)
                self.handle = None

    def __del__(self):
        try:
            if getattr(self, "handle", None):
                self.close()
        except Exception:
            pass


_local = threading.local()
_matrix_libraries = {}


def decode_raw(raw, labels, object_threshold=0.15, state_threshold=0.85, library=None):

    raw = np.asarray(raw)
    if raw.ndim != 4 or raw.shape[0] != 1 or raw.shape[1] != 5 + len(labels):
        raise ValueError("Raw-head schema does not match the selected labels")
    key = (raw.shape[2], raw.shape[3], len(labels), str(library))
    if getattr(_local, "key", None) != key:
        previous = getattr(_local, "decoder", None)
        if previous:
            previous.close()
        _local.decoder = RawPostprocessor(
            raw.shape[2], raw.shape[3], len(labels), library
        )
        _local.key = key
    return _local.decoder.decode(raw, labels, object_threshold, state_threshold)


def last_c_ms():
    decoder = getattr(_local, "decoder", None)
    return decoder.last_c_ms if decoder else None


def iou_matrix(boxes, tracks, library=None):

    boxes = np.ascontiguousarray(np.asarray(boxes, dtype=np.float64).reshape(-1, 4))
    tracks = np.ascontiguousarray(np.asarray(tracks, dtype=np.float64).reshape(-1, 4))
    result = np.empty((len(boxes), len(tracks)), dtype=np.float64)
    if not result.size:
        return result
    path = str(
        library
        or os.environ.get(
            "VISION_POSTPROCESS_LIBRARY",
            Path(__file__).with_name("libnative_postprocess.so"),
        )
    )
    lib = _matrix_libraries.get(path)
    if lib is None:
        lib = (C.PyDLL if os.environ.get("VISION_MINIMAL60") == "1" else C.CDLL)(path)
        lib.native_postprocess_iou_matrix.argtypes = [
            C.c_void_p,
            C.c_uint32,
            C.c_void_p,
            C.c_uint32,
            C.c_void_p,
        ]
        lib.native_postprocess_iou_matrix.restype = C.c_int
        _matrix_libraries[path] = lib
    code = lib.native_postprocess_iou_matrix(
        boxes.ctypes.data,
        len(boxes),
        tracks.ctypes.data,
        len(tracks),
        result.ctypes.data,
    )
    if code:
        raise ValueError(
            "Non-finite IoU coordinates" if code == -3 else "Native IoU matrix failed"
        )
    return result
