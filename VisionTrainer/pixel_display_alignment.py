from collections import Counter, OrderedDict, deque
import math
import time

import cv2
import numpy as np


def frame_delta(new, old):
    value = (int(new) - int(old)) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def _first(row, *names):
    return next((row[name] for name in names if row.get(name) is not None), None)


def _overlap(a, b):
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    return inter / max(
        1e-9, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    )


class PixelDisplayAlignment:
    def __init__(
        self,
        fps=60.0,
        enabled=True,
        max_prediction_frames=None,
        max_capture_age_s=None,
        max_speed_px_per_frame=None,
        max_observation_interval_frames=None,
        source_size=(1920, 1080),
        history_frames=16,
        max_points=8,
        max_objects=12,
        max_source_gap=8,
        max_capture_age=0.2,
        max_displacement_preview=48.0,
        min_correlation=0.60,
        max_fb_error=0.8,
        budget_ms=3.0,
    ):
        self.fps = float(fps)
        self.enabled = bool(enabled)
        self.source_size = tuple(source_size)
        self.history_frames = max(3, min(24, int(history_frames)))
        self.max_points = max(4, min(16, int(max_points)))
        self.max_objects = max(1, min(24, int(max_objects)))

        self.horizon = int(
            max_source_gap if max_prediction_frames is None else max_prediction_frames
        )
        self.horizon = max(0, min(self.history_frames - 1, self.horizon))
        self.max_age = float(
            max_capture_age if max_capture_age_s is None else max_capture_age_s
        )
        self.max_age = max(0.0, min(0.3, self.max_age))
        self.max_displacement = float(max_displacement_preview)
        self.min_correlation = float(min_correlation)
        self.max_fb_error = float(max_fb_error)
        self.budget_ms = float(budget_ms)

        cv2.setNumThreads(1)
        self.frames = OrderedDict()
        self.features = OrderedDict()
        self.windows = {}
        self.counts = Counter()
        self.events = deque(maxlen=4096)
        self.timings = deque(maxlen=240)
        self.started = None
        self.last_display = None
        self.last_received = None
        self.epoch = None
        self.last_rejections = []
        self.pending_observations = set()
        self.observed = set()
        self.observed_order = deque()

    @staticmethod
    def identity(row):
        return tuple(
            str(row.get(name, ""))
            for name in (
                "session_id",
                "track_id",
                "generation",
                "region_version",
                "boot_id",
                "sensor_generation",
            )
        )

    @staticmethod
    def _epoch(row):
        return tuple(
            str(row.get(name, ""))
            for name in (
                "session_id",
                "generation",
                "region_version",
                "boot_id",
                "sensor_generation",
            )
        )

    def _reject(self, row, reason, **extra):
        self.last_rejections.append(
            dict(track_id=row.get("track_id"), reason=reason, **extra)
        )
        self.counts[reason] += 1

    def _clear_images(self, reason):
        self.frames.clear()
        self.features.clear()
        self.windows.clear()
        self.counts[reason] += 1

    def _image(self, rgb, source):
        if rgb is None:
            return None
        array = np.asarray(rgb)
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
            raise ValueError("Display alignment expects the supplied uint8 RGB preview")
        if (
            self.last_received is not None
            and frame_delta(source, self.last_received) < 0
        ):
            self._clear_images("preview_source_reset")
        self.last_received = int(source)
        if source in self.frames:
            return self.frames[source]
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        if self.frames and next(iter(self.frames.values())).shape != gray.shape:
            self._clear_images("preview_geometry_reset")
        self.frames[int(source)] = gray
        while len(self.frames) > self.history_frames:
            self.frames.popitem(last=False)
        return gray

    def _points(self, gray, box, key):
        cached = self.features.get(key)
        if cached is not None:
            return cached
        h, w = gray.shape
        left = max(0, int(math.floor(box[0])) + 1)
        top = max(0, int(math.floor(box[1])) + 1)
        right = min(w, int(math.ceil(box[2])) - 1)
        bottom = min(h, int(math.ceil(box[3])) - 1)
        if right - left < 5 or bottom - top < 5:
            return np.empty((0, 2), np.float32)
        points = cv2.goodFeaturesToTrack(
            gray[top:bottom, left:right],
            maxCorners=self.max_points,
            qualityLevel=0.025,
            minDistance=2.0,
            blockSize=3,
            useHarrisDetector=False,
        )
        if points is None:
            points = np.empty((0, 2), np.float32)
        else:
            points = points.reshape(-1, 2) + np.array([left, top], np.float32)
        self.features[key] = points
        while len(self.features) > self.history_frames * self.max_objects:
            self.features.popitem(last=False)
        return points

    def _phase(self, old, current, items):

        h, w = old.shape
        padding = int(math.ceil(self.max_displacement)) + 2
        vertical = max(
            8, int(max(i["preview_box"][3] - i["preview_box"][1] for i in items) * 0.5)
        )
        l = max(0, int(min(i["preview_box"][0] for i in items)) - padding)
        t = max(0, int(min(i["preview_box"][1] for i in items)) - vertical)
        r = min(w, int(max(i["preview_box"][2] for i in items)) + padding)
        b = min(h, int(max(i["preview_box"][3] for i in items)) + vertical)
        if r - l < 12 or b - t < 12:
            return (0.0, 0.0), 0.0
        shape = (r - l, b - t)
        window = self.windows.get(shape)
        if window is None:
            window = cv2.createHanningWindow(shape, cv2.CV_32F)
            if len(self.windows) >= 8:
                self.windows.clear()
            self.windows[shape] = window

        a = cv2.Sobel(old[t:b, l:r], cv2.CV_32F, 1, 0, ksize=3)
        c = cv2.Sobel(current[t:b, l:r], cv2.CV_32F, 1, 0, ksize=3)
        shift, response = cv2.phaseCorrelate(a, c, window)
        if (
            not all(math.isfinite(float(v)) for v in (*shift, response))
            or math.hypot(*shift) > self.max_displacement
        ):
            return (0.0, 0.0), 0.0
        return shift, float(max(0.0, min(1.0, response)))

    def _correlation(self, old, current, box, matrix):
        h, w = old.shape
        l = max(0, int(math.ceil(box[0])))
        t = max(0, int(math.ceil(box[1])))
        r = min(w, int(math.floor(box[2])))
        b = min(h, int(math.floor(box[3])))
        if r - l < 5 or b - t < 5:
            return -1.0
        patch = old[t:b, l:r]
        if float(np.std(patch)) < 5.0:
            return -1.0
        local = matrix.copy()
        local[:, 2] += matrix[:, :2] @ np.array([l, t], np.float64)
        measured = cv2.warpAffine(
            current,
            local,
            (r - l, b - t),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        return float(cv2.matchTemplate(patch, measured, cv2.TM_CCOEFF_NORMED)[0, 0])

    def _template(self, old, current, item, shift, response):

        box = item["preview_box"]
        h, w = old.shape
        l = max(0, int(math.ceil(box[0])))
        t = max(0, int(math.ceil(box[1])))
        r = min(w, int(math.floor(box[2])))
        b = min(h, int(math.floor(box[3])))
        patch = old[t:b, l:r]
        if patch.shape[0] < 6 or patch.shape[1] < 6 or float(np.std(patch)) < 5.0:
            return None
        radius = 12 if response >= 0.20 else int(self.max_displacement)
        cx = l + (shift[0] if response >= 0.20 else 0.0)
        cy = t + (shift[1] if response >= 0.20 else 0.0)
        left = max(0, int(math.floor(cx)) - radius)
        top = max(0, int(math.floor(cy)) - radius)
        right = min(w, int(math.ceil(cx)) + patch.shape[1] + radius)
        bottom = min(h, int(math.ceil(cy)) + patch.shape[0] + radius)
        search = current[top:bottom, left:right]
        if search.shape[0] < patch.shape[0] or search.shape[1] < patch.shape[1]:
            return None
        scores = cv2.matchTemplate(search, patch, cv2.TM_CCOEFF_NORMED)
        _, best, _, location = cv2.minMaxLoc(scores)
        if best < 0.78:
            return None

        competitors = scores.copy()
        xx, yy = location
        exclusion_x = max(3, patch.shape[1] // 3)
        exclusion_y = max(3, patch.shape[0] // 3)
        competitors[
            max(0, yy - exclusion_y) : yy + exclusion_y + 1,
            max(0, xx - exclusion_x) : xx + exclusion_x + 1,
        ] = -1.0
        second = float(competitors.max()) if competitors.size else -1.0
        margin = float(best) - second
        if margin < 0.07:
            return None
        dx = left + xx - l
        dy = top + yy - t
        if math.hypot(dx, dy) > self.max_displacement:
            return None

        measured = current[
            top + yy : top + yy + patch.shape[0], left + xx : left + xx + patch.shape[1]
        ]
        back_radius = 5
        bl = max(0, l - back_radius)
        bt = max(0, t - back_radius)
        br = min(w, r + back_radius)
        bb = min(h, b + back_radius)
        reverse = cv2.matchTemplate(old[bt:bb, bl:br], measured, cv2.TM_CCOEFF_NORMED)
        _, reverse_score, _, reverse_at = cv2.minMaxLoc(reverse)
        if (
            reverse_score < 0.75
            or math.hypot(bl + reverse_at[0] - l, bt + reverse_at[1] - t) > 1.5
        ):
            return None
        sx = self.source_size[0] / w
        sy = self.source_size[1] / h
        candidate = [
            (box[0] + dx) * sx,
            (box[1] + dy) * sy,
            (box[2] + dx) * sx,
            (box[3] + dy) * sy,
        ]
        if not (
            0 <= candidate[0] < candidate[2] <= self.source_size[0]
            and 0 <= candidate[1] < candidate[3] <= self.source_size[1]
        ):
            return None
        return self._result(
            item,
            candidate,
            True,
            float(best),
            method="current_template_unique_forward_backward",
            pixel_correlation=float(best),
            pixel_template_margin=margin,
            pixel_reverse_correlation=float(reverse_score),
            pixel_phase_response=response,
            pixel_features=0,
            pixel_inliers=0,
            pixel_fb_error_preview=0.0,
        )

    def _group(self, old, current, items, deadline):
        points = []
        offsets = []
        for item in items:
            p = self._points(old, item["preview_box"], item["feature_key"])
            offsets.append((len(points), len(points) + len(p)))
            points.extend(p)
        shift, response = self._phase(old, current, items)
        if not points:
            output = []
            for item in items:
                result = (
                    self._template(old, current, item, shift, response)
                    if time.perf_counter() < deadline
                    else None
                )
                if result is not None:
                    output.append(result)
                else:
                    self._reject(item["row"], "insufficient_pixel_features")
            return output
        p0 = np.asarray(points, np.float32).reshape(-1, 1, 2)

        height, width = old.shape
        px = (
            int(math.ceil(abs(shift[0]) if response >= 0.16 else self.max_displacement))
            + 24
        )
        py = (
            int(math.ceil(abs(shift[1]) if response >= 0.16 else self.max_displacement))
            + 24
        )
        left = max(0, int(p0[:, :, 0].min()) - px)
        top = max(0, int(p0[:, :, 1].min()) - py)
        right = min(width, int(p0[:, :, 0].max()) + px + 1)
        bottom = min(height, int(p0[:, :, 1].max()) + py + 1)
        offset = np.array([left, top], np.float32)
        p0 = p0 - offset
        flow_old = np.ascontiguousarray(old[top:bottom, left:right])
        flow_current = np.ascontiguousarray(current[top:bottom, left:right])
        initial = (p0 + np.array(shift, np.float32)) if response >= 0.16 else None
        lk = dict(
            winSize=(11, 11),
            maxLevel=1,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 12, 0.015),
            minEigThreshold=1e-4,
        )
        p1, status, error = cv2.calcOpticalFlowPyrLK(
            flow_old,
            flow_current,
            p0,
            initial,
            flags=cv2.OPTFLOW_USE_INITIAL_FLOW if initial is not None else 0,
            **lk
        )
        if p1 is None:
            for item in items:
                self._reject(item["row"], "pixel_flow_failed")
            return []
        back, back_status, _ = cv2.calcOpticalFlowPyrLK(
            flow_current,
            flow_old,
            p1,
            p0.copy(),
            flags=cv2.OPTFLOW_USE_INITIAL_FLOW,
            **lk
        )
        if back is None:
            for item in items:
                self._reject(item["row"], "pixel_backward_flow_failed")
            return []
        p0 = p0.reshape(-1, 2) + offset
        p1 = p1.reshape(-1, 2) + offset
        back = back.reshape(-1, 2) + offset
        fb = np.linalg.norm(back - p0, axis=1)
        good = (
            (status.ravel() != 0)
            & (back_status.ravel() != 0)
            & np.isfinite(p1).all(axis=1)
            & (fb <= self.max_fb_error)
        )
        if error is not None:
            good &= error.ravel() <= 24.0
        output = []
        fallback = []
        for item, (begin, end) in zip(items, offsets):
            row = item["row"]
            valid = good[begin:end]
            if end - begin < 4 or int(valid.sum()) < max(
                4, math.ceil((end - begin) * 0.5)
            ):
                fallback.append(
                    (
                        item,
                        "pixel_match_not_consistent",
                        dict(
                            pixel_features=end - begin,
                            consistent_features=int(valid.sum()),
                        ),
                    )
                )
                continue
            a = p0[begin:end][valid]
            b = p1[begin:end][valid]
            matrix, inliers = cv2.estimateAffinePartial2D(
                a,
                b,
                method=cv2.RANSAC,
                ransacReprojThreshold=0.9,
                maxIters=35,
                confidence=0.95,
                refineIters=3,
            )
            if matrix is None or inliers is None or int(inliers.sum()) < 4:
                fallback.append((item, "pixel_transform_not_supported", {}))
                continue
            inlier_mask = inliers.ravel().astype(bool)
            scale = math.hypot(matrix[0, 0], matrix[1, 0])
            rotation = abs(math.atan2(matrix[1, 0], matrix[0, 0]))
            displacement = np.median(b - a, axis=0)
            if (
                not 0.85 <= scale <= 1.18
                or rotation > 0.25
                or math.hypot(*displacement) > self.max_displacement
            ):
                fallback.append((item, "pixel_transform_outside_capture_range", {}))
                continue
            correlation = self._correlation(old, current, item["preview_box"], matrix)
            if correlation < self.min_correlation:
                fallback.append(
                    (
                        item,
                        "current_pixel_appearance_mismatch",
                        dict(correlation=round(correlation, 4)),
                    )
                )
                continue
            if (
                math.hypot(*displacement) > 8.0
                and response < 0.16
                and (
                    correlation < 0.80
                    or float(np.median(fb[begin:end][valid][inlier_mask])) > 0.3
                )
            ):
                fallback.append((item, "large_pixel_motion_unverified", {}))
                continue
            l, t, r, bottom = item["preview_box"]
            corners = (
                np.array(
                    [[l, t, 1], [r, t, 1], [r, bottom, 1], [l, bottom, 1]], np.float64
                )
                @ matrix.T
            )
            candidate = [
                float(corners[:, 0].min()),
                float(corners[:, 1].min()),
                float(corners[:, 0].max()),
                float(corners[:, 1].max()),
            ]
            h, w = current.shape
            cx = (candidate[0] + candidate[2]) / 2
            cy = (candidate[1] + candidate[3]) / 2
            if not 0 <= cx < w or not 0 <= cy < h:
                self._reject(row, "pixel_match_out_of_view")
                continue
            sx = self.source_size[0] / w
            sy = self.source_size[1] / h
            box = [
                max(0.0, candidate[0]) * sx,
                max(0.0, candidate[1]) * sy,
                min(w, candidate[2]) * sx,
                min(h, candidate[3]) * sy,
            ]
            confidence = (
                max(0.0, min(1.0, correlation))
                * float(inlier_mask.sum())
                / (end - begin)
            )
            output.append(
                self._result(
                    item,
                    box,
                    True,
                    confidence,
                    method="lk_fb_affine_ncc",
                    pixel_correlation=correlation,
                    pixel_phase_response=response,
                    pixel_features=end - begin,
                    pixel_inliers=int(inlier_mask.sum()),
                    pixel_fb_error_preview=float(
                        np.median(fb[begin:end][valid][inlier_mask])
                    ),
                )
            )
        for item, reason, extra in fallback:
            result = (
                self._template(old, current, item, shift, response)
                if time.perf_counter() < deadline
                else None
            )
            if result is not None:
                output.append(result)
            else:
                self._reject(item["row"], reason, **extra)
        return output

    def _result(self, item, box, pixel, confidence, method, **extra):
        row = item["row"]
        cached = row.get("cached_fine")
        if cached:
            cached = dict(cached)
            if any(
                k in cached and str(cached[k]) != str(row.get(k))
                for k in ("session_id", "track_id", "generation", "region_version")
            ):
                cached = None
        base = dict(row)
        base.pop("display_velocity_px_per_source_frame", None)
        return dict(
            base,
            raw_box=list(item["raw_box"]),
            box=box,
            cached_fine=cached,
            display_source_frame_id=item["display_source"],
            display_prediction=False,
            display_history=False,
            display_pixel_tracked=bool(pixel),
            pixel_match_confidence=float(confidence),
            display_observation_source_frame_id=item["source"],
            display_observation_age_ms=item["age_ms"],
            display_source_gap=item["gap"],
            prediction_frames=item["gap"],
            prediction_ms_estimate=0.0,
            display_alignment_reason=method,
            display_alignment_uses_current_pixels=bool(pixel),
            display_submit_start_monotonic=item["now"],
            preview_capture_received_monotonic=item["preview_received"],
            **extra
        )

    def align(
        self,
        rows,
        display_source_id,
        now,
        preview_capture_received_monotonic=None,
        rgb=None,
    ):
        at = time.perf_counter()
        source = int(display_source_id)
        if self.started is None:
            self.started = float(now)
        self.last_rejections = []
        self.pending_observations = set()
        epochs = {self._epoch(row) for row in rows if row.get("session_id")}
        if len(epochs) > 1:
            for row in rows:
                self._reject(row, "mixed_image_epochs")
            self._clear_images("image_epoch_reset")
            return []
        if len(epochs) == 1:
            epoch = next(iter(epochs))
            if self.epoch is not None and epoch != self.epoch:
                self._clear_images("image_epoch_reset")
            self.epoch = epoch
        current = self._image(rgb, source)
        if current is not None:
            ph, pw = current.shape
            sx = pw / self.source_size[0]
            sy = ph / self.source_size[1]
        else:
            sx = sy = 0.0
        keys = [self.identity(r) for r in rows]
        duplicates = {k for k, n in Counter(keys).items() if n > 1}
        groups = OrderedDict()
        output = []
        for row, key in zip(rows[: self.max_objects], keys):
            observed = _first(
                row,
                "observed_source_frame_id",
                "observation_source_frame_id",
                "roi_frame_id",
            )
            raw = _first(row, "observed_box", "raw_box", "box")
            received = _first(
                row, "capture_received_monotonic", "capture_return_monotonic"
            )
            reason = None
            if key in duplicates:
                reason = "duplicate_identity"
            elif (
                not row.get("session_id")
                or row.get("track_id") is None
                or observed is None
            ):
                reason = "missing_identity_or_source"
            elif row.get("sensor_transition"):
                reason = "sensor_transition"
            elif row.get("lifecycle") in ("exited", "retired"):
                reason = "exited"
            elif (
                raw is None
                or len(raw) != 4
                or not all(math.isfinite(float(v)) for v in raw)
            ):
                reason = "invalid_geometry"
            elif (
                received is None
                or not math.isfinite(float(received))
                or not 0 <= now - float(received) <= self.max_age
            ):
                reason = "missing_or_stale_capture_time"
            if reason:
                self._reject(row, reason)
                continue
            box = tuple(map(float, raw))
            if not (
                0 <= box[0] < box[2] <= self.source_size[0]
                and 0 <= box[1] < box[3] <= self.source_size[1]
            ):
                self._reject(row, "invalid_geometry")
                continue
            gap = frame_delta(source, observed)
            if not 0 <= gap <= self.horizon:
                self._reject(row, "future_or_expired_source", source_gap=gap)
                continue
            item = dict(
                row=row,
                key=key,
                source=int(observed),
                raw_box=box,
                gap=gap,
                age_ms=(now - float(received)) * 1000,
                display_source=source,
                now=now,
                preview_received=preview_capture_received_monotonic,
            )
            if gap == 0:
                output.append(
                    self._result(
                        item,
                        list(box),
                        False,
                        1.0,
                        method="same_frame_model_observation",
                    )
                )
            elif not self.enabled:
                self._reject(row, "pixel_alignment_disabled")
            elif current is None:
                self._reject(row, "missing_current_image")
            elif int(observed) not in self.frames:
                self._reject(row, "model_source_image_unavailable")
            else:
                item["preview_box"] = (
                    box[0] * sx,
                    box[1] * sy,
                    box[2] * sx,
                    box[3] * sy,
                )
                item["feature_key"] = (key, int(observed), box)
                groups.setdefault(int(observed), []).append(item)
        for old_source, items in groups.items():
            if (time.perf_counter() - at) * 1000 > self.budget_ms:
                for item in items:
                    self._reject(item["row"], "pixel_alignment_budget_exhausted")
                continue
            output.extend(
                self._group(
                    self.frames[old_source], current, items, at + self.budget_ms / 1000
                )
            )

        colliding = set()
        for i, a in enumerate(output):
            for j in range(i + 1, len(output)):
                b = output[j]
                if _overlap(a["box"], b["box"]) > 0.60 and (
                    a["display_pixel_tracked"] or b["display_pixel_tracked"]
                ):
                    colliding.update((i, j))
        if colliding:
            self.counts["pixel_identity_collision"] += len(colliding)
            output = [row for index, row in enumerate(output) if index not in colliding]
        self.pending_observations = {
            (self._epoch(row), row["display_observation_source_frame_id"])
            for row in output
        }
        elapsed = (time.perf_counter() - at) * 1000
        self.timings.append(elapsed)
        if elapsed > self.budget_ms:
            self.counts["pixel_alignment_over_budget"] += 1
        return output

    def presented(self, source, rows, now):
        if self.last_display == source:
            return
        self.last_display = source
        fresh = self.pending_observations - self.observed
        for key in fresh:
            self.observed.add(key)
            self.observed_order.append(key)
        while len(self.observed_order) > 1024:
            self.observed.discard(self.observed_order.popleft())
        self.events.append(
            (
                float(now),
                len(fresh),
                bool(rows),
                sum(bool(r.get("display_pixel_tracked")) for r in rows),
                max((r.get("prediction_frames", 0) for r in rows), default=0),
                max(
                    (r.get("display_observation_age_ms", 0.0) for r in rows),
                    default=0.0,
                ),
            )
        )
        self.counts["display_fresh_frames"] += 1
        self.counts["pixel_box_updates"] += sum(
            bool(r.get("display_pixel_tracked")) for r in rows
        )

    def stats(self, now):
        span = min(
            2.0,
            max(
                0.001, float(now) - (self.started if self.started is not None else now)
            ),
        )
        recent = [event for event in self.events if now - 2 < event[0] <= now]
        values = list(self.timings)
        return dict(
            enabled=self.enabled,
            alignment_mode="exact_source_image_to_current_lk_fb_affine_ncc",
            linear_velocity_prediction=False,
            counting_uses_display_boxes=False,
            counts=dict(self.counts),
            active_motion_histories=0,
            source_image_ring_depth=len(self.frames),
            actual_observation_updates_per_second=sum(e[1] for e in recent) / span,
            frames_with_boxes_per_second=sum(e[2] for e in recent) / span,
            pixel_frame_updates_per_second=sum(e[3] > 0 for e in recent) / span,
            pixel_box_updates_total=self.counts["pixel_box_updates"],
            prediction_frame_updates_per_second=0.0,
            historical_frame_updates_per_second=0.0,
            prediction_box_updates_total=0,
            historical_box_updates_total=0,
            max_visible_source_gap=max((e[4] for e in recent), default=0),
            max_visible_observation_age_ms=max((e[5] for e in recent), default=0.0),
            pixel_alignment_ms_mean=float(np.mean(values)) if values else 0.0,
            pixel_alignment_ms_p95=float(np.percentile(values, 95)) if values else 0.0,
            pixel_alignment_ms_max=max(values, default=0.0),
            pixel_alignment_budget_ms=self.budget_ms,
            max_source_gap=self.horizon,
            max_capture_age_seconds=self.max_age,
            pixel_capture_range_preview_px=self.max_displacement,
            pixel_points_per_box=self.max_points,
            sensor_exposure_timestamp_available=False,
            physical_scanout_measured=False,
            geometry_validation="current image LK forward/backward, affine inliers and appearance correlation; not human position truth",
        )


DisplayAlignment = PixelDisplayAlignment
