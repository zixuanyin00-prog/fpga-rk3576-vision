import time
import numpy as np


def write_failures(self):
    from evidence_process_bridge import write_failures as process_writer

    return process_writer(self)


def plan(rect):
    from native_windows import plan as shared_plan

    return shared_plan(rect)


def capture_rectangle(settings, model):
    rect = settings["rectangle"]
    if model and model.get("input_transform") == "native_tiles_v1":
        plan(rect)
    return list(rect)


def merge(rows):
    from native_decode import merge as native_merge

    return native_merge(rows)


def infer_tile(self, core, job, tile):
    from native_decode import decode
    from native_windows import accepts_tile_box

    with self.lock:
        if not self._valid(job):
            self.stale_results += 1
            return None
    frame = job["frame"]
    config = job["config"]
    prepare = time.monotonic()
    x, y, w, h = tile
    a = x - frame.x
    b = y - frame.y
    inp = np.ascontiguousarray(frame.rgb[b : b + h, a : a + w])
    if inp.shape != (288, 512, 3):
        raise ValueError("Native capture does not cover model tile")
    start = time.monotonic()
    outputs = self.engines[core].inference(inputs=[inp[None]], data_format=["nhwc"])
    finished = time.monotonic()
    from native_outputs import unpack

    outputs = unpack(outputs, len(self.config["labels"]))
    found = decode(
        outputs,
        labels=self.config["labels"],
        object_threshold=self.config.get("object_threshold", 0.15),
        state_threshold=config["confidence_percent"] / 100,
    )
    objects = []
    for obj in found:
        if accepts_tile_box(obj["bbox"], tile, config["rectangle"]):
            l, t, r, bottom = obj["bbox"]
            objects.append(dict(obj, bbox=[x + l, y + t, x + r, y + bottom]))
    done = time.monotonic()
    sample = dict(
        time=done,
        started=prepare,
        core=core,
        source_frame_id=frame.source_frame_id,
        queue_wait_ms=(prepare - frame.capture_received_monotonic) * 1000,
        preprocessing_ms=(start - prepare + done - finished) * 1000,
        rknn_ms=(finished - start) * 1000,
        input_copy_ms=(start - prepare) * 1000,
        decode_ms=(done - finished) * 1000,
        received_to_result_ms=(done - frame.capture_received_monotonic) * 1000,
    )
    if hasattr(self.engines[core], "last_timing"):
        sample["engine_timing"] = dict(self.engines[core].last_timing)
    with self.lock:
        self.core_samples[core].append(sample)
        self.core_calls[core] += 1
    return objects, sample


def infer(self, core, job):

    results = [
        infer_tile(self, core, job, tile) for tile in plan(job["config"]["rectangle"])
    ]
    if any(r is None for r in results):
        return None
    return merge([o for rows, _ in results for o in rows]), results[-1][1]


def observe_tracks(self, objects, source, now, hits, threshold):

    from native_decode import iou

    if (
        self.last_source is not None
        and not 0 < ((source - self.last_source) & 0xFFFFFFFF) < 0x80000000
    ):
        return [], []
    self.last_source = source
    rows = []
    failures = []
    used = set()
    self.tracks = {k: t for k, t in self.tracks.items() if now - t["seen"] < 0.6}
    import os

    keys = list(self.tracks)
    overlaps = None
    if os.environ.get("VISION_RKNN_C_API") == "1" and keys and objects:
        from native_postprocess import iou_matrix

        overlaps = iou_matrix(
            [o["bbox"] for o in objects], [self.tracks[k]["box"] for k in keys]
        )
    for index, obj in enumerate(objects):
        choices = (
            sorted(
                [
                    (float(overlaps[index, j]), k)
                    for j, k in enumerate(keys)
                    if k not in used
                ],
                reverse=True,
            )
            if overlaps is not None
            else sorted(
                [
                    (iou(obj["bbox"], t["box"]), k)
                    for k, t in self.tracks.items()
                    if k not in used
                ],
                reverse=True,
            )
        )
        ambiguous = (
            len(choices) > 1
            and choices[0][0] > 0.2
            and choices[0][0] - choices[1][0] < 0.1
        )
        if choices and choices[0][0] > 0.2 and not ambiguous:
            key = choices[0][1]
        else:
            self.next_id += 1
            key = self.next_id
            self.tracks[key] = dict(hits=0, box=obj["bbox"], counted=False)
        used.add(key)
        t = self.tracks[key]
        t.update(hits=t["hits"] + 1, box=list(obj["bbox"]), seen=now, source=source)
        classified = obj["confidence"] >= threshold and obj["label"] not in (
            "uncertain",
            "unreadable",
        )
        eligible = classified and not ambiguous
        if t["hits"] >= hits and eligible and not t["counted"]:
            self.counts[obj["label"]] = self.counts.get(obj["label"], 0) + 1
            t["counted"] = True
        tracking = (
            "association_uncertain"
            if ambiguous
            else "confirmed" if t["hits"] >= hits else "confirming"
        )
        row = dict(
            track_id=key,
            box=t["box"],
            raw_box=t["box"],
            roi_frame_id=source,
            tracking_hits=t["hits"],
            confirmed=t["hits"] >= hits,
            association_uncertain=ambiguous,
            classification=dict(
                label=obj["label"],
                raw_label=obj.get("raw_label", obj["label"]),
                confidence=obj["confidence"],
                accepted=classified,
            ),
            tracking=dict(
                status=tracking,
                association_uncertain=ambiguous,
                counting_eligible=eligible and t["hits"] >= hits,
            ),
            cached_fine=dict(label=obj["label"], score=obj["confidence"], age_ms=0),
        )
        rows.append(row)
        if not eligible:
            failures.append(
                dict(
                    row,
                    reason=(
                        "association_uncertain"
                        if ambiguous
                        else "low_confidence_or_unreadable"
                    ),
                )
            )
    return rows, failures


