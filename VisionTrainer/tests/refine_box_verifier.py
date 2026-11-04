from pathlib import Path
import argparse
import sys
import hashlib
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import torch
from core import box_v2, store as db
from core.box_assistant import development_indices
from compare_box_models import score


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source_run")
    p.add_argument("scene_job")
    p.add_argument("output")
    a = p.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(357630)
    rng = np.random.default_rng(357630)
    source = Path(a.source_run)
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    job = db.read(a.scene_job)
    held = development_indices(job["frames"])
    data = []
    for f in job["frames"]:
        raw = Path(f["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == f["sha256"]
        data.append((np.frombuffer(raw, np.uint8).reshape(288, 512, 3), f["objects"]))
    historical = [
        i
        for i, f in enumerate(job["frames"])
        if i not in held and f["source"] == "historical_reviewed_train"
    ]
    current = [
        i
        for i, f in enumerate(job["frames"])
        if i not in held and f["source"] == "confirmed_project_train"
    ]
    examples = box_v2.verifier_examples(
        [data[i] for i in historical], rng, count=120
    ) + box_v2.verifier_examples(
        [data[i] for i in current], rng, count=60, positive_only=True
    )
    patches = torch.stack([x for x, y in examples])
    labels = torch.tensor([y for x, y in examples])
    pos = (labels == 1).nonzero().flatten().numpy()
    neg = (labels == 0).nonzero().flatten().numpy()
    verifier = box_v2.SpatialVerifier()
    opt = torch.optim.AdamW(verifier.parameters(), lr=0.0007, weight_decay=0.0003)
    start = time.monotonic()
    for step in range(1200):
        ids = np.r_[rng.choice(pos, 24), rng.choice(neg, 24)]
        loss = torch.nn.functional.binary_cross_entropy_with_logits(
            verifier(patches[ids]), labels[ids]
        )
        opt.zero_grad()
        loss.backward()
        opt.step()
        if step == 900:
            for g in opt.param_groups:
                g["lr"] = 0.00015
        if step % 100 == 0 or step == 1199:
            db.write(
                out / "progress.json",
                dict(
                    step=step + 1,
                    steps=1200,
                    loss=float(loss.detach()),
                    seconds=time.monotonic() - start,
                ),
            )
    weights = torch.load(source / "boxes.pt", map_location="cpu", weights_only=True)
    weights["verifier"] = verifier.eval().state_dict()
    torch.save(weights, out / "boxes.pt")
    net = box_v2.BoxNetV2().eval()
    net.load_state_dict(weights["detector"])
    metrics = []
    for i in held:
        metrics.append(
            dict(
                index=i,
                source=job["frames"][i]["source"],
                **score(data[i][1], box_v2.locate(net, verifier, data[i][0]))
            )
        )
    base = db.read(source / "result.json")
    base.pop("history", None)
    base.update(
        created=db.now(),
        sha256=hashlib.sha256((out / "boxes.pt").read_bytes()).hexdigest(),
        verifier_layout="spatial4x6",
        verifier_steps=1200,
        verifier_seed=357630,
        detector_source_run=source.name,
        verifier_train_frames=len(historical) + len(current),
        new_scene_positive_only=True,
        new_scene_unmarked_regions_not_negative=True,
        verifier_positive_examples=len(pos),
        verifier_negative_examples=len(neg),
        verifier_refinement_seconds=time.monotonic() - start,
        parameters=sum(p.numel() for p in net.parameters())
        + sum(p.numel() for p in verifier.parameters()),
        development_rows=metrics,
        development=dict(
            matched=sum(
                r["matched"]
                for r in metrics
                if r["source"] == "historical_reviewed_train"
            ),
            targets=sum(
                r["targets"]
                for r in metrics
                if r["source"] == "historical_reviewed_train"
            ),
            extra=sum(
                r["extra"]
                for r in metrics
                if r["source"] == "historical_reviewed_train"
            ),
            iou=0.5,
            independent_scene_acceptance=False,
        ),
        inference=dict(
            detector_threshold=0.30, verifier_threshold=0.35, joint_threshold=0.35
        ),
        new_scene_labels_incomplete=True,
    )
    db.write(
        out / "job.json",
        dict(
            scene_job=job,
            source_run=str(source),
            held_indices=held,
            detector_train_indices=historical,
            verifier_positive_only_indices=current,
            seed=357630,
            steps=1200,
        ),
    )
    db.write(out / "result.json", base)
    print(
        {
            k: v
            for k, v in base.items()
            if k in ["sha256", "development", "verifier_refinement_seconds"]
        }
    )


if __name__ == "__main__":
    main()
