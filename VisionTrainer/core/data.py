import cv2
import numpy as np
import torch


def tensor(im):
    a = (im.astype(np.float32) - [123.675, 116.28, 103.53]) / [58.395, 57.12, 57.375]
    return torch.from_numpy(a.astype(np.float32).transpose(2, 0, 1).copy())


def targets(objects):
    heat = np.zeros((72, 128), np.float32)
    ignored = np.zeros_like(heat, dtype=bool)
    truth = []
    yy, xx = np.mgrid[:72, :128]
    for o in objects:
        x0, y0, x1, y1 = o["bbox"]
        cx = (x0 + x1) / 8
        cy = (y0 + y1) / 8
        ix, iy = int(cx), int(cy)
        if o["label"] == -3:
            continue
        if o["label"] < 0:
            ignored[
                max(0, int(y0 / 4)) : min(72, int(y1 / 4) + 1),
                max(0, int(x0 / 4)) : min(128, int(x1 / 4) + 1),
            ] = True
            continue
        sigma = max(0.6, min(x1 - x0, y1 - y0) / 24)
        heat = np.maximum(
            heat, np.exp(-((xx - ix) ** 2 + (yy - iy) ** 2) / (2 * sigma * sigma))
        ).astype(np.float32)
        truth.append(
            [iy, ix, (x1 - x0) / 512, (y1 - y0) / 288, cx - ix, cy - iy, o["label"]]
        )
    heat[ignored & (heat < 1)] = -1
    return heat, np.asarray(truth, np.float32).reshape(-1, 7)


def augment(image, objects, rng, k, labels):
    image = image.copy()
    objects = [dict(o, bbox=list(o["bbox"])) for o in objects]
    if not any(name.startswith("front_") for name in labels):

        return np.clip(
            image.astype(np.float32) * rng.uniform(0.96, 1.04), 0, 255
        ).astype(np.uint8), targets(objects)

    def flip(i):
        name = labels[i]
        if name.startswith("front_upright"):
            return labels.index(name.replace("front_upright", "front_inverted"))
        if name.startswith("front_inverted"):
            return labels.index(name.replace("front_inverted", "front_upright"))
        return i

    for o in objects:
        if (
            o["label"] < 0
            or not labels[o["label"]].startswith("front_")
            or rng.random() >= 0.5
        ):
            continue
        x0, y0, x1, y1 = o["bbox"]
        w, h = x1 - x0, y1 - y0
        xa, xb = int(x0 + 0.16 * w), int(x1 - 0.16 * w)
        ya, yb = int(y0 + 0.08 * h), int(y1 - 0.08 * h)
        image[ya:yb, xa:xb] = image[ya:yb, xa:xb][::-1, ::-1].copy()
        o["label"] = flip(o["label"])
    turn = bool(k % 2)
    angle = 0.0 if k % 4 == 0 else float(rng.uniform(-7, 7))
    matrix = cv2.getRotationMatrix2D((256, 144), angle + (180 if turn else 0), 1)
    matrix[:, 2] += [rng.uniform(-75, 75) if k else 0, rng.uniform(-28, 28) if k else 0]
    im = cv2.warpAffine(
        image,
        matrix,
        (512, 288),
        borderValue=tuple(int(v) for v in np.median(image.reshape(-1, 3), axis=0)),
    )
    obs = []
    for o in objects:
        x0, y0, x1, y1 = o["bbox"]
        pts = (
            np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) @ matrix[:, :2].T
            + matrix[:, 2]
        )
        box = np.r_[pts.min(0), pts.max(0)]
        clip = np.clip(box, [0, 0, 0, 0], [512, 288, 512, 288])
        area = np.maximum(0, clip[2:] - clip[:2]).prod()
        if area < 1:
            continue
        label = o["label"]
        if area < 0.98 * np.maximum(0, box[2:] - box[:2]).prod():
            label = -1
        if turn and label >= 0:
            label = flip(label)
        obs.append(dict(bbox=clip.tolist(), label=label))
    if k % 3:
        im = np.clip(
            (
                (im.astype(np.float32) - 64) * rng.uniform(0.85, 1.15)
                + 64
                + rng.uniform(-9, 14)
            )
            * rng.uniform(0.94, 1.06, (1, 1, 3)),
            0,
            255,
        ).astype(np.uint8)
    if k % 4 == 3:
        im = np.clip(
            im.astype(np.float32) + rng.normal(0, 1.5, im.shape), 0, 255
        ).astype(np.uint8)
    return im, targets(obs)
