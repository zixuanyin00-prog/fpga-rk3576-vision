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
    if not np.isfinite(p).all() or not np.isfinite(logits).all():
        raise ValueError("Selected model produced non-finite detection outputs")
    if np.any(p < 0) or np.any(p > 1):
        raise ValueError("Selected model produced invalid probabilities")
    return p, logits
