import time
import numpy as np
from ordered_inference import OrderedInference

INPUT_TRANSFORM = "native_full_roi_fpn_integer_crop_v1"
INPUT_WH = (793, 317)
OUTPUT_HW = (80, 199)


def validate_model_config(config):
    if config.get("input_transform") == "native_tiles_v1":
        from model_input_adapter import validate_model_config as validate_tiles

        return validate_tiles(config)
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
    if model is not None and model.get("input_transform") == "native_tiles_v1":
        from model_input_adapter import capture_rectangle as capture_tiles

        return capture_tiles(settings, model)
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
        transport_received_monotonic=frame.capture_received_monotonic,
        transport_dequeued_monotonic=getattr(
            frame, "transport_dequeued_monotonic", 0.0
        ),
        python_copy_returned_monotonic=getattr(
            frame, "python_copy_returned_monotonic", 0.0
        ),
        acquired_monotonic=job.get("acquired_monotonic"),
        scheduler_admitted_monotonic=job.get("scheduler_admitted_monotonic"),
        npu_worker_started_monotonic=job.get("npu_worker_started_monotonic"),
        npu_started_monotonic=start,
        npu_finished_monotonic=finished,
        decode_finished_monotonic=done,
        native_ring_wait_ms=max(
            0.0,
            getattr(
                frame, "transport_dequeued_monotonic", frame.capture_received_monotonic
            )
            - frame.capture_received_monotonic,
        )
        * 1000,
        admitted_to_worker_ms=max(
            0.0,
            job.get("npu_worker_started_monotonic", prepare)
            - job.get("scheduler_admitted_monotonic", prepare),
        )
        * 1000,
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
    with self.sample_lock:
        self.core_samples[core].append(sample)
        self.core_calls[core] += 1
    return objects, sample


def infer(self, core, job):
    if self.config.get("input_transform") == "native_tiles_v1":
        from model_input_adapter import infer as infer_tiles

        return infer_tiles(self, core, job)
    return infer_tile(self, core, job)


from model_input_adapter import OrderedWholeFrames
