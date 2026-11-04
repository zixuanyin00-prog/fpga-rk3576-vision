import copy
import hashlib
import json
import math
import time
from itertools import combinations
from pathlib import Path
import cv2
import numpy as np
import torch
from torch.nn import functional as F
from .data import tensor, targets
from .decoder import decode, iou
from .model import ResistorNet
from .store import write
from .batch15 import beijing_now
from .robustness import (
    POLICY,
    curriculum,
    augmented_target,
    consistency_loss,
    assert_split,
)
from .gpu import require_cuda, record_device


def prepare_native_scene_job(job):

    job = copy.deepcopy(job)
    frames = job["train"] + job["validation"]
    groups = {}
    for f in frames:
        group = (
            f.get("source_group")
            or f.get("capture_session")
            or f.get("original_id", f["id"])
        )
        groups.setdefault(group, []).append(f)
    if len(groups) < 2:
        job["manifest"][
            "development_split_policy"
        ] = "single capture group; existing original-frame split, correlated"
        return job
    labels = len(job["schema"]["labels"])
    counts = lambda rows: np.bincount(
        [o["label"] for f in rows for o in f["objects"] if o["label"] >= 0],
        minlength=labels,
    )
    total = counts(frames)
    names = sorted(groups)
    best = None

    for n in range(1, min(2, len(names) - 1) + 1):
        for held in combinations(names, n):
            val = [f for g in held for f in groups[g]]
            vc = counts(val)
            if np.any((total > 0) & ((vc == 0) | (total - vc == 0))):
                continue
            key = (
                int(np.any((total >= 4) & (vc < 2))),
                abs(len(val) / len(frames) - 0.2),
                float(np.abs(vc / np.maximum(total, 1) - 0.2).mean()),
                held,
            )
            if best is None or key < best[0]:
                best = (key, set(held))
    if best is None:
        job["manifest"][
            "development_split_policy"
        ] = "no whole-group holdout covers all classes; existing original-frame split, correlated"
        return job
    held = best[1]
    job["train"] = []
    job["validation"] = []
    for group, rows in groups.items():
        role = "validation" if group in held else "train"
        for f in rows:
            f["role"] = role
            job[role].append(f)
    job["manifest"]["split"] = {
        f.get("original_id", f["id"]): role
        for role in ("train", "validation")
        for f in job[role]
    }
    job["manifest"][
        "development_split_policy"
    ] = "whole capture groups; approximately 20%; annotation coverage only"
    job["manifest"]["development_capture_groups"] = sorted(held)
    return job


def scene_augment(image, objects, rng, translation_x=96, native=False):

    tx, ty = int(rng.integers(-translation_x, translation_x + 1)), int(
        rng.integers(-3, 4)
    )
    if native:

        im = image[
            np.clip(np.arange(288) - ty, 0, 287)[:, None],
            np.clip(np.arange(512) - tx, 0, 511)[None, :],
        ].copy()
    else:
        matrix = np.float32([[1, 0, tx], [0, 1, ty]])
        im = cv2.warpAffine(image, matrix, (512, 288), borderMode=cv2.BORDER_REPLICATE)
    obs = []
    for obj in objects:
        box = np.array(obj["bbox"]) + [tx, ty, tx, ty]
        clipped = np.clip(box, [0, 0, 0, 0], [512, 288, 512, 288])
        if np.any(clipped[2:] <= clipped[:2]):
            continue

        label = obj["label"] if np.allclose(box, clipped) else -1
        obs.append(dict(bbox=clipped.tolist(), label=label))
    if not native:
        im = np.clip(
            im.astype(np.float32) * rng.uniform(0.96, 1.04) + rng.uniform(-3, 3), 0, 255
        ).astype(np.uint8)
    return im, targets(obs)


