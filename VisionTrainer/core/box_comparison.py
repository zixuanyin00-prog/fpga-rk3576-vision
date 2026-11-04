from collections import Counter
from .decoder import iou
from . import feedback


def metrics(objects, predicted):
    truth = [o for o in objects if o["label"] >= 0]
    ignored = [o for o in objects if o["label"] == -1]
    edges = {
        i: sorted(
            [j for j, p in enumerate(predicted) if iou(g["bbox"], p["bbox"]) >= 0.5],
            key=lambda j: iou(g["bbox"], predicted[j]["bbox"]),
            reverse=True,
        )
        for i, g in enumerate(truth)
    }
    assigned = {}

    def match(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in assigned or match(assigned[j], seen):
                assigned[j] = i
                return True
        return False

    for i in edges:
        match(i, set())
    from .box_geometry import intersection, area

    resize = sum(
        intersection(truth[i]["bbox"], predicted[j]["bbox"])
        / max(1, area(truth[i]["bbox"]))
        < 0.9
        or intersection(truth[i]["bbox"], predicted[j]["bbox"])
        / max(1, area(predicted[j]["bbox"]))
        < 0.65
        for j, i in assigned.items()
    )
    extras = [
        p
        for j, p in enumerate(predicted)
        if j not in assigned
        and not any(iou(p["bbox"], g["bbox"]) >= 0.2 for g in ignored)
    ]
    missed = len(truth) - len(assigned)
    return dict(
        targets=len(truth),
        matched=len(assigned),
        missed=missed,
        false_boxes=len(extras),
        duplicate_boxes=sum(
            any(iou(p["bbox"], g["bbox"]) >= 0.5 for g in truth) for p in extras
        ),
        ignored_edge_regions=len(ignored),
        edge_regions_proposed=sum(
            any(iou(g["bbox"], p["bbox"]) >= 0.2 for p in predicted) for g in ignored
        ),
        estimated_add_delete_operations=missed + len(extras),
        resize_required=resize,
        estimated_geometry_operations=missed + len(extras) + resize,
    )


def report(frames, training, before, after, allow_train_holdout=False):
    used = set().union(*(feedback.family(f) for f in training)) if training else set()
    rows = []
    pending = []
    for f in frames:
        if feedback.family(f) & used:
            raise ValueError("对比图片与助手训练原图或派生图重叠")
        if not allow_train_holdout and (
            f["role"] == "train"
            or f.get("feedback_transferred_at")
            or f.get("independent_test_ineligible")
        ):
            raise ValueError("该图片不符合独立验证要求")
        if (
            f.get("excluded")
            or not f.get("reviewed")
            or not f.get("all_objects_reviewed")
            or any(
                o.get("proposed") or o.get("needs_review") or o["label"] == -2
                for o in f.get("objects", [])
            )
        ):
            pending.append(f["id"])
            continue
        rows.append(
            dict(
                frame=f["id"],
                before=metrics(f["objects"], before[f["id"]]),
                after=metrics(f["objects"], after[f["id"]]),
                recorded_actions=dict(
                    Counter(e["kind"] for e in f.get("annotation_events", []))
                ),
            )
        )
    totals = {
        side: dict(sum((Counter(r[side]) for r in rows), Counter()))
        for side in ("before", "after")
    }
    return dict(
        iou=0.5,
        rows=rows,
        totals=totals,
        pending_human_review=pending,
        scope=(
            "same_batch_held_out_images_not_scene_acceptance"
            if allow_train_holdout
            else "independent_capture_images"
        ),
        effort_note="加框、删框和调框为几何修正项估计，不是实测点击数或耗时；历史无操作日志不能虚构前后人工操作量。",
        no_regression=bool(rows)
        and not pending
        and totals["after"].get("missed", 0) <= totals["before"].get("missed", 0)
        and totals["after"].get("false_boxes", 0)
        <= totals["before"].get("false_boxes", 0),
        accepted=False,
        acceptance_note="仅为画框几何对比；不自动宣告识别、板端性能或整体验收",
    )
