import hashlib
from pathlib import Path
from . import store as db
from .gpu import GPU_PYTHON

HOME = db.ROOT / "box_assistant"
FOUNDATION = db.ROOT / "box_foundation"


def development_indices(rows):
    held = []
    counts = {}
    for i, f in enumerate(rows):
        group = f.get("project", "historical")
        n = counts.get(group, 0)
        counts[group] = n + 1
        if (
            f["source"] == "historical_reviewed_train"
            and n % 6 == 5
            or f["source"] == "confirmed_project_train"
            and n % 5 == 4
        ):
            held.append(i)
    return held


def status():
    provenance = FOUNDATION / "models" / "grounding-dino-base" / "provenance.json"
    if provenance.exists():
        meta = db.read(provenance)
        return dict(
            ready=(provenance.parent / "model.safetensors").exists()
            and GPU_PYTHON.exists(),
            run="grounding_dino_base",
            provider="huggingface",
            model_id=meta["model_id"],
            revision=meta["revision"],
            sha256=meta["files"]["model.safetensors"],
            created=None,
            training_time_note=meta["training_time_note"],
            architecture="grounding-dino-base",
            required_device="cuda:0",
        )
    path = HOME / "active.json"
    if not path.exists():
        return dict(ready=False, message="画框助手尚未训练")
    info = db.read(path)
    model = HOME / "runs" / info["run"] / "boxes.pt"
    return dict(info, ready=model.exists())


def training_frames():
    seed = db.read(HOME / "seed" / "annotations.json")
    rows = []
    seen = set()
    for f in seed["frames"]:
        path = HOME / "seed" / f["file"]
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != f["sha256"]:
            raise ValueError("画框种子图完整性校验失败")
        rows.append(
            dict(
                f,
                path=str(path),
                source="historical_reviewed_train",
                annotation_source=seed["annotation_source"],
            )
        )
        seen.add(f["sha256"])

    for p in db.listing():
        for f in p["frames"]:
            if (
                f.get("excluded")
                or f["role"] != "train"
                or not f.get("reviewed")
                or f["sha256"] in seen
            ):
                continue
            if any(
                o.get("needs_review") or o.get("proposed") or o["label"] == -2
                for o in f["objects"]
            ):
                continue
            path = db.project_path(p["id"]) / f["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != f["sha256"]:
                raise ValueError("画框训练图完整性校验失败")
            rows.append(
                dict(
                    path=str(path),
                    sha256=f["sha256"],
                    source="confirmed_project_train",
                    project=p["id"],
                    frame=f["id"],
                    objects=[
                        dict(bbox=o["bbox"], ignore=o["label"] < 0)
                        for o in f["objects"]
                    ],
                )
            )
            seen.add(f["sha256"])
    return rows
