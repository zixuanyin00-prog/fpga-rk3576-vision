import cv2
import numpy as np
from .decoder import iou


def annotation_predictions(predict, image):

    candidates = []
    for scale in (1.0, 1.5):
        w, h = round(512 / scale), round(288 / scale)
        for y in sorted({0, (288 - h) // 2, 288 - h}):
            for x in sorted({0, (512 - w) // 2, 512 - w}):
                tile = image[y : y + h, x : x + w]
                if scale != 1.0:
                    tile = cv2.resize(tile, (512, 288), interpolation=cv2.INTER_LINEAR)
                for row in predict(tile):
                    a, b, c, d = row["bbox"]
                    if not 0 <= a < c <= 512 or not 0 <= b < d <= 288:
                        continue

                    boundary = scale != 1.0 and (a < 5 or b < 5 or c > 507 or d > 283)
                    box = [
                        a * w / 512 + x,
                        b * h / 288 + y,
                        c * w / 512 + x,
                        d * h / 288 + y,
                    ]
                    if (
                        not all(np.isfinite(box))
                        or box[2] <= box[0]
                        or box[3] <= box[1]
                    ):
                        continue
                    candidates.append(
                        dict(
                            row, bbox=box, proposal_scale=scale, tile_boundary=boundary
                        )
                    )
    selected = []
    for row in sorted(
        candidates,
        key=lambda r: (not r.get("tile_boundary"), r["objectness"]),
        reverse=True,
    ):
        if all(iou(row["bbox"], old["bbox"]) < 0.35 for old in selected):
            selected.append(row)
        if len(selected) >= 40:
            break
    return selected


def supplemental(existing, proposed):

    from .box_geometry import duplicate

    result = [dict(o, bbox=list(o["bbox"])) for o in existing]
    added = 0
    for candidate in proposed:
        b = candidate["bbox"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if any(duplicate(b, o["bbox"]) for o in result):
            continue

        result.append(
            dict(candidate, bbox=list(b), label=-2, needs_review=True, proposed=True)
        )
        added += 1
    return result, added