def detection_loss(z, truth, class_weight=None, state_ohem=False):
    heat = torch.from_numpy(np.stack([t[0] for t in truth])).to(z.device)
    logit = z[:, 0]
    p = logit.sigmoid()
    pos = heat == 1

    positive = -(1 - p).square() * F.logsigmoid(logit) * pos
    negative = (
        -p.square()
        * F.logsigmoid(-logit)
        * (1 - heat.clamp_min(0)).pow(4)
        * ((heat >= 0) & (heat < 1))
    )
    obj = (positive.sum() + negative.sum()) / pos.sum().clamp_min(1)
    bp = []
    bt = []
    cls = []
    ct = []
    focused = []
    for bi, (_, rows) in enumerate(truth):
        t = torch.as_tensor(rows, device=z.device)
        if not len(t):
            continue
        bp.append(z[bi, 1:5, t[:, 0].long(), t[:, 1].long()].T)
        bt.append(t[:, 2:6])
        for row in t:
            y, x = int(row[0]), int(row[1])
            v = z[
                bi, 5:, max(0, y - 2) : min(72, y + 3), max(0, x - 2) : min(128, x + 3)
            ]
            logits = v.permute(1, 2, 0).reshape(-1, z.shape[1] - 5)
            label = torch.full(
                (len(logits),), int(row[6]), dtype=torch.long, device=z.device
            )
            if state_ohem:
                losses = F.cross_entropy(
                    logits, label, weight=class_weight, reduction="none"
                )
                center = F.cross_entropy(
                    z[bi, 5:, y, x][None], label[:1], weight=class_weight
                )
                focused.append(
                    0.5 * losses.mean()
                    + 0.25 * center
                    + 0.25 * losses.topk(max(1, len(losses) // 4)).values.mean()
                )
            else:
                cls.append(logits)
                ct.append(label)
    size = offset = state = z.sum() * 0
    if bp:
        q = torch.cat(bp).sigmoid()
        t = torch.cat(bt)
        size = F.smooth_l1_loss(
            (q[:, :2].clamp_min(0.001) / t[:, :2]).log(),
            torch.zeros_like(t[:, :2]),
            beta=0.1,
        )
        offset = F.l1_loss(q[:, 2:], t[:, 2:])
    if cls:
        state = F.cross_entropy(torch.cat(cls), torch.cat(ct), weight=class_weight)
    if focused:
        state = torch.stack(focused).mean()
    total = obj + 6 * size + 1.5 * offset + state
    return total, dict(
        object=float(obj.detach()),
        size=float(size.detach()),
        offset=float(offset.detach()),
        state=float(state.detach()),
        positive_cells=int(pos.sum()),
    )


def score_frames(frames, outputs, config, complete=True, overlap=0.5, decoded=False):
    rows = []
    extras = []
    ignored_predictions = []
    per_class = {
        s: dict(
            total=0,
            detected=0,
            correct=0,
            missed=0,
            rejected=0,
            wrong=0,
            unscorable=0,
            predicted=0,
        )
        for s in config["labels"]
    }
    detected = correct = rejected = wrong = duplicates = 0
    overlaps = []
    for frame, values in zip(frames, outputs):
        found = (
            values
            if decoded
            else decode(
                values,
                object_threshold=config["object_threshold"],
                state_threshold=config["state_threshold"],
                labels=config["labels"],
            )
        )
        margin = config.get("complete_body_margin", 8)
        if complete:
            found = [
                d
                for d in found
                if margin <= d["bbox"][0] < d["bbox"][2] <= 512 - margin
                and margin <= d["bbox"][1] < d["bbox"][3] <= 288 - margin
            ]
        truth = [g for g in frame["objects"] if g["label"] >= 0]
        pairs = sorted(
            [
                (iou(g["bbox"], d["bbox"]), i, j)
                for i, g in enumerate(truth)
                for j, d in enumerate(found)
            ],
            reverse=True,
        )
        matches = {}
        used = set()
        for ov, i, j in pairs:
            if ov >= overlap and i not in matches and j not in used:
                matches[i] = (j, ov)
                used.add(j)
        for i, g in enumerate(truth):
            expected = config["labels"][g["label"]]
            pc = per_class[expected]
            pc["total"] += 1
            d = found[matches[i][0]] if i in matches else None
            success = (
                d is not None and d["label"] == expected and expected != "unreadable"
            )
            status = (
                "unscorable"
                if expected == "unreadable"
                else (
                    "missed"
                    if d is None
                    else (
                        "correct"
                        if success
                        else (
                            "rejected"
                            if d["label"] in ("uncertain", "unreadable")
                            else "wrong"
                        )
                    )
                )
            )
            pc[status] += 1
            pc["detected"] += d is not None
            detected += d is not None
            correct += success
            if status == "unscorable" and d is None:
                pc["missed"] += 1
            if d and d["label"] in per_class:
                per_class[d["label"]]["predicted"] += 1
            rejected += status == "rejected"
            wrong += status == "wrong"
            if d:
                overlaps.append(matches[i][1])
            rows.append(
                dict(
                    frame=frame["id"],
                    truth=expected,
                    predicted=d["label"] if d else "missed",
                    correct=success,
                    status=status,
                    bbox=g["bbox"],
                    prediction=d,
                    iou=matches[i][1] if d else 0,
                )
            )
        for k, d in enumerate(found):
            if k in used:
                continue

            if any(
                iou(d["bbox"], g["bbox"]) >= overlap
                for g in frame["objects"]
                if g["label"] == -1
            ):
                ignored_predictions.append(dict(frame=frame["id"], **d))
                continue
            duplicate = any(iou(d["bbox"], g["bbox"]) >= overlap for g in truth)
            duplicates += duplicate
            if d["label"] in per_class:
                per_class[d["label"]]["predicted"] += 1
            extras.append(dict(frame=frame["id"], duplicate=duplicate, **d))
    total = len(rows)
    n_pred = detected + len(extras)
    for v in per_class.values():
        v["recall"] = v["detected"] / v["total"] if v["total"] else None
        v["end_to_end_accuracy"] = v["correct"] / v["total"] if v["total"] else None
        v["classification_precision"] = (
            v["correct"] / v["predicted"] if v["predicted"] else None
        )
    confusion = {s: {} for s in config["labels"]}
    for r in rows:
        confusion[r["truth"]][r["predicted"]] = (
            confusion[r["truth"]].get(r["predicted"], 0) + 1
        )
    return dict(
        total=total,
        detected=detected,
        correct=correct,
        missed=total - detected,
        rejected=rejected,
        wrong=wrong,
        unscorable=sum(r["truth"] == "unreadable" for r in rows),
        extra=len(extras),
        false_positives=len(extras),
        duplicates=duplicates,
        recall=detected / total if total else None,
        precision=detected / n_pred if n_pred else None,
        accuracy=correct / total if total else None,
        end_to_end_accuracy=correct / total if total else None,
        mean_matched_iou=float(np.mean(overlaps)) if overlaps else 0,
        per_class=per_class,
        confusion=confusion,
        rows=rows,
        unmatched_predictions=extras,
        ignored_predictions=ignored_predictions,
        iou_threshold=overlap,
        complete_body_filter=complete,
        scope="Correlated labeled frame observations; not independent physical passages",
        accepted=False,
        board_tested=False,
    )


def infer_all(net, images):
    was_training = net.training
    net.eval()
    out = []
    with torch.no_grad():
        for im in images:
            out.append(
                [
                    v.cpu().numpy()
                    for v in net(tensor(im)[None].to(next(net.parameters()).device))
                ]
            )
    net.train(was_training)
    return out


def train_scene(job, root, out, config):
    assert_split(job)
    settings = dict(
        method="scene_v1",
        mode="train",
        backbone_lr=0.001,
        head_lr=0.002,
        weight_decay=0.0001,
        batch_size=4,
        warmup=30,
        fit_steps=min(200, max(50, job["steps"] // 3)),
        augmentation=True,
        translation_x=96,
        eval_every=50,
        balanced_classes=True,
    )
    settings.update(job.get("training_options", {}))
    robust = settings["method"] == "scene_robust_v1"
    native = settings["method"] == "native_scene_v2"
    if native:
        settings["training_view_policy"] = dict(
            integer_translation_x=settings["translation_x"],
            integer_translation_y=3,
            interpolation=False,
            resize=False,
            rotation=False,
            flip=False,
            color_changes=False,
            uncovered_border="replicate",
            newly_clipped_objects="ignore",
            originals_per_batch="at least half",
        )
    if robust:
        settings["robustness_policy"] = POLICY
    device = require_cuda()
    execution = record_device(out, device)
    torch.manual_seed(job["seed"])
    rng = np.random.default_rng(job["seed"])
    net = ResistorNet(states=len(config["labels"])).to(device)

    torch.use_deterministic_algorithms(True)
    sets = {}
    images = {}
    for role in ("train", "validation"):
        sets[role] = job[role]
        images[role] = []
        for f in sets[role]:
            if f.get("role") == "test":
                raise ValueError(
                    "Final test frames cannot enter training or checkpoint selection"
                )
            raw = (root / f["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != f["sha256"]:
                raise ValueError("Source image hash mismatch")
            images[role].append(np.frombuffer(raw, np.uint8).reshape(288, 512, 3))
    if {f["sha256"] for f in sets["train"]} & {f["sha256"] for f in sets["validation"]}:
        raise ValueError("Train/development leakage")
    if {
        (f.get("boot_id"), f.get("source_frame_id"))
        for f in sets["train"]
        if f.get("boot_id")
    } & {
        (f.get("boot_id"), f.get("source_frame_id"))
        for f in sets["validation"]
        if f.get("boot_id")
    }:
        raise ValueError("Same source frame crosses train/development split")
    counts = np.bincount(
        [o["label"] for f in sets["train"] for o in f["objects"] if o["label"] >= 0],
        minlength=len(config["labels"]),
    )
    weights = np.where(counts > 0, 1 / np.sqrt(np.maximum(counts, 1)), 0).astype(
        np.float32
    )
    weights[weights > 0] /= weights[weights > 0].mean()
    class_weight = (
        torch.from_numpy(weights).to(device) if settings["balanced_classes"] else None
    )
    optimizer = torch.optim.AdamW(
        [
            dict(params=net.backbone.parameters(), lr=settings["backbone_lr"]),
            dict(params=net.head.parameters(), lr=settings["head_lr"]),
        ],
        weight_decay=settings["weight_decay"],
    )
    resolved = dict(
        settings,
        steps=job["steps"],
        seed=job["seed"],
        torch=torch.__version__,
        numpy=np.__version__,
        opencv=cv2.__version__,
        deterministic_algorithms=True,
        initialization="random",
        pretrained_weights=None,
        thresholds=config,
        selection=(
            "complete training fit first; then development end-to-end, wrong, extra, recall, IoU; never final test"
            if native
            else "development end-to-end, recall, precision, IoU; never final test"
        ),
        class_counts=counts.tolist(),
        class_weights=weights.tolist(),
        development_independent_groups=not bool(
            {f.get("source_group") for f in sets["train"]}
            & {f.get("source_group") for f in sets["validation"]}
        ),
        source_hashes={f["id"]: f["sha256"] for role in sets for f in sets[role]},
    )
    resolved["execution"] = execution
    resolved["code_hashes"] = {
        str(p.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in [
            Path(__file__),
            Path(__file__).with_name("model.py"),
            Path(__file__).with_name("data.py"),
            Path(__file__).with_name("decoder.py"),
            Path(__file__).with_name("robustness.py"),
            Path(__file__).with_name("gpu.py"),
        ]
    }
    resolved["annotations_sha256"] = hashlib.sha256(
        json.dumps(
            {
                role: [dict(id=f["id"], objects=f["objects"]) for f in sets[role]]
                for role in sets
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    source_dir = out / "training_source"
    source_dir.mkdir(exist_ok=True)
    for rel in resolved["code_hashes"]:
        src = Path(__file__).resolve().parents[1] / rel
        dst = source_dir / rel
        dst.parent.mkdir(exist_ok=True, parents=True)
        dst.write_bytes(src.read_bytes())
    write(out / "training_config.json", resolved)
    history = []
    best_key = None
    best_state = None
    best_step = 0
    fit_pass = False
    started = time.monotonic()
    order = []
    legacy = []
    if settings["method"] == "bn_only":
        from .data import augment

        for im, f in zip(images["train"], sets["train"]):
            for k in range(8):
                legacy.append(augment(im, f["objects"], rng, k, config["labels"]))
            legacy.append((im, targets(f["objects"])))
    for step in range(job["steps"]):
        net.train(settings["mode"] == "train")
        if settings["method"] == "bn_only":
            batch = [
                legacy[i]
                for i in rng.choice(len(legacy), settings["batch_size"], replace=True)
            ]
            factor = 1.0
        else:
            ids = []
            while len(ids) < settings["batch_size"]:
                if not order:
                    order = rng.permutation(len(sets["train"])).tolist()
                ids.append(order.pop())
            batch = []
            for bi, idx in enumerate(ids):
                im = images["train"][idx]
                obs = sets["train"][idx]["objects"]

                if (
                    (fit_pass or native)
                    and step >= settings["fit_steps"]
                    and settings["augmentation"]
                    and bi >= settings["batch_size"] // 2
                ):
                    batch.append(
                        scene_augment(
                            im, obs, rng, settings["translation_x"], native=native
                        )
                    )
                else:
                    batch.append((im, targets(obs)))
            factor = min(1.0, (step + 1) / settings["warmup"]) * (
                0.1
                + 0.9 * 0.5 * (1 + math.cos(math.pi * step / max(1, job["steps"] - 1)))
            )
        for group, base in zip(
            optimizer.param_groups, (settings["backbone_lr"], settings["head_lr"])
        ):
            group["lr"] = base * factor
        severity, consistency_weight = (
            curriculum(step, job["steps"], settings["fit_steps"], fit_pass)
            if robust
            else (0.0, 0.0)
        )

        paired = []
        pair_truth = []
        pair_meta = []
        if robust and severity > 0:
            for idx in ids[:2]:
                aug, obs, meta = augmented_target(
                    images["train"][idx],
                    sets["train"][idx]["objects"],
                    rng,
                    severity,
                    config["labels"],
                )
                paired.append(aug)
                pair_truth.append(targets(obs))
                pair_meta.append(meta)
        x = torch.stack(
            [tensor(im) for im, _ in batch] + [tensor(im) for im in paired]
        ).to(device)
        z = net.head(net.encode(x))
        loss, parts = detection_loss(
            z[: len(batch)],
            [t for _, t in batch],
            class_weight,
            settings.get("state_ohem", False),
        )
        if paired:
            aug_loss, aug_parts = detection_loss(
                z[len(batch) :], pair_truth, class_weight
            )
            cons, used = consistency_loss(
                z[:2], z[len(batch) :], pair_truth, config["labels"]
            )
            loss = loss + 0.5 * aug_loss + consistency_weight * cons
            parts.update(
                augmented=float(aug_loss.detach()),
                consistency=float(cons.detach()),
                consistency_objects=used,
                augmented_safe=sum(sum(m["safe"]) for m in pair_meta),
                augmented_ignored=sum(sum(not v for v in m["safe"]) for m in pair_meta),
            )
        parts.update(severity=severity, consistency_weight=consistency_weight)
        if not torch.isfinite(loss):
            raise RuntimeError("Nonfinite training loss")
        optimizer.zero_grad()
        loss.backward()
        grad = torch.nn.utils.clip_grad_norm_(
            net.parameters(), 5, error_if_nonfinite=True
        )
        optimizer.step()
        if (step + 1) % settings["eval_every"] == 0 or step == job["steps"] - 1:
            scores = {}
            for role in ("train", "validation"):
                outputs = infer_all(net, images[role])
                scores[role] = score_frames(sets[role], outputs, config, complete=False)
            tr = scores["train"]
            fit_pass = fit_pass or (
                tr["recall"] == 1
                and tr["end_to_end_accuracy"] == 1
                and tr["precision"] == 1
            )
            chosen = scores["validation"] if sets["validation"] else tr
            current_fit = (
                tr["recall"] == 1
                and tr["end_to_end_accuracy"] == 1
                and tr["precision"] == 1
            )
            metrics = (
                chosen["end_to_end_accuracy"] or 0,
                -chosen["wrong"],
                -chosen["extra"],
                chosen["recall"] or 0,
                chosen["mean_matched_iou"],
            )
            key = (
                (int(current_fit), *metrics)
                if native
                else (
                    int(current_fit),
                    chosen["end_to_end_accuracy"] or 0,
                    chosen["recall"] or 0,
                    chosen["precision"] or 0,
                    chosen["mean_matched_iou"],
                )
            )
            if best_key is None or key > best_key:
                best_key = key
                best_state = copy.deepcopy(net.state_dict())
                best_step = step + 1
                best_fit = current_fit
                write(out / "best_metrics.json", scores)
            compact = lambda s: {
                k: s[k]
                for k in (
                    "total",
                    "detected",
                    "correct",
                    "missed",
                    "rejected",
                    "wrong",
                    "extra",
                    "recall",
                    "precision",
                    "end_to_end_accuracy",
                    "mean_matched_iou",
                )
            }
            row = dict(
                step=step + 1,
                steps=job["steps"],
                loss=float(loss.detach()),
                loss_parts=parts,
                gradient_norm=float(grad),
                lr=[g["lr"] for g in optimizer.param_groups],
                seconds=time.monotonic() - started,
                fit_passed=fit_pass,
                train=compact(tr),
                validation=compact(scores["validation"]),
            )
            history.append(row)
            write(out / "history.json", history)
            write(out / "progress.json", dict(stage="training", **row))
            print(row, flush=True)
    torch.save(net.state_dict(), out / "last.pt")
    net.load_state_dict(best_state)
    net.eval()
    write(
        out / "selection.json",
        dict(
            best_step=best_step,
            fit_passed=fit_pass,
            selected_training_fit_passed=best_fit,
            key=best_key,
            test_used=False,
            independent_test_available=False,
        ),
    )
    return net, history, resolved
