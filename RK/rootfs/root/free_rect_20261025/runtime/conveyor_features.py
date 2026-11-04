from pathlib import Path
import ctypes as C
import os
import numpy as np

_lib = None


def library():
    global _lib
    if _lib is None:
        lib = (C.PyDLL if os.environ.get("VISION_MINIMAL60") == "1" else C.CDLL)(
            str(Path(__file__).with_name("libconveyor_features.so"))
        )
        lib.conveyor_features_api_version.restype = C.c_int
        if lib.conveyor_features_api_version() != 1:
            raise RuntimeError("Real feature API mismatch")
        lib.conveyor_features_opencv_version.restype = C.c_char_p
        lib.conveyor_features_extract.argtypes = [
            C.c_void_p,
            C.c_int,
            C.c_int,
            C.c_void_p,
            C.c_int,
            C.c_double,
            C.c_double,
            C.c_int,
            C.c_void_p,
            C.c_void_p,
            C.c_void_p,
        ]
        lib.conveyor_features_extract.restype = C.c_int
        _lib = lib
    return _lib


def backend():
    lib = library()
    return dict(
        backend="native_batch_original_real_pixel_features",
        api=1,
        opencv=lib.conveyor_features_opencv_version().decode(),
        optical_flow=False,
    )


def extract(gray, boxes, origin, mode):

    if gray.dtype != np.uint8 or gray.ndim != 2:
        raise ValueError("Real features need uint8 grayscale")
    values = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    if len(values) > 256 or not np.isfinite(values).all():
        raise ValueError("Invalid real feature boxes")
    if not len(values):
        return []
    gray = np.ascontiguousarray(gray)
    values = np.ascontiguousarray(values)
    valid = np.zeros(len(values), np.uint8)
    stats = np.zeros((len(values), 4), np.float32)
    vectors = (
        np.empty((len(values), 384), np.float32) if mode else np.empty(0, np.float32)
    )
    rc = library().conveyor_features_extract(
        gray.ctypes.data,
        gray.shape[1],
        gray.shape[0],
        values.ctypes.data,
        len(values),
        origin[0],
        origin[1],
        mode,
        valid.ctypes.data,
        stats.ctypes.data,
        vectors.ctypes.data,
    )
    if rc:
        raise RuntimeError("Native real feature extraction rejected input: " + str(rc))
    if mode:
        return [
            (
                dict(mean=float(stats[i, 0]), std=float(stats[i, 1]), vector=vectors[i])
                if valid[i]
                else None
            )
            for i in range(len(values))
        ]
    result = []
    for i, okay in enumerate(valid):
        if not okay:
            result.append(dict(accepted=False, reason="insufficient_visible_pixels"))
            continue

        yedge, xedge, left, right = map(float, stats[i])
        accepted = yedge >= 11.5 and xedge >= max(6.0, 0.28 * yedge)
        result.append(
            dict(
                accepted=accepted,
                reason=(
                    "rectangular_face_edges" if accepted else "rail_or_gap_structure"
                ),
                horizontal_boundary=yedge,
                vertical_boundary=xedge,
                side_left=left,
                side_right=right,
                side_edges_accepted=min(left, right) >= 6.0,
            )
        )
    return result
