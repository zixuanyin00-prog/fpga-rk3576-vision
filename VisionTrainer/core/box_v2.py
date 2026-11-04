import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from .box_model import tensor, targets, predict
from .proposals import annotation_predictions
from .decoder import iou

ARCHITECTURE = "scratch-box-fpn-verifier-v2"


def block(a, b, stride=1):
    return nn.Sequential(nn.Conv2d(a, b, 3, stride, padding=1), nn.ReLU())


class BoxNetV2(nn.Module):
    def __init__(self):
        super().__init__()
        self.stem = nn.Sequential(block(3, 12, 2), block(12, 24, 2))
        self.mid = nn.Sequential(block(24, 40, 2), block(40, 40))
        self.coarse = nn.Sequential(block(40, 56, 2), block(56, 56), block(56, 56))
        self.fuse = nn.Sequential(
            nn.Conv2d(120, 48, 1), nn.ReLU(), block(48, 48), nn.Conv2d(48, 5, 1)
        )
        with torch.no_grad():
            self.fuse[-1].bias.zero_()
            self.fuse[-1].bias[0] = -3
            self.fuse[-1].bias[1:3] = -2

    def forward(self, x):
        fine = self.stem(x)
        mid = self.mid(fine)
        coarse = self.coarse(mid)
        return self.fuse(
            torch.cat(
                [
                    fine,
                    F.interpolate(
                        mid, size=fine.shape[-2:], mode="bilinear", align_corners=False
                    ),
                    F.interpolate(
                        coarse,
                        size=fine.shape[-2:],
                        mode="bilinear",
                        align_corners=False,
                    ),
                ],
                1,
            )
        )


class PatchVerifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            block(3, 12, 2),
            block(12, 24, 2),
            block(24, 40, 2),
            block(40, 40),
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(40, 1),
        )

    def forward(self, x):
        return self.net(x).flatten()


class SpatialVerifier(nn.Module):

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            block(3, 12, 2),
            block(12, 24, 2),
            block(24, 40, 2),
            block(40, 40),
            nn.AdaptiveAvgPool2d((4, 6)),
            nn.Flatten(),
            nn.Linear(40 * 4 * 6, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )

    def forward(self, x):
        return self.net(x).flatten()


def make_verifier(meta):
    return (
        SpatialVerifier()
        if meta.get("verifier_layout") == "spatial4x6"
        else PatchVerifier()
    )


def locate_configured(net, verifier, image, meta):
    c = meta.get("inference", {})
    return locate(
        net,
        verifier,
        image,
        threshold=c.get("detector_threshold", 0.30),
        verify_threshold=c.get("verifier_threshold", 0.35),
        joint_threshold=c.get("joint_threshold", 0.35),
        native_only=c.get("native_tiles_only", False),
    )


def crop(image, bbox, pad=0.08):
    a, b, c, d = bbox
    w, h = c - a, d - b
    a, b = max(0, int(a - pad * w)), max(0, int(b - pad * h))
    c, d = min(512, int(c + pad * w + 1)), min(288, int(d + pad * h + 1))
    return cv2.resize(image[b:d, a:c], (96, 64), interpolation=cv2.INTER_AREA)


def intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )


def negative_box(objects, rng, large=False):
    for _ in range(150):
        w = float(rng.uniform(150, 260) if large else rng.uniform(45, 150))
        h = w * float(rng.uniform(0.4, 0.9))
        h = min(h, 220)
        x = float(rng.uniform(0, 512 - w))
        y = float(rng.uniform(0, 288 - h))
        box = [x, y, x + w, y + h]
        if all(intersection(box, o["bbox"]) == 0 for o in objects):
            return box
    return None


def photometric(image, rng):
    im = np.clip(
        (image.astype(np.float32) - 90) * rng.uniform(0.65, 1.35)
        + 90
        + rng.uniform(-25, 25),
        0,
        255,
    )
    return np.clip(
        im * rng.uniform(0.85, 1.15, (1, 1, 3)) + rng.normal(0, 2, im.shape), 0, 255
    ).astype(np.uint8)


def background(image, objects, rng):
    b = negative_box(objects, rng, large=True)
    if b is None:
        return None
    a, y, c, d = map(int, b)
    im = cv2.resize(image[y:d, a:c], (512, 288))
    return tensor(photometric(im, rng)), targets([], 4)


def verifier_examples(data, rng, count=120, positive_only=False):
    examples = []
    for image, objects in data:
        for o in objects:
            if o.get("ignore"):
                continue
            for _ in range(count // 6):
                b = np.array(o["bbox"], float)
                w, h = b[2] - b[0], b[3] - b[1]
                dx, dy = rng.uniform(-0.06, 0.06, 2) * [w, h]
                b += np.array([dx, dy, dx, dy])
                b = np.clip(b, [0, 0, 1, 1], [511, 287, 512, 288])
                patch = crop(image, b, pad=float(rng.uniform(0.02, 0.16)))
                if rng.random() < 0.5:
                    patch = patch[::-1, ::-1].copy()
                examples.append((tensor(photometric(patch, rng)), 1.0))
        if positive_only:
            continue
        for _ in range(count):
            b = negative_box(objects, rng)
            if b is not None:
                examples.append((tensor(photometric(crop(image, b), rng)), 0.0))

        for o in objects:
            a, b, c, d = o["bbox"]
            w, h = c - a, d - b
            for _ in range(16):
                dx, dy = (
                    (float(rng.choice([-1, 1])) * w * rng.uniform(0.6, 0.95), 0.0)
                    if rng.random() < 0.5
                    else (0.0, float(rng.choice([-1, 1])) * h * rng.uniform(0.6, 0.95))
                )
                box = np.clip(
                    [a + dx, b + dy, c + dx, d + dy], [0, 0, 0, 0], [512, 288, 512, 288]
                ).tolist()
                if box[2] - box[0] < w * 0.7 or box[3] - box[1] < h * 0.7:
                    continue
                if max((iou(box, t["bbox"]) for t in objects), default=0) >= 0.27:
                    continue
                examples.append((tensor(photometric(crop(image, box), rng)), 0.0))
    return examples


@torch.inference_mode()
def locate(
    net,
    verifier,
    image,
    threshold=0.30,
    verify_threshold=0.35,
    joint_threshold=0.35,
    native_only=False,
):

    candidates = (
        predict(net, image, threshold)
        if native_only
        else annotation_predictions(lambda im: predict(net, im, threshold), image)
    )
    if not candidates:
        return []
    scores = (
        verifier(
            torch.stack([tensor(crop(image, r["bbox"])) for r in candidates]).to(
                next(verifier.parameters()).device
            )
        )
        .sigmoid()
        .cpu()
        .tolist()
    )
    return [
        dict(r, verifier_score=s)
        for r, s in zip(candidates, scores)
        if s >= verify_threshold and s * r["objectness"] >= joint_threshold
    ]
