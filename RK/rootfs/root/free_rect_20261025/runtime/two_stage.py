from dataclasses import dataclass
import math
import time
import numpy as np


class StaleROIError(RuntimeError):
    pass


@dataclass(frozen=True)
class Command:
    request_id: int
    roi: bool = False
    x: int = 0
    y: int = 0
    parent_frame_id: int = 0


@dataclass(frozen=True)
class Frame:
    request_id: int
    source_frame_id: int
    roi: bool
    x: int
    y: int
    rgb: np.ndarray


class TwoStage:
    def __init__(
        self,
        transport,
        detector,
        refiner,
        decode,
        *,
        source=(1920, 1080),
        tensor=(512, 288),
        max_frame_gap=3,
        max_rois=4,
        scheduler=None,
        clock=time.monotonic,
        on_result=None
    ):
        self.transport, self.detector, self.refiner = transport, detector, refiner
        self.decode = decode
        self.scheduler, self.clock = scheduler, clock
        self.on_result = on_result
        self.sw, self.sh = source
        self.w, self.h = tensor
        if not 0 < self.w <= self.sw or not 0 < self.h <= self.sh:
            raise ValueError("Invalid geometry")
        if not 0 < max_frame_gap < 0x80000000:
            raise ValueError("Invalid maximum source-frame gap")
        self.max_frame_gap = max_frame_gap
        if not isinstance(max_rois, int) or max_rois < 1:
            raise ValueError("Invalid ROI budget")
        self.max_rois = max_rois
        self.sequence = 0

    def _capture(self, roi=False, x=0, y=0, parent=None):
        if hasattr(self.transport, "acquire"):
            command, frame = self.transport.acquire(roi, x, y, parent)
            self.sequence = command.request_id
        else:
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF or 1
            command = Command(self.sequence, roi, x, y, 0 if parent is None else parent)
            frame = self.transport.capture(command)
        if hasattr(self, "last_metrics"):
            self.last_metrics["rgb_payload_bytes"] += frame.rgb.nbytes
        if (frame.request_id, frame.roi, frame.x, frame.y) != (
            command.request_id,
            roi,
            x,
            y,
        ):
            raise RuntimeError("FPGA response does not match command")
        a = frame.rgb
        if (
            a.dtype != np.uint8
            or a.shape != (self.h, self.w, 3)
            or not a.flags.c_contiguous
        ):
            raise RuntimeError(
                "FPGA must supply model-ready contiguous RGB888; CPU resize forbidden"
            )
        if parent is not None:
            gap = (frame.source_frame_id - parent) & 0xFFFFFFFF
            if not 0 < gap <= self.max_frame_gap:
                raise StaleROIError(
                    "ROI source frame is stale or too far from localization"
                )
        return frame

    @staticmethod
    def _infer(engine, frame):

        out = engine.inference(inputs=[frame.rgb[None]], data_format=["nhwc"])
        if out is None or len(out) == 0 or not all(np.isfinite(x).all() for x in out):
            raise RuntimeError("Invalid NPU output")
        return out

    def run_once(self):
        if self.scheduler is not None:
            return self._run_selective()
        overview = self._capture()
        boxes = self.decode(self._infer(self.detector, overview))
        self.last_overview = overview
        self.last_boxes = boxes
        self.last_roi = None
        results = []
        requested = 0
        for box in boxes:
            if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
                raise ValueError("Invalid localization box")
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= self.w and 0 <= y1 < y2 <= self.h):
                raise ValueError("Localization box outside preview")

            native = (
                x1 * self.sw / self.w,
                y1 * self.sh / self.h,
                x2 * self.sw / self.w,
                y2 * self.sh / self.h,
            )
            if native[2] - native[0] > self.w or native[3] - native[1] > self.h:
                results.append(dict(box=native, status="object_exceeds_native_roi"))
                continue
            x = max(
                0,
                min(self.sw - self.w, math.floor((native[0] + native[2] - self.w) / 2)),
            )
            y = max(
                0,
                min(self.sh - self.h, math.floor((native[1] + native[3] - self.h) / 2)),
            )
            if requested >= self.max_rois:
                results.append(dict(box=native, status="deferred_roi_budget"))
                continue
            requested += 1
            try:
                crop = self._capture(True, x, y, overview.source_frame_id)
            except StaleROIError:
                results.append(dict(box=native, status="stale_roi_rejected"))
                break
            outputs = self._infer(self.refiner, crop)
            self.last_roi = crop
            results.append(
                dict(
                    box=native,
                    origin=(x, y),
                    request_id=crop.request_id,
                    overview_frame_id=overview.source_frame_id,
                    roi_frame_id=crop.source_frame_id,
                    outputs=outputs,
                    status="candidate",
                )
            )
        return results

    def _run_selective(self):
        start = self.clock()
        self.last_metrics = dict(
            overview_inferences=0,
            roi_requests=0,
            roi_inferences=0,
            rgb_payload_bytes=0,
            coarse_ms=0,
            fine_ms=0,
            roi_phase_ms=0,
        )
        metrics = self.last_metrics
        overview = self._capture()
        inference_start = self.clock()
        objects = self.decode(self._infer(self.detector, overview))
        metrics["coarse_ms"] = (self.clock() - inference_start) * 1000
        metrics["overview_inferences"] = 1
        self.last_overview = overview
        self.last_boxes = [o["bbox"] for o in objects]
        self.last_roi = None
        native_objects = []
        for obj in objects:
            x1, y1, x2, y2 = obj["bbox"]
            if not (0 <= x1 < x2 <= self.w and 0 <= y1 < y2 <= self.h):
                raise ValueError("Localization box outside preview")
            native_objects.append(
                dict(
                    obj,
                    bbox=[
                        x1 * self.sw / self.w,
                        y1 * self.sh / self.h,
                        x2 * self.sw / self.w,
                        y2 * self.sh / self.h,
                    ],
                )
            )
        jobs = self.scheduler.observe(native_objects)
        results = [
            dict(
                track_id=j["track_id"],
                box=j["box"],
                coarse=j["coarse"],
                overview_frame_id=overview.source_frame_id,
                status="coarse_pending",
                selection_reason=j["selection_reason"],
            )
            for j in jobs
        ]
        if self.on_result is not None:
            self.on_result("coarse", results)
        roi_start = self.clock()
        frame_bytes = self.w * self.h * 3
        stale = False
        for job in sorted(jobs, key=lambda j: j["priority"]):
            result = results[job["index"]]
            box = job["box"]
            if box[2] - box[0] > self.w or box[3] - box[1] > self.h:
                result["status"] = "object_exceeds_native_roi"
                continue
            if job["cached_fine"] is not None:
                result["cached_fine"] = job["cached_fine"]
            if not job["eligible"]:
                result["status"] = (
                    "cached"
                    if job["selection_reason"] == "stable_cached"
                    else "deferred_retry"
                )
                continue
            reason = (
                "deferred_stale_overview"
                if stale
                else self.scheduler.budget_reason(
                    metrics["roi_requests"],
                    metrics["roi_requests"] * frame_bytes,
                    roi_start,
                    frame_bytes,
                )
            )
            if reason:
                result["status"] = reason
                continue
            x = max(
                0, min(self.sw - self.w, math.floor((box[0] + box[2] - self.w) / 2))
            )
            y = max(
                0, min(self.sh - self.h, math.floor((box[1] + box[3] - self.h) / 2))
            )
            self.scheduler.attempted(job["track_id"])
            metrics["roi_requests"] += 1
            try:
                crop = self._capture(True, x, y, overview.source_frame_id)
            except StaleROIError:
                result["status"] = "stale_roi_rejected"
                stale = True
                continue
            fine_start = self.clock()
            candidates = self.decode(self._infer(self.refiner, crop))
            metrics["fine_ms"] += (self.clock() - fine_start) * 1000
            metrics["roi_inferences"] += 1
            for obj in candidates:
                b = obj["bbox"]
                obj["bbox"] = [b[0] + x, b[1] + y, b[2] + x, b[3] + y]
            match = self.scheduler.complete(
                job["track_id"], candidates, crop.source_frame_id
            )
            self.last_roi = crop
            result.pop("cached_fine", None)
            result.update(
                status="candidate" if match else "no_matching_fine_candidate",
                origin=(x, y),
                request_id=crop.request_id,
                roi_frame_id=crop.source_frame_id,
                source_frame_gap=(crop.source_frame_id - overview.source_frame_id)
                & 0xFFFFFFFF,
                fine_candidates=[] if match is None else [match],
                result_age_ms=0,
                frame_relation="later source frame",
            )
            if self.on_result is not None:
                self.on_result("fine_update", results)
        self.scheduler.finish(results)
        metrics.update(
            roi_phase_ms=(self.clock() - roi_start) * 1000,
            processing_ms=(self.clock() - start) * 1000,
            targets=len(jobs),
            reused_results=sum(r["status"] == "cached" for r in results),
            pending_targets=sum(r["status"].startswith("deferred_") for r in results),
            payload_note="RGB payload only; excludes headers, PCIe overhead and archive traffic",
        )
        return results
