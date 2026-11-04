import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import torch
from PIL import Image, ImageDraw
from core import store as db, box_v2
from core.box_model import ScratchBoxNet, predict
from core.box_assistant import development_indices
from core.proposals import annotation_predictions
from core.decoder import iou


def score(objects, found):
    truth = [o for o in objects if not o.get("ignore")]
    used = set()
    matched = 0
    miss = []
    for g in truth:
        choices = sorted(
            (iou(g["bbox"], d["bbox"]), j) for j, d in enumerate(found) if j not in used
        )
        if choices and choices[-1][0] >= 0.5:
            matched += 1
            used.add(choices[-1][1])
        else:
            miss.append(g["bbox"])
    extra = [
        j
        for j, d in enumerate(found)
        if j not in used
        and not any(
            o.get("ignore") and iou(o["bbox"], d["bbox"]) >= 0.2 for o in objects
        )
    ]
    return dict(
        targets=len(truth),
        matched=matched,
        missed=len(truth) - matched,
        extra=len(extra),
        missed_boxes=miss,
        extra_indices=extra,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("job")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("output")
    a = p.parse_args()
    torch.set_num_threads(2)
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    job = db.read(a.job)
    old = ScratchBoxNet().eval()
    old.load_state_dict(torch.load(a.old, map_location="cpu", weights_only=True))
    weights = torch.load(a.new, map_location="cpu", weights_only=True)
    net = box_v2.BoxNetV2().eval()
    net.load_state_dict(weights["detector"])
    verifier = box_v2.make_verifier(db.read(Path(a.new).parent / "result.json")).eval()
    verifier.load_state_dict(weights["verifier"])
    rows = []
    panels = []
    for i in development_indices(job["frames"]):
        f = job["frames"][i]
        raw = Path(f["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == f["sha256"]
        im = np.frombuffer(raw, np.uint8).reshape(288, 512, 3)
        predictions = [
            annotation_predictions(lambda x: predict(old, x), im),
            box_v2.locate_configured(
                net, verifier, im, db.read(Path(a.new).parent / "result.json")
            ),
        ]
        predictions = [
            [
                r
                for r in rs
                if r["bbox"][0] >= 4
                and r["bbox"][1] >= 4
                and r["bbox"][2] <= 508
                and r["bbox"][3] <= 284
            ]
            for rs in predictions
        ]
        row = dict(
            index=i,
            source=f["source"],
            frame=f.get("frame", f.get("file")),
            sha256=f["sha256"],
        )
        panel = Image.new("RGB", (1024, 320))
        draw = ImageDraw.Draw(panel)
        for j, (name, rs) in enumerate(zip(("v1", "v2"), predictions)):
            row[name] = dict(score(f["objects"], rs), predictions=rs)
            pic = Image.fromarray(im)
            d = ImageDraw.Draw(pic)
            for o in f["objects"]:
                d.rectangle(
                    o["bbox"], outline="gray" if o.get("ignore") else "#00ff88", width=2
                )
            for k, r in enumerate(rs):
                d.rectangle(r["bbox"], outline="#ff6655", width=1)
                d.text((r["bbox"][0], max(0, r["bbox"][1] - 10)), str(k), fill="yellow")
            panel.paste(pic, (j * 512, 32))
            s = row[name]
            draw.text(
                (j * 512 + 4, 4),
                f'{name} row {i}: {s["matched"]}/{s["targets"]}, extra {s["extra"]}',
                fill="white",
            )
        panels.append(panel)
        rows.append(row)
    summary = {}
    for source in ("all", "historical_reviewed_train", "confirmed_project_train"):
        group = [r for r in rows if source == "all" or r["source"] == source]
        summary[source] = dict(
            frames=len(group),
            **{
                name: {
                    k: sum(r[name][k] for r in group)
                    for k in ("targets", "matched", "missed", "extra")
                }
                for name in ("v1", "v2")
            },
        )
    db.write(
        out / "comparison.json",
        dict(summary=summary, rows=rows, independent_acceptance=False, iou=0.5),
    )
    for page in range((len(panels) + 3) // 4):
        current = panels[page * 4 : page * 4 + 4]
        sheet = Image.new("RGB", (1024, 320 * len(current)))
        for j, panel in enumerate(current):
            sheet.paste(panel, (0, j * 320))
        sheet.save(out / f"page_{page+1}.png")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
