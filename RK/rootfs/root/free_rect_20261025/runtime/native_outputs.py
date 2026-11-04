from pathlib import Path
import ctypes as C
import os
import numpy as np


def unpack(outputs, classes):
    if len(outputs) == 2:
        p, logits = outputs
    elif len(outputs) == 1:
        raw = np.asarray(outputs[0], dtype=np.float32)
        if raw.ndim != 4 or raw.shape[0] != 1 or raw.shape[1] != 5 + classes:
            raise ValueError("Raw-head schema does not match the selected labels")
        z = raw[:, :5]
        exponent = np.exp(-np.abs(z))
        p = np.where(z >= 0, 1 / (1 + exponent), exponent / (1 + exponent))
        logits = raw[:, 5:]
    else:
        raise ValueError("Unsupported model output count")
    if (
        p.ndim != 4
        or p.shape[:2] != (1, 5)
        or logits.shape != (1, classes, *p.shape[2:])
    ):
        raise ValueError("Selected model output geometry mismatch")
    if (
        len(outputs) == 2
        and p.dtype == np.float32
        and logits.dtype == np.float32
        and p.flags.c_contiguous
        and logits.flags.c_contiguous
    ):
        error = _validate_legacy(p, logits)
        if error == 1:
            raise ValueError("Selected model produced non-finite detection outputs")
        if error == 2:
            raise ValueError("Selected model produced invalid probabilities")
        return p, logits
    if not np.isfinite(p).all() or not np.isfinite(logits).all():
        raise ValueError("Selected model produced non-finite detection outputs")
    if np.any(p < 0) or np.any(p > 1):
        raise ValueError("Selected model produced invalid probabilities")
    return p, logits


_validation_library = None


def _validate_legacy(p, logits):
    global _validation_library
    if _validation_library is None:
        lib = (C.PyDLL if os.environ.get("VISION_MINIMAL60") == "1" else C.CDLL)(
            str(Path(__file__).with_name("libnative512_geometry.so"))
        )
        lib.native512_geometry_api.restype = C.c_uint
        if lib.native512_geometry_api() != 1:
            raise RuntimeError("Selected512 validation API mismatch")
        lib.native512_validate_outputs.argtypes = [
            C.c_void_p,
            C.c_size_t,
            C.c_void_p,
            C.c_size_t,
        ]
        lib.native512_validate_outputs.restype = C.c_uint
        _validation_library = lib
    return _validation_library.native512_validate_outputs(
        p.ctypes.data, p.size, logits.ctypes.data, logits.size
    )


def validation_backend():
    return (
        "native_single_probability_and_logit_scan_for_contiguous_float32_legacy_pairs"
    )
