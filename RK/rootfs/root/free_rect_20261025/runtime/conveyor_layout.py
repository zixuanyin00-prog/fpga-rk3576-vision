from pathlib import Path
import ctypes as C
import os
import numpy as np

_library = None
_error = None


def library():
    global _library, _error
    if _library is not None:
        return _library
    if _error is not None:
        return None
    try:
        lib = (C.PyDLL if os.environ.get("VISION_MINIMAL60") == "1" else C.CDLL)(
            str(Path(__file__).resolve().with_name("libconveyor_layout.so"))
        )
        lib.conveyor_layout_api_version.restype = C.c_int
        if lib.conveyor_layout_api_version() != 1:
            raise RuntimeError("Conveyor layout API mismatch")
        lib.conveyor_layout_select.argtypes = [
            C.c_void_p,
            C.c_int,
            C.c_int,
            C.c_int,
        ] + [C.c_void_p] * 4
        lib.conveyor_layout_select.restype = C.c_int
        _library = lib
    except Exception as exc:
        _error = str(exc)
    return _library


def backend():
    return dict(
        backend=(
            "native_exact_real_layout_scoring"
            if _library is not None
            else "reference_python"
        ),
        load_error=_error,
        optical_flow=False,
    )


def select(pairs, old_count, new_count):
    lib = library()
    if lib is None:
        raise RuntimeError(_error or "Native real layout scorer unavailable")
    values = np.ascontiguousarray(pairs, dtype=np.float64)
    summary = np.empty(6, np.float64)
    selected = np.empty(len(pairs), np.int32)
    quality = np.empty(len(pairs), np.float64)
    ambiguous = np.zeros(new_count, np.int32)
    n = lib.conveyor_layout_select(
        values.ctypes.data,
        len(pairs),
        old_count,
        new_count,
        summary.ctypes.data,
        selected.ctypes.data,
        quality.ctypes.data,
        ambiguous.ctypes.data,
    )
    if n < 0:
        raise ValueError("Native real layout validation rejected input: " + str(n))
    return dict(
        score=float(summary[0]),
        runner=float(summary[1]),
        dx=float(summary[2]),
        dy=float(summary[3]),
        width=float(summary[4]),
        required_margin=float(summary[5]),
        chosen=[(float(quality[k]), pairs[int(selected[k])]) for k in range(n)],
        ambiguous_indices=np.flatnonzero(ambiguous).tolist(),
    )
