import time
import numpy as np
from ordered_whole_frames import OrderedWholeFrames as _RuntimeOrderedWholeFrames

INPUT_TRANSFORM = "native_tiles_v1"
INPUT_WH = (512, 288)
OUTPUT_HW = (72, 128)


def validate_model_config(config):
    if config.get("input_transform") != INPUT_TRANSFORM or config.get(
        "input_size"
    ) != list(INPUT_WH):
        raise ValueError(
            "Tile adapter requires the selected native_tiles_v1 512x288 model"
        )
    if config.get("window_stride", [256, 144]) != [256, 144]:
        raise ValueError(
            "Tile adapter requires the recorded native window stride 256x144"
        )
    if (
        config.get("bbox_size_normalization", [512, 288]) != [512, 288]
        or config.get("center_stride", 4) != 4
    ):
        raise ValueError(
            "Tile decoder requires trained bbox constants 512x288 and stride4 centers"
        )
    labels = config.get("labels")
    if (
        not isinstance(labels, list)
        or not 1 <= len(labels) <= 256
        or any(not isinstance(label, str) or not label for label in labels)
        or len(set(labels)) != len(labels)
    ):
        raise ValueError(
            "Tile model requires 1 to 256 distinct configured class labels"
        )
    return config


def capture_rectangle(settings, model):
    from native_windows import plan

    rect = settings["rectangle"]
    if len(rect) != 4:
        raise ValueError("Native capture rectangle requires four integers")
    if tuple(rect[2:]) != tuple(model["input_size"]):
        width, height = model["input_size"]
        raise ValueError(
            f"ROI尺寸必须固定为当前模型输入{width}×{height}；仅允许调整位置，禁止缩放或补像素"
        )
    plan(rect)
    return list(rect)


def infer(self, core, job):
    from native_windows import plan, accepts_tile_box
    from native_outputs import unpack
    from native_decode import decode, merge

    frame = job["frame"]
    settings = job["config"]
    prepare = time.monotonic()
    rect = capture_rectangle(settings, self.config)
    x, y, width, height = rect
    if (
        frame.x != x
        or frame.y != y
        or frame.rgb.dtype != np.uint8
        or frame.rgb.shape != (height, width, 3)
    ):
        raise ValueError(
            "Model tiles require the unchanged complete native capture rectangle"
        )
    tiles = plan(rect)
    objects = []
    timings = []
    npu_ms = copy_ms = decode_ms = 0.0
    first_npu = last_npu = None
    for tile in tiles:
        if not self._valid(job):
            self.stale_results += 1
            return None
        tile_prepare = time.monotonic()
        tx, ty, tw, th = tile
        a, b = tx - x, ty - y
        inp = np.ascontiguousarray(frame.rgb[b : b + th, a : a + tw])

        start = time.monotonic()
        outputs = self.engines[core].inference(inputs=[inp[None]], data_format=["nhwc"])
        finished = time.monotonic()
        p, logits = unpack(outputs, len(self.config["labels"]))
        if p.shape[2:] != OUTPUT_HW:
            raise ValueError(
                "Selected tile model output must use its native 72x128 stride4 grid"
            )
        found = decode(
            (p, logits),
            labels=self.config["labels"],
            object_threshold=self.config.get("object_threshold", 0.15),
            state_threshold=settings["confidence_percent"] / 100,
        )
        for obj in found:
            if accepts_tile_box(obj["bbox"], tile, rect):
                left, top, right, bottom = obj["bbox"]
                objects.append(
                    dict(obj, bbox=[tx + left, ty + top, tx + right, ty + bottom])
                )
        done = time.monotonic()
        native_wait = (
            max(
                0.0,
                getattr(
                    frame,
                    "transport_dequeued_monotonic",
                    frame.capture_received_monotonic,
                )
                - frame.capture_received_monotonic,
            )
            * 1000
        )
        admitted_wait = (
            max(
                0.0,
                job.get("npu_worker_started_monotonic", prepare)
                - job.get("scheduler_admitted_monotonic", prepare),
            )
            * 1000
        )
        sample = dict(
            time=done,
            started=tile_prepare,
            core=core,
            source_frame_id=frame.source_frame_id,
            native_ring_wait_ms=native_wait,
            admitted_to_worker_ms=admitted_wait,
            queue_wait_ms=(tile_prepare - frame.capture_received_monotonic) * 1000,
            preprocessing_ms=(start - tile_prepare + done - finished) * 1000,
            input_copy_ms=(start - tile_prepare) * 1000,
            decode_ms=(done - finished) * 1000,
            rknn_ms=(finished - start) * 1000,
            received_to_result_ms=(done - frame.capture_received_monotonic) * 1000,
            model_calls=1,
            tile_calls=1,
            native_tile=list(tile),
            complete_native_roi=False,
            input_transform=INPUT_TRANSFORM,
            input_size=list(INPUT_WH),
        )
        if hasattr(self.engines[core], "last_timing"):
            sample["engine_timing"] = dict(self.engines[core].last_timing)
        with self.sample_lock:
            self.core_samples[core].append(sample)
            self.core_calls[core] += 1
        timings.append(sample)
        npu_ms += sample["rknn_ms"]
        copy_ms += sample["input_copy_ms"]
        decode_ms += sample["decode_ms"]
        if first_npu is None:
            first_npu = start
        last_npu = finished

    before_merge = time.monotonic()
    objects = merge(objects)
    done = time.monotonic()
    decode_ms += (done - before_merge) * 1000
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
        npu_started_monotonic=first_npu,
        npu_finished_monotonic=last_npu,
        decode_finished_monotonic=done,
        native_ring_wait_ms=timings[0]["native_ring_wait_ms"],
        admitted_to_worker_ms=timings[0]["admitted_to_worker_ms"],
        tile_calls=len(tiles),
        model_calls=len(tiles),
        complete_native_roi=True,
        input_transform=INPUT_TRANSFORM,
        input_size=list(INPUT_WH),
        native_capture_rectangle=rect,
        native_tiles=[list(tile) for tile in tiles],
        pixel_policy="native_crop_only_v1",
        queue_wait_ms=(prepare - frame.capture_received_monotonic) * 1000,
        preprocessing_ms=max(0.0, (done - prepare) * 1000 - npu_ms),
        input_copy_ms=copy_ms,
        decode_ms=decode_ms,
        rknn_ms=npu_ms,
        tile_wall_ms=(done - prepare) * 1000,
        received_to_result_ms=(done - frame.capture_received_monotonic) * 1000,
    )
    return objects, sample


class OrderedWholeFrames(_RuntimeOrderedWholeFrames):

    def snapshot(self):
        result = super().snapshot()
        pipeline = self.execute.__self__
        if (
            pipeline.config
            and pipeline.config.get("input_transform") == INPUT_TRANSFORM
        ):
            from native_windows import plan

            count = len(plan(pipeline.settings["rectangle"]))
            result.update(
                input_transform=INPUT_TRANSFORM,
                model_input_size=list(INPUT_WH),
                model_calls_per_completed_source=count,
                tiles_per_complete_source=count,
                partial_tile_results=False,
                complete_source_publication=True,
            )
        return result
