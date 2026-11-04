import copy
import cv2
import numpy as np
import torch
from torch.nn import functional as F

POLICY = dict(
    gain=[0.78, 1.22],
    gamma=[0.90, 1.10],
    offset=[-8, 8],
    red_blue_gain=[0.92, 1.08],
    gaussian_sigma=[0, 2.5],
    shot_sigma_at_white=1.5,
    motion_kernel=3,
    motion_mix_max=0.45,
    motion_angles=[0, 45, 90, 135],
    max_new_clip_fraction=0.04,
    min_detail_retention=0.60,
    min_contrast_retention=0.65,
    consistency_weight=0.30,
    teacher_confidence=0.85,
)


def curriculum(step, steps, fit_steps, fit_pass):
    if not fit_pass or step < fit_steps:
        return 0.0, 0.0
    ramp = min(1.0, (step - fit_steps + 1) / max(1, int(steps * 0.35)))

    if step >= int(steps * 0.85):
        return 0.35, 0.10
    return ramp, POLICY["consistency_weight"] * ramp


def perturb(image, rng, strength=1.0, kind="mixed"):

    s = float(np.clip(strength, 0, 1))
    x = image.astype(np.float32)
    if not s:
        return image.copy(), dict(kind=kind, strength=0.0)
    meta = dict(kind=kind, strength=s)
    if kind in ("mixed", "brightness"):
        gain = 1 + s * rng.uniform(-0.22, 0.22)
        gamma = 1 + s * rng.uniform(-0.10, 0.10)
        offset = s * rng.uniform(-8, 8)
        x = 255 * np.power(x / 255, gamma) * gain + offset
        meta.update(gain=gain, gamma=gamma, offset=offset)
    if kind in ("mixed", "temperature"):
        t = s * rng.uniform(-0.08, 0.08)
        gains = np.array([1 + t, 1, 1 - t], np.float32)
        x = x * gains
        meta["rgb_gains"] = gains.tolist()
    if kind in ("mixed", "motion"):
        angle = int(rng.choice([0, 45, 90, 135]))
        mix = s * rng.uniform(0.15, 0.45)
        kernel = np.zeros((3, 3), np.float32)
        if angle == 0:
            kernel[1, :] = 1 / 3
        elif angle == 90:
            kernel[:, 1] = 1 / 3
        elif angle == 45:
            np.fill_diagonal(kernel, 1 / 3)
        else:
            kernel = np.fliplr(np.eye(3, dtype=np.float32)) / 3
        x = (1 - mix) * x + mix * cv2.filter2D(
            x, -1, kernel, borderType=cv2.BORDER_REFLECT_101
        )
        meta.update(motion_angle=angle, motion_mix=mix)
    if kind in ("mixed", "noise"):
        sigma = s * rng.uniform(0, 2.5)
        noise_sigma = np.sqrt(sigma * sigma + (s * 1.5) ** 2 * np.clip(x / 255, 0, 1))
        x = x + rng.normal(size=x.shape).astype(np.float32) * noise_sigma
        meta["read_noise_sigma"] = sigma
    return np.clip(x, 0, 255).astype(np.uint8), meta


def readable_mask(original, augmented, objects, labels):

    safe = []
    reasons = []
    for o in objects:
        label = o["label"]
        if label < 0 or labels[label] == "unreadable":
            safe.append(False)
            reasons.append("ignored_or_unreadable")
            continue
        x0, y0, x1, y1 = o["bbox"]
        dx = (x1 - x0) * 0.12
        dy = (y1 - y0) * 0.12
        x0, x1 = max(0, int(x0 + dx)), min(512, int(x1 - dx))
        y0, y1 = max(0, int(y0 + dy)), min(288, int(y1 - dy))
        a = original[y0:y1, x0:x1]
        b = augmented[y0:y1, x0:x1]
        if a.size == 0:
            safe.append(False)
            reasons.append("empty")
            continue
        ga = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32)
        gb = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY).astype(np.float32)

        ga = cv2.GaussianBlur(ga, (3, 3), 0.6)
        gb = cv2.GaussianBlur(gb, (3, 3), 0.6)
        grad = lambda g: np.hypot(
            cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
        )
        ea, eb = grad(ga), grad(gb)
        edge = ea >= np.percentile(ea, 75)
        detail = float(eb[edge].mean() / max(1e-3, ea[edge].mean()))
        contrast = float(np.std(gb) / max(1e-3, np.std(ga)))
        new_clip = float(
            np.mean(np.any(((b <= 1) | (b >= 254)) & ((a > 1) & (a < 254)), axis=2))
        )
        ok = (
            new_clip <= 0.04
            and detail >= 0.60
            and contrast >= 0.65
            and np.std(ga) >= 2.0
        )
        safe.append(bool(ok))
        reasons.append(dict(new_clip=new_clip, detail=detail, contrast=contrast))
    return safe, reasons


def augmented_target(image, objects, rng, strength, labels):
    aug, meta = perturb(image, rng, strength)
    safe, reasons = readable_mask(image, aug, objects, labels)
    obs = copy.deepcopy(objects)
    for o, ok in zip(obs, safe):
        if not ok:
            o["label"] = -1
    meta.update(safe=safe, quality=reasons)
    return aug, obs, meta


def consistency_loss(clean, augmented, truth, labels, confidence=0.85):

    terms = []
    used = 0
    for bi, (_, rows) in enumerate(truth):
        for row in rows:
            y, x, label = int(row[0]), int(row[1]), int(row[6])
            if label < 0 or labels[label] == "unreadable":
                continue
            teacher = clean[bi, 5:, y, x].detach().softmax(0)
            if int(teacher.argmax()) != label or float(teacher.max()) < confidence:
                continue
            if float(clean[bi, 0, y, x].detach().sigmoid()) < 0.5:
                continue
            kl = F.kl_div(
                augmented[bi, 5:, y, x].log_softmax(0), teacher, reduction="sum"
            )
            loc = F.smooth_l1_loss(
                augmented[bi, :5, y, x].sigmoid(),
                clean[bi, :5, y, x].detach().sigmoid(),
            )
            terms.append(kl + loc)
            used += 1
    return (torch.stack(terms).mean() if terms else augmented.sum() * 0), used


def assert_split(job):

    train = job["train"]
    val = job["validation"]
    for key in ("id", "file", "sha256"):
        a = {f[key] for f in train}
        b = {f[key] for f in val}
        if a & b:
            raise ValueError("Train/validation overlap: " + key)
    for keys in [
        ("boot_id", "source_frame_id"),
        ("capture_session", "source_frame_id"),
        ("parent_sha256",),
        ("original_id",),
        ("passage_id",),
    ]:

        def values(frames):
            return {
                tuple(f[k] for k in keys)
                for f in frames
                if all(f.get(k) is not None for k in keys)
            }

        if values(train) & values(val):
            raise ValueError("Source family crosses split: " + str(keys))
    if any(f.get("role") == "test" for f in train + val):
        raise ValueError("Test frames in fitting/selection")
