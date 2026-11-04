MAGIC = 0x32435250
ABI = 1
IDENTITY = "shared_optional_rgb_v2"
FLAGS = (
    "gaussian",
    "histogram",
    "foreground",
    "binary_morphology",
    "sobel",
    "edge_cleanup",
    "edge_overlay",
    "background_shade",
    "feature_statistics",
    "grayscale_morphology",
    "edge_closing",
    "mask_boundary",
)
VIEWS = (
    "processed_rgb",
    "baseline_rgb",
    "foreground_mask",
    "clean_foreground_mask",
    "final_edges",
    "grayscale_morphology",
    "mask_inner_boundary",
)
DEFAULT_WORDS = (1, 0, 0xFF40, 3, 0x11080, 0x1AFFFF00, 0x180103, 0xF080, 32, 0)

FIELDS = {
    "foreground_low": (2, 0, 255, 255),
    "foreground_high": (2, 8, 255, 255),
    "foreground_mode": (2, 16, 3, 2),
    "foreground_invert": (2, 18, 1, 1),
    "binary_operation": (3, 0, 7, 4),
    "binary_shape": (3, 4, 3, 3),
    "sobel_threshold": (4, 0, 4095, 2040),
    "edge_neighbors": (4, 12, 15, 8),
    "edge_foreground_only": (4, 16, 1, 1),
    "overlay_rgb": (5, 0, 0xFFFFFF, 0xFFFFFF),
    "overlay_alpha": (5, 24, 7, 4),
    "background_level": (5, 27, 7, 4),
    "gray_operation": (6, 0, 7, 4),
    "gray_kernel5": (6, 4, 1, 1),
    "gray_fusion": (6, 8, 7, 4),
    "gray_delta_clip": (6, 16, 255, 255),
    "feature_luma": (7, 0, 255, 255),
    "feature_highlight": (7, 8, 255, 255),
    "gaussian_strength": (8, 0, 511, 256),
    "histogram_strength": (9, 0, 511, 256),
}


def effective_mask(words):
    if words[0] == 1:
        return 0
    mask = words[1]
    if not mask & (1 << 2):
        mask &= ~((1 << 3) | (1 << 7) | (1 << 11))
    if not words[3] & 7:
        mask &= ~(1 << 3)
    if not words[6] & 7:
        mask &= ~(1 << 9)
    if not mask & (1 << 4):
        mask &= ~((1 << 5) | (1 << 10))
    if not mask & ((1 << 4) | (1 << 11)):
        mask &= ~(1 << 6)
    return mask


def decode(words):
    w = list(words)
    mask = effective_mask(w)
    return dict(
        words=w,
        view=w[0],
        image_mode=VIEWS[w[0]],
        enabled={k: bool(w[1] & (1 << i)) for i, k in enumerate(FLAGS)},
        effective_enabled={k: bool(mask & (1 << i)) for i, k in enumerate(FLAGS)},
        parameters={
            k: (w[i] >> shift) & bits for k, (i, shift, bits, _) in FIELDS.items()
        },
    )


def validate_patch(patch):
    if (
        not isinstance(patch, dict)
        or not patch
        or set(patch) - {"view", "enabled", "parameters"}
    ):
        raise ValueError("Unknown processing patch")
    if "view" in patch and (
        type(patch["view"]) is not int or not 0 <= patch["view"] < len(VIEWS)
    ):
        raise ValueError("Invalid processing view")
    for key, allowed in [("enabled", FLAGS), ("parameters", FIELDS)]:
        if key not in patch:
            continue
        values = patch[key]
        if not isinstance(values, dict) or set(values) - set(allowed):
            raise ValueError("Unknown " + key)
        for name, value in values.items():
            if key == "enabled":
                if type(value) is not bool:
                    raise ValueError(name + " must be boolean")
            elif type(value) is not int or not 0 <= value <= FIELDS[name][3]:
                raise ValueError("Invalid " + name)
    return patch


def apply_patch(words, patch):
    validate_patch(patch)
    w = list(words)
    if "view" in patch:
        w[0] = patch["view"]
    for name, value in patch.get("enabled", {}).items():
        bit = 1 << FLAGS.index(name)
        w[1] = (w[1] | bit) if value else (w[1] & ~bit)
    for name, value in patch.get("parameters", {}).items():
        i, shift, mask, _ = FIELDS[name]
        w[i] = (w[i] & ~(mask << shift)) | (value << shift)
    if (w[2] >> 16) & 3 == 2 and (w[2] & 255) > ((w[2] >> 8) & 255):
        raise ValueError("Foreground low exceeds high")
    required = {
        2: 1 << 2,
        3: 1 << 2,
        4: 1 << 4,
        5: 1 << 9,
        6: (1 << 2) | (1 << 11),
    }.get(w[0], 0)
    if effective_mask(w) & required != required:
        raise ValueError("Diagnostic view requires its processing stage enabled")
    return w


def status(values):
    v = list(values)
    if v[:2] != [MAGIC, ABI] or v[3] != 10:
        raise ValueError("Unsupported PRC2 interface")
    return dict(
        magic=v[0],
        abi=v[1],
        supported_mask=v[2],
        word_count=v[3],
        requested_revision=v[4],
        effective_revision=v[5],
        effective_source_frame_id=v[6],
        pending=bool(v[7] & 1),
        fault=bool(v[7] & 2),
        source_valid=bool(v[7] & 4),
        rejected=v[8],
        requested=decode(v[9:19]),
        effective=decode(v[19:29]),
        jpeg_supported=False,
    )
