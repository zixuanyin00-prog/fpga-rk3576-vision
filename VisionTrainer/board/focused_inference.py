from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import hashlib
import json
import threading
import time
from rknnlite.api import RKNNLite
from decoder import decode, iou
from output_counter import OutputPassageCounter
from dynamic_audit import DynamicAudit
from training_capture_bridge import TrainingCaptureBridge
from rate_statistics import recent_rate
from focus_metadata import READABLE, validate_region, native_objects, fresh

ROOT = Path(__file__).resolve().parent


class FocusedInference:
    def __init__(self, camera, coarse=False, preview=None):

        self.camera = camera
        self.lock = threading.RLock()
        self.generation = 0
        self.stop = threading.Event()
        self.error = None
        self.started = time.monotonic()
        self.region = json.loads((ROOT / "focus_region.json").read_text())
        self.origin = validate_region(self.region)
        self.control = SimpleNamespace(
            scheduler=SimpleNamespace(tracks={}), collect=lambda wait=True: None
        )
        self.counter = OutputPassageCounter(ROOT / "passage_counts.json")
        self.counter_generation = None
        self.completed = deque(maxlen=300)
        self.timings = deque(maxlen=300)
        self.recognitions = deque(maxlen=1200)
        self.rows = []
        self.results_at = 0
        self.results_generation = -1
        self.captured = 0
        self.ignored = 0
        self.previous = None
        self.class_counts = {}
        self.model_config = json.loads((ROOT / "model/config.json").read_text())
        self.model_sha256 = hashlib.sha256(
            (ROOT / "model/detector_fp16.rknn").read_bytes()
        ).hexdigest()
        self.engine = RKNNLite(verbose=False)
        if self.engine.load_rknn(str(ROOT / "model/detector_fp16.rknn")) != 0:
            raise RuntimeError("Cannot load fine model")
        if self.engine.init_runtime(core_mask=RKNNLite.NPU_CORE_1) != 0:
            raise RuntimeError("Cannot initialize fine NPU")
        self.dynamic_audit = DynamicAudit(ROOT)
        self.training_bridge = TrainingCaptureBridge(ROOT)
        self.thread = threading.Thread(
            target=self._run, name="central-native-roi", daemon=True
        )
        self.thread.start()

    def _capture(self):
        with self.lock:
            generation = self.generation
        _, frame = self.camera.acquire(True, *self.origin)
        received = time.monotonic()
        if (
            not frame.roi
            or (frame.x, frame.y) != self.origin
            or frame.rgb.shape != (288, 512, 3)
        ):
            raise RuntimeError("Fixed native ROI response mismatch; inference refused")
        return frame, generation, received

    def _run(self):
        try:
            with ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="central-roi-acquire"
            ) as capture:
                pending = capture.submit(self._capture)
                while not self.stop.is_set():
                    frame, generation, received = pending.result()
                    self.captured += 1
                    changed = self.training_bridge.poll(
                        self.camera,
                        self._set_training_region,
                        busy=self.dynamic_audit.request is not None
                        or (ROOT / "dynamic_audit/request.json").exists(),
                    )
                    pending = capture.submit(self._capture)
                    if changed:
                        continue
                    if not fresh(frame.source_frame_id, self.previous):
                        self.ignored += 1
                        continue
                    self.previous = frame.source_frame_id
                    began = time.monotonic()
                    outputs = self.engine.inference(
                        inputs=[frame.rgb[None]], data_format=["nhwc"]
                    )
                    inferred = time.monotonic()
                    objects = decode(
                        outputs,
                        object_threshold=self.model_config.get(
                            "object_threshold", 0.15
                        ),
                        state_threshold=self.model_config.get("state_threshold", 0.85),
                        labels=self.model_config["labels"],
                    )
                    done = time.monotonic()
                    selected = native_objects(objects, self.region)
                    applied = []
                    with self.lock:
                        self.completed.append((done, frame.source_frame_id, None))
                        self.timings.append((inferred - began, done - inferred))
                        if generation != self.generation:
                            self.ignored += 1
                            self.rows = []
                            for t in self.counter.tracks.values():
                                t.pop("output_evidence", None)
                        else:
                            if self.counter_generation != generation:
                                for t in self.counter.tracks.values():
                                    t.pop("output_evidence", None)
                                self.counter_generation = generation

                            for t in self.counter.tracks.values():
                                t.update(label=None, label_time=-1.0)
                            self.counter.observe(selected, done, frame.source_frame_id)
                            used = set()
                            rows = []
                            for obj in selected:
                                available = [
                                    k
                                    for k, t in self.counter.tracks.items()
                                    if k not in used and t["seen"] == done
                                ]
                                key = max(
                                    available,
                                    key=lambda k: iou(
                                        obj["bbox"], self.counter.tracks[k]["box"]
                                    ),
                                    default=None,
                                )
                                if key is None:
                                    raise RuntimeError(
                                        "Same-frame body lost its geometric association"
                                    )
                                used.add(key)
                                label = obj["label"]
                                box = list(obj["bbox"])
                                rows.append(
                                    dict(
                                        track_id=key,
                                        box=box,
                                        raw_box=box,
                                        roi_frame_id=frame.source_frame_id,
                                        cached_fine=dict(
                                            label=label,
                                            score=obj["confidence"],
                                            age_ms=0,
                                        ),
                                    )
                                )
                                applied.append(
                                    dict(
                                        track_id=key,
                                        box=box,
                                        raw_box=box,
                                        label=label,
                                        score=obj["confidence"],
                                    )
                                )
                                self.recognitions.append(
                                    (done, frame.source_frame_id, key, label)
                                )
                                counts = self.class_counts.setdefault(str(key), {})
                                counts[label] = counts.get(label, 0) + 1
                            while len(self.class_counts) > 128:
                                self.class_counts.pop(next(iter(self.class_counts)))
                            self.rows = rows
                            self.results_at = done
                            self.results_generation = generation
                    self.dynamic_audit.offer(
                        frame, objects, None, applied, done, self.model_sha256
                    )
                pending.result()
        except BaseException as exc:
            self.error = exc

    def _set_training_region(self, spec):
        origin = validate_region(spec)
        with self.lock:
            self.region = spec
            self.origin = origin
            self.previous = None
            self.rows = []
            self.counter.tracks.clear()
            self.counter_generation = None

    def snapshot(self):
        if self.error:
            raise RuntimeError("Central ROI inference failed") from self.error
        now = time.monotonic()
        with self.lock:
            rows = (
                [
                    dict(
                        r,
                        cached_fine=dict(
                            r["cached_fine"], age_ms=(now - self.results_at) * 1000
                        ),
                    )
                    for r in self.rows
                ]
                if now - self.results_at < 0.15
                and self.results_generation == self.generation
                else []
            )
            completed = list(self.completed)
            timings = list(self.timings)
            recognitions = [r for r in self.recognitions if now - 2 < r[0] <= now]
            counting = self.counter.snapshot()
            counts = {k: dict(v) for k, v in self.class_counts.items()}
        _, elapsed, rate, distinct_rate = recent_rate(completed, now, self.started)
        span = min(2.0, max(1e-6, now - self.started))
        valid = [r for r in recognitions if r[3] in READABLE]
        return rows, dict(
            roi_calls_per_second=rate,
            distinct_source_fps=distinct_rate,
            fine_state="central_roi_only",
            active_targets=len(rows),
            captured=self.captured,
            ignored_old_generation=self.ignored,
            coarse_enabled=False,
            coarse_fps=0.0,
            coarse_source_coverage=0.0,
            full_preview_npu_inferences=0,
            npu_input="FPGA native fixed ROI",
            native_roi=self.region,
            source_frame_coverage=distinct_rate / 60.0,
            mean_npu_ms=sum(t[0] for t in timings) / max(1, len(timings)) * 1000,
            mean_decode_ms=sum(t[1] for t in timings) / max(1, len(timings)) * 1000,
            valid_fresh_recognitions_per_second=len(valid) / span,
            rejected_fresh_results_per_second=(len(recognitions) - len(valid)) / span,
            per_track_valid_fresh_fps={
                str(k): sum(r[2] == k for r in valid) / span
                for k in {r[2] for r in recognitions}
            },
            per_track_fresh_predictions=counts,
            counting=counting,
            task_region_excludes_raw_upper_track=True,
            model_id=self.model_config.get("window_model_id"),
            model_sha256=self.model_sha256,
            fine_object_threshold=self.model_config.get("object_threshold", 0.15),
            fine_state_threshold=self.model_config.get("state_threshold", 0.85),
            output_mode="connected_disabled_dry_run",
            crossing_time_basis="inference completion; no sensor timestamp",
        )

    def close(self):
        self.stop.set()
        with self.lock:
            self.counter.close()
        self.thread.join(8)
        if self.thread.is_alive():
            raise RuntimeError("Central inference did not stop")
        self.dynamic_audit.close()
        self.engine.release()
        if self.error:
            raise RuntimeError("Central inference failed") from self.error
