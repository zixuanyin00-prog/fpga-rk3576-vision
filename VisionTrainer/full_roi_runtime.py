import time
import numpy as np
from ordered_inference import OrderedInference

INPUT_TRANSFORM = "native_full_roi_fpn_integer_crop_v1"
INPUT_WH = (793, 317)
OUTPUT_HW = (80, 199)


def validate_model_config(config):
    if (
        config.get("input_transform") != INPUT_TRANSFORM
        or tuple(config.get("input_size", ())) != INPUT_WH
    ):
        raise ValueError(
            "Selected model is not the explicit 793x317 whole-native-ROI candidate"
        )
    if (
        config.get("bbox_size_normalization") != [512, 288]
        or config.get("center_stride") != 4
    ):
        raise ValueError(
            "Whole ROI must retain trained bbox size constants 512/288 and stride4 centers"
        )
    labels = config.get("labels", [])
    if (
        not isinstance(labels, list)
        or not 1 <= len(labels) <= 256
        or any(not isinstance(k, str) or not k for k in labels)
        or len(set(labels)) != len(labels)
    ):
        raise ValueError(
            "Whole-ROI model requires 1 to 256 distinct configured class labels"
        )
    return config


def capture_rectangle(settings, model):
    if model is None:
        return list(settings["rectangle"])
    validate_model_config(model)
    rect = settings["rectangle"]
    if len(rect) != 4 or any(type(v) is not int for v in rect):
        raise ValueError("Native ROI geometry must contain four integers")
    x, y, w, h = rect
    if (w, h) != INPUT_WH or not (0 <= x <= 1920 - w and 0 <= y <= 1080 - h):
        raise ValueError(
            "Whole-ROI candidate requires native793x317 inside1920x1080; resize/pad forbidden"
        )
    return list(rect)


def model_input(rgb, config):
    validate_model_config(config)
    if rgb.dtype != np.uint8 or rgb.shape != (317, 793, 3):
        raise ValueError("Whole-ROI pixels must be native uint8 RGB793x317")
    return np.ascontiguousarray(rgb)


def infer_tile(self, core, job, tile=None):

    from native_postprocess import decode_raw, last_c_ms
    from native_windows import accepts_tile_box

    with self.lock:
        if not self._valid(job):
            self.stale_results += 1
            return None
    frame = job["frame"]
    settings = job["config"]
    prepare = time.monotonic()
    rect = capture_rectangle(settings, self.config)
    x, y, w, h = rect
    if tile is not None and list(tile) != rect:
        raise ValueError("Partial windows cannot produce whole-ROI performance results")
    if frame.x != x or frame.y != y:
        raise ValueError(
            "Whole-ROI capture origin does not match the admitted source frame"
        )
    inp = model_input(frame.rgb, self.config)
    start = time.monotonic()
    outputs = self.engines[core].inference(inputs=[inp[None]], data_format=["nhwc"])
    finished = time.monotonic()
    if len(outputs) != 1 or outputs[0].dtype != np.float32:
        raise ValueError(
            "INT8 runtime must return one dequantized float32 raw head (want_float=1)"
        )
    if outputs[0].shape != (1, 5 + len(self.config["labels"]), *OUTPUT_HW):
        raise ValueError("Whole-ROI output must use the native80x199 stride4 grid")
    found = decode_raw(
        outputs[0],
        labels=self.config["labels"],
        object_threshold=self.config.get("object_threshold", 0.15),
        state_threshold=settings["confidence_percent"] / 100,
    )
    objects = []
    for obj in found:
        if accepts_tile_box(obj["bbox"], rect, rect):
            l, t, r, b = obj["bbox"]
            objects.append(dict(obj, bbox=[x + l, y + t, x + r, y + b]))
    done = time.monotonic()
    sample = dict(
        time=done,
        started=prepare,
        core=core,
        source_frame_id=frame.source_frame_id,
        tile_calls=1,
        model_calls=1,
        complete_native_roi=True,
        input_transform=INPUT_TRANSFORM,
        input_size=list(INPUT_WH),
        six_window_baseline=False,
        queue_wait_ms=(prepare - frame.capture_received_monotonic) * 1000,
        preprocessing_ms=(start - prepare + done - finished) * 1000,
        input_copy_ms=(start - prepare) * 1000,
        decode_ms=(done - finished) * 1000,
        rknn_ms=(finished - start) * 1000,
        postprocess_c_ms=last_c_ms(),
        tile_wall_ms=(done - prepare) * 1000,
        received_to_result_ms=(done - frame.capture_received_monotonic) * 1000,
    )
    if hasattr(self.engines[core], "last_timing"):
        sample["engine_timing"] = dict(self.engines[core].last_timing)
    with self.lock:
        self.core_samples[core].append(sample)
        self.core_calls[core] += 1
    return objects, sample