def install_display_titles(root):

    import json
    from pathlib import Path
    import native_view

    original_build = native_view.ScreenView.build
    original_show = native_view.ScreenView.show
    config = json.loads((Path(root) / "model/config.json").read_text())
    titles = dict(zip(config["labels"], config.get("titles", config["labels"])))

    def show(self, control, row):

        count = dict(row.get("counting", {}))
        selected_titles = dict(titles)
        selected_titles.update(
            {
                c["label"]: c.get("title", c["label"])
                for c in count.get("class_counts", [])
            }
        )

        objects = []
        for r in row.get("classification_observations", row.get("results", []))[:12]:
            classification = r.get("classification", {})
            cached = r.get("cached_fine", {})
            raw = classification.get("raw_label", cached.get("label"))
            accepted = classification.get(
                "accepted", cached.get("label") not in ("uncertain", "unreadable")
            )
            objects.append(
                dict(
                    id=r.get("track_id"),
                    pose=selected_titles.get(raw, "未知"),
                    accepted=accepted,
                    confidence=round(
                        classification.get("confidence", cached.get("score", 0)) * 100,
                        1,
                    ),
                    tracking={
                        "association_uncertain": "关联不确定",
                        "confirming": "确认中",
                        "classification_pending": "姿态待确认",
                        "crowded_pending": "重叠待确认",
                        "confirmed": "已确认",
                    }.get(r.get("tracking", {}).get("status"), "确认中"),
                    temporal=r.get("classification_temporal", {}),
                    source_frame_id=r.get("roi_frame_id"),
                )
            )
            temporal = objects[-1]["temporal"]
            objects[-1]["temporal_title"] = selected_titles.get(
                temporal.get("label"), "未知"
            )
        count["console_objects"] = objects
        from console_display import DisplaySummary

        if not hasattr(self, "console_display_summary"):
            self.console_display_summary = DisplaySummary()
        count["console_display_state"] = self.console_display_summary.add(
            row.get("display_alignment_state", {}), time.monotonic()
        )
        return original_show(self, control, dict(row, counting=count))

    def build(self, key, state, metrics, w, h, full):
        result = original_build(self, key, state, metrics, w, h, full)

        rows = metrics.get("counting", {}).get("console_objects", [])
        if w >= 1100 and h >= 1000 and rows:
            from PIL import ImageDraw

            x = w - 436
            y = max(250, h - 240)
            self.fill(None, (x, y, 416, h - 20 - y), (18, 29, 43))
            self.text(
                None,
                "分类 / 置信度 / 跟踪（异步结果）",
                x + 12,
                y + 10,
                392,
                size=18,
                color=(220, 235, 245, 255),
            )
            display = metrics.get("counting", {}).get("console_display_state", {})
            status = (
                f"近{display['seconds']:.1f}s 框{display['visible_percent']:.0f}% "
                f"图像跟踪≤{display.get('pixel_boxes',0)} "
                f"{display['max_source_gap']}帧/{display['max_age_ms']:.0f}ms"
                if display.get("available")
                else "画框区间统计：采样中"
            )
            self.text(
                None, status, x + 12, y + 36, 392, size=18, color=(255, 209, 62, 255)
            )
            for i, r in enumerate(rows[:6]):
                label = r["pose"] if r["accepted"] else r["pose"] + "(低置信度)"
                temporal = r.get("temporal", {})
                consensus = (
                    f" · 多帧{r['temporal_title']} {temporal.get('support',0)}/{temporal.get('reliable_observations',0)}"
                    if temporal.get("accepted")
                    else ""
                )
                self.text(
                    None,
                    f"{label} {r['confidence']:.1f}%{consensus} · {r['tracking']}",
                    x + 12,
                    y + 64 + i * 24,
                    392,
                    size=18,
                    color=(80, 224, 180, 255),
                )
            return result[0], self.canvas.tobytes("raw", "BGRA"), result[2], result[3]
        return result

    native_view.ScreenView.show = show
    native_view.ScreenView.build = build
