from pathlib import Path
import ctypes as C
import os
import numpy as np

_lib = None
_assignment_calls = 0


def library():
    global _lib
    if _lib is None:
        lib = (C.PyDLL if os.environ.get("VISION_MINIMAL60") == "1" else C.CDLL)(
            str(Path(__file__).resolve().with_name("libconveyor_match.so"))
        )
        lib.conveyor_match_api_version.restype = C.c_int
        if lib.conveyor_match_api_version() != 1:
            raise RuntimeError("Conveyor numeric matching API mismatch")
        lib.conveyor_match_assign.argtypes = (
            [C.c_void_p] * 3 + [C.c_int, C.c_int, C.c_double] + [C.c_void_p] * 2
        )
        lib.conveyor_match_assign.restype = C.c_int
        _lib = lib
    return _lib


def ordered_assignment(costs, observed_positions, track_orders, unmatched=0.88):
    global _assignment_calls
    n = len(costs)
    m = len(track_orders)
    if not n:
        return [], {}
    if not m:
        return [None] * n, {}
    table = np.ascontiguousarray(costs, dtype=np.float64)
    positions = np.ascontiguousarray(observed_positions, dtype=np.float64)
    orders = np.ascontiguousarray(track_orders, dtype=np.float64)
    matched = np.empty(n, np.int32)
    margins = np.empty(n, np.float64)
    if table.shape != (n, m) or positions.shape != (n,) or n > 256 or m > 256:
        raise ValueError("Invalid bounded matching shape")
    rc = library().conveyor_match_assign(
        table.ctypes.data,
        positions.ctypes.data,
        orders.ctypes.data,
        n,
        m,
        float(unmatched),
        matched.ctypes.data,
        margins.ctypes.data,
    )
    if rc:
        raise RuntimeError("Native ordered matching rejected input " + str(rc))
    _assignment_calls += 1
    matched = matched.tolist()
    margins = margins.tolist()
    return [None if v < 0 else v for v in matched], {
        i: margins[i] for i, v in enumerate(matched) if v >= 0
    }


def backend():
    return dict(
        backend="native_exact_ordered_assignment",
        loaded=_lib is not None,
        assignment_calls=_assignment_calls,
        api=1,
        optical_flow=False,
    )
