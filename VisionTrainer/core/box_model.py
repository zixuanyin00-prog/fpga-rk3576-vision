import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from .decoder import iou

ARCHITECTURE = "scratch-box-cnn-v1"


class ScratchBoxNet(nn.Module):
    def __init__(self):
        super().__init__()

        def block(a, b, stride=1):
            return nn.Sequential(nn.Conv2d(a, b, 3, stride, padding=1), nn.ReLU())

        self.features = nn.Sequential(
            block(3, 12, 2),
            block(12, 24, 2),
            block(24, 40, 2),
            block(40, 56),
            block(56, 56),
            block(56, 56),
        )
        self.head = nn.Conv2d(56, 5, 1)
        with torch.no_grad():
            self.head.bias.zero_()
            self.head.bias[0] = -3
            self.head.bias[1:3] = -2

    def forward(self, x):
        return self.head(self.features(x))


def tensor(image):
    return torch.from_numpy(image.transpose(2, 0, 1).copy()).float() / 127.5 - 1


def targets(objects, stride=8):
    height, width = 288 // stride, 512 // stride
    heat = np.zeros((height, width), np.float32)
    ignored = np.zeros_like(heat, dtype=bool)
    truth = []
    yy, xx = np.mgrid[:height, :width]
    for o in objects:
        a, b, c, d = o["bbox"]
        cx, cy = (a + c) / (2 * stride), (b + d) / (2 * stride)
        ix, iy = int(cx), int(cy)
        if o.get("ignore", False):
            ignored[
                max(0, int(b / stride)) : min(height, int(d / stride) + 1),
                max(0, int(a / stride)) : min(width, int(c / stride) + 1),
            ] = True
            continue
        sigma = max(0.65, min(c - a, d - b) / (4 * stride))
        heat = np.maximum(
            heat, np.exp(-((xx - ix) ** 2 + (yy - iy) ** 2) / (2 * sigma * sigma))
        ).astype(np.float32)
        truth.append([iy, ix, (c - a) / 512, (d - b) / 288, cx - ix, cy - iy])
    heat[ignored & (heat < 1)] = -1
    return heat, np.asarray(truth, np.float32).reshape(-1, 6)


def augment(image, objects, rng, plain=False, stride=8, scene=False):
    if plain:
        return tensor(image), targets(objects, stride)
    angle = (
        rng.uniform(-3, 3)
        if scene
        else rng.uniform(-18, 18) + (180 if rng.random() < 0.5 else 0)
    )
    matrix = cv2.getRotationMatrix2D(
        (256, 144), angle, rng.uniform(0.95, 1.05) if scene else rng.uniform(0.48, 1.25)
    )
    matrix[:, 2] += (
        [rng.uniform(-16, 16), rng.uniform(-8, 8)]
        if scene
        else [rng.uniform(-90, 90), rng.uniform(-65, 65)]
    )
    out = cv2.warpAffine(
        image,
        matrix,
        (512, 288),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=tuple(int(v) for v in np.median(image.reshape(-1, 3), axis=0)),
    )
    boxes = []
    for o in objects:
        a, b, c, d = o["bbox"]
        pts = (
            np.array([[a, b], [c, b], [c, d], [a, d]]) @ matrix[:, :2].T + matrix[:, 2]
        )
        box = np.r_[pts.min(0), pts.max(0)]
        clip = np.clip(box, [0, 0, 0, 0], [512, 288, 512, 288])
        area = np.maximum(0, clip[2:] - clip[:2]).prod()
        if area < 4:
            continue
        boxes.append(
            dict(
                bbox=clip.tolist(),
                ignore=bool(
                    o.get("ignore")
                    or area < 0.98 * np.maximum(0, box[2:] - box[:2]).prod()
                ),
            )
        )
    out = np.clip(
        (out.astype(np.float32) - 90) * rng.uniform(0.65, 1.35)
        + 90
        + rng.uniform(-30, 30),
        0,
        255,
    )
    out = np.clip(
        out * rng.uniform(0.85, 1.15, (1, 1, 3)) + rng.normal(0, 2, out.shape), 0, 255
    ).astype(np.uint8)
    if rng.random() < 0.2:
        out = cv2.GaussianBlur(out, (3, 3), 0.5)
    return tensor(out), targets(boxes, stride)


def loss_for(z, truth):
    pr = z[:, 0].sigmoid().clamp(1e-5, 1 - 1e-5)
    heat = torch.from_numpy(np.stack([t[0] for t in truth])).to(z.device)
    pos = heat == 1
    loss = (
        -((1 - pr) ** 2) * pr.log() * pos
        - pr**2
        * (1 - pr).log()
        * (1 - heat.clamp_min(0)) ** 4
        * ((heat >= 0) & (heat < 1))
    ).sum() / pos.sum().clamp_min(1)
    bp = []
    bt = []
    for i, (_, rows) in enumerate(truth):
        for row in rows:
            y, x = map(int, row[:2])
            bp.append(z[i, 1:, y, x])
            bt.append(row[2:])
    if bp:
        q = torch.stack(bp).sigmoid()
        t = torch.from_numpy(np.array(bt)).to(z.device)
        loss = (
            loss
            + 6
            * F.smooth_l1_loss(
                (q[:, :2].clamp_min(0.001) / t[:, :2]).log(),
                torch.zeros_like(t[:, :2]),
                beta=0.1,
            )
            + 1.5 * F.l1_loss(q[:, 2:], t[:, 2:])
        )
    return loss


@torch.inference_mode()
def predict(net, image, threshold=0.6):
    p = net(tensor(image)[None].to(next(net.parameters()).device)).sigmoid()[0].cpu()
    heat = p[0]
    peaks = (heat == F.max_pool2d(heat[None, None], 3, 1, 1)[0, 0]) & (
        heat >= threshold
    )
    stride = 512 / heat.shape[1]
    rows = []
    for y, x in peaks.nonzero().tolist():
        score = float(heat[y, x])
        w, h, dx, dy = p[1:, y, x].tolist()
        cx, cy = (x + dx) * stride, (y + dy) * stride
        box = np.clip(
            [cx - w * 256, cy - h * 144, cx + w * 256, cy + h * 144],
            [0, 0, 0, 0],
            [512, 288, 512, 288],
        ).tolist()
        if box[2] - box[0] < 8 or box[3] - box[1] < 6:
            continue
        rows.append(dict(bbox=box, objectness=score))
    selected = []
    for row in sorted(rows, key=lambda r: r["objectness"], reverse=True):
        if all(iou(row["bbox"], old["bbox"]) < 0.35 for old in selected):
            selected.append(row)
        if len(selected) >= 40:
            break
    return selected
