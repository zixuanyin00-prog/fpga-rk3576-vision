import numpy as np
import torch
from torch.nn import functional as F
from .native_frames import finalize

ARCHITECTURE = "native-scene-template-v1"


def features(image, device):
    rgb = (
        torch.from_numpy(np.asarray(image).copy())
        .to(device=device, dtype=torch.float32)
        .permute(2, 0, 1)[None]
        / 255
    )
    grey = rgb.mean(1, keepdim=True)
    kernel = (
        torch.tensor(
            [[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]], device=device
        )[None, None]
        / 8
    )
    return torch.cat(
        [
            grey,
            F.conv2d(grey, kernel, padding=1),
            F.conv2d(grey, kernel.transpose(2, 3), padding=1),
        ],
        1,
    )


def window_sum(x, h, w):
    integral = F.pad(x.cumsum(2).cumsum(3), (1, 0, 1, 0))
    return (
        integral[:, :, h:, w:]
        - integral[:, :, :-h, w:]
        - integral[:, :, h:, :-w]
        + integral[:, :, :-h, :-w]
    )


@torch.inference_mode()
def locate(image, weights, device, threshold=None):
    feat = features(image, device)
    height, width = feat.shape[-2:]
    rows = []
    cutoff = float(weights.get("threshold", 0.55) if threshold is None else threshold)
    for index, item in enumerate(weights["templates"]):

        patch = item["features"].to(device)
        h, w = patch.shape[-2:]
        n = h * w
        padded = F.pad(
            feat, (w // 2, w - 1 - w // 2, h // 2, h - 1 - h // 2), mode="replicate"
        )
        mean = patch.mean((-2, -1), keepdim=True)
        centered = patch - mean
        std = centered.square().sum((-2, -1)).sqrt().clamp_min(1e-5)
        sums = window_sum(padded, h, w)
        squared = window_sum(padded.square(), h, w)
        local_std = (squared - sums.square() / n).clamp_min(1e-8).sqrt()
        correlation = F.conv2d(padded, centered.reshape(3, 1, h, w), groups=3) / (
            local_std * std.reshape(1, 3, 1, 1)
        )
        score = (
            correlation
            * torch.tensor([0.4, 0.3, 0.3], device=device)[None, :, None, None]
        ).sum(1)[0]

        low, high = weights["center_y_band"]
        yy = torch.arange(height, device=device)[:, None]
        score = score.masked_fill((yy < low) | (yy > high), -1)
        peaks = (score == F.max_pool2d(score[None, None], 21, 1, 10)[0, 0]) & (
            score >= cutoff
        )
        locations = peaks.nonzero()
        values = score[peaks]
        if len(values) > 80:
            values, order = values.topk(80)
            locations = locations[order]
        for (y, x), value in zip(locations.cpu().tolist(), values.cpu().tolist()):
            a, b, c, d = x - w // 2, y - h // 2, x - w // 2 + w, y - h // 2 + h
            clipped = [max(0, a), max(0, b), min(width, c), min(height, d)]
            if (clipped[2] - clipped[0]) * (clipped[3] - clipped[1]) < w * h * 0.4:
                continue
            rows.append(
                dict(
                    bbox=clipped,
                    score=float(value),
                    label=-2,
                    proposed=True,
                    needs_review=True,
                    proposal_source=ARCHITECTURE,
                    template=index,
                    tile_boundary=a < 0 or b < 0 or c > width or d > height,
                )
            )
    return finalize(rows, (width, height))


def fit(root, frames, device):
    from .native_frames import load_native

    templates = []
    centers = []
    heights = []
    for f in frames:
        image, _ = load_native(root, f)
        width, height = image.size
        feat = features(image, device)
        for o in f["objects"]:
            if o["label"] < 0 or o.get("needs_review") or o.get("proposed"):
                continue
            a, b, c, d = [
                round(o["bbox"][k] * [width / 512, height / 288][k % 2])
                for k in range(4)
            ]
            if c - a < 8 or d - b < 8:
                continue
            templates.append(
                dict(
                    features=feat[0, :, b:d, a:c].contiguous().cpu(),
                    source_frame=f["id"],
                    bbox_native=[a, b, c, d],
                )
            )
            centers.append((b + d) / 2)
            heights.append(d - b)
    if not templates:
        raise ValueError("缺少已确认完整元件小框")
    return dict(
        templates=templates,
        threshold=0.55,
        center_y_band=[
            min(centers) - float(np.median(heights)) * 0.6,
            max(centers) + float(np.median(heights)) * 0.6,
        ],
    )