def infer(self, core, job):
    return infer_tile(self, core, job)


class OrderedWholeFrames(OrderedInference):

    def __init__(self, execute, publish, fail, capacity=6, setup=None):
        if type(capacity) is not int or not 3 <= capacity <= 8:
            raise ValueError(
                "Whole-ROI scheduler capacity must be an integer from3 through8"
            )
        super().__init__(execute, publish, fail, capacity=capacity, setup=setup)
        self.last_admitted_source = None
        self.duplicate_admissions = 0
        self.admission_attempts = 0
        self.admission_wait_count = 0
        self.admission_wait_ms_total = 0.0
        self.admission_wait_ms_max = 0.0
        self.admission_wait_ms_last = 0.0
        self.admission_cancelled_epoch = 0
        self.admission_rejected_closed = 0
        self.admission_waiting = {}

    def submit(self, job):
        source = int(job["frame"].source_frame_id)
        with self.cv:
            self.admission_attempts += 1
            if self.closed:
                self.admission_rejected_closed += 1
                return False
            if self.last_admitted_source is not None:
                delta = (source - self.last_admitted_source) & 0xFFFFFFFF
                if not 0 < delta < 0x80000000:
                    self.duplicate_admissions += 1
                    return False
            epoch = self.epoch
            wait_key = None
            began = None
            try:
                while (
                    not self.closed
                    and self.epoch == epoch
                    and len(set(self.order) | self.active) >= self.capacity
                ):
                    if began is None:
                        began = time.perf_counter()
                        wait_key = object()
                        self.admission_wait_count += 1
                        self.admission_waiting[wait_key] = dict(
                            source_frame_id=source, started=began
                        )
                    self.cv.wait()
            finally:
                if began is not None:
                    elapsed = (time.perf_counter() - began) * 1000
                    self.admission_wait_ms_total += elapsed
                    self.admission_wait_ms_max = max(
                        self.admission_wait_ms_max, elapsed
                    )
                    self.admission_wait_ms_last = elapsed
                    self.admission_waiting.pop(wait_key, None)
            if self.closed:
                self.admission_rejected_closed += 1
                return False
            if self.epoch != epoch:
                self.admission_cancelled_epoch += 1
                return False

            if self.last_admitted_source is not None:
                delta = (source - self.last_admitted_source) & 0xFFFFFFFF
                if not 0 < delta < 0x80000000:
                    self.duplicate_admissions += 1
                    return False

            accepted = super().submit(job)
            if accepted:
                self.last_admitted_source = source
            return accepted

    def _collect(self):
        try:
            while True:
                with self.cv:
                    self.cv.wait_for(
                        lambda: self.closed
                        or (self.order and self.order[0] in self.results)
                    )
                    if self.closed:
                        return
                    key = self.order[0]
                    job, result = self.results[key]

                if result is not None:
                    self.publish(job, result)
                with self.cv:
                    if key in self.order:
                        self.order.remove(key)
                    self.results.pop(key, None)
                    if result is not None:
                        self.published += 1

                    self.cv.notify_all()
        except BaseException as exc:
            self.fail(exc)
            self.stop()

    def invalidate(self):
        with self.cv:
            super().invalidate()
            self.last_admitted_source = None

    def snapshot(self):
        with self.cv:
            result = super().snapshot()
            now = time.perf_counter()
            return dict(
                result,
                mode="two_cores_distinct_complete_native_roi_frames",
                input_transform=INPUT_TRANSFORM,
                model_calls_per_completed_source=1,
                partial_tile_results=False,
                duplicate_admissions=self.duplicate_admissions,
                worker_capacity=2,
                pending_capacity_when_two_running=self.capacity - 2,
                pending_capacity_without_running_or_completed=self.capacity,
                capacity_scope="running_pending_and_completed_unpublished",
                pending_depth=len(self.pending),
                occupied_depth=len(set(self.order) | self.active),
                admission_policy="bounded_backpressure_no_pending_drop",
                admission_attempts=self.admission_attempts,
                admission_wait_count=self.admission_wait_count,
                admission_wait_ms_total=self.admission_wait_ms_total,
                admission_wait_ms_mean=self.admission_wait_ms_total
                / max(1, self.admission_wait_count),
                admission_wait_ms_max=self.admission_wait_ms_max,
                admission_wait_ms_last=self.admission_wait_ms_last,
                admission_waiters=len(self.admission_waiting),
                admission_wait_ms_current_max=max(
                    (
                        (now - v["started"]) * 1000
                        for v in self.admission_waiting.values()
                    ),
                    default=0.0,
                ),
                waiting_source_frame_ids=[
                    v["source_frame_id"] for v in self.admission_waiting.values()
                ],
                admission_cancelled_epoch=self.admission_cancelled_epoch,
                admission_rejected_closed=self.admission_rejected_closed,
                published_is_callback_count=True,
            )
