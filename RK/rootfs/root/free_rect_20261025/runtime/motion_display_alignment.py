from collections import Counter, deque
import math
import time


def frame_delta(new, old):
    value = (int(new) - int(old)) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def _source(value):
    return value if type(value) is int and 0 <= value <= 0xFFFFFFFF else None


def _finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


class MotionDisplayAlignment:
    def __init__(
        self,
        fps=60.0,
        enabled=True,
        max_prediction_frames=None,
        max_capture_age_s=None,
        source_size=(1920, 1080),
        max_objects=None,
        max_source_gap=8,
        max_capture_age=0.2,
        budget_ms=6.0,
    ):
        self.fps = float(fps)
        if not math.isfinite(self.fps) or self.fps != 60.0:
            raise ValueError("Display motion requires the 60-source-FPS contract")
        self.enabled = bool(enabled)
        self.source_size = tuple(source_size)
        if len(self.source_size) != 2 or any(
            not _finite(v) or v <= 0 for v in self.source_size
        ):
            raise ValueError("Invalid full-sensor display geometry")
        self.max_objects = None if max_objects is None else max(1, int(max_objects))
        self.horizon = max(
            0,
            min(
                24,
                int(
                    max_source_gap
                    if max_prediction_frames is None
                    else max_prediction_frames
                ),
            ),
        )
        self.max_age = float(
            max_capture_age if max_capture_age_s is None else max_capture_age_s
        )
        if not math.isfinite(self.max_age) or not 0 <= self.max_age <= 0.5:
            raise ValueError("Invalid real-observation receipt-age guard")
        self.budget_ms = float(budget_ms)
        if not math.isfinite(self.budget_ms) or self.budget_ms <= 0:
            raise ValueError("Display alignment budget must be positive")
        self.counts = Counter()
        self.events = deque(maxlen=4096)
        self.timings = deque(maxlen=240)
        self.observed = set()
        self.observed_order = deque()
        self.pending_observations = set()
        self.last_rejections = []
        self.started = None
        self.last_display = None
        self.last_received_source = None
        self.epoch = None
        self.retired_epochs = deque(maxlen=32)
        self.latest_revision = None
        self.last_motion_state = None
        self.real_observations = {}

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

    def _snapshot(self, row, observed):
        motion = row.get("display_motion_v1")
        if not isinstance(motion, dict):
            return None, "global_motion_missing"
        if motion.get("evidence_source") != "trusted_real_detections":
            return None, "global_motion_untrusted_evidence"
        if _source(motion.get("anchor_source_frame_id")) != observed:
            return None, "global_motion_anchor_mismatch"
        revision = motion.get("revision")
        if type(revision) is not int or revision < 0:
            return None, "global_motion_invalid_revision"
        if motion.get("state") not in ("learning", "tracking", "relearning"):
            return None, "global_motion_invalid_state"
        return motion, None

    def _result(self, item, box, predicted, motion=None, velocity=None):
        row = item["row"]
        cached = row.get("cached_fine")
        if cached:
            cached = dict(cached)
            if any(
                name in cached and str(cached[name]) != str(row.get(name))
                for name in ("session_id", "track_id", "generation", "region_version")
            ):
                cached = None
        result = dict(
            row,
            box=list(box),
            raw_box=list(item["raw_box"]),
            cached_fine=cached,
            display_source_frame_id=item["display_source"],
            display_prediction=bool(predicted),
            display_history=False,
            display_pixel_tracked=False,
            display_only=True,
            display_observation_source_frame_id=item["observed"],
            display_observation_age_ms=item["age_ms"],
            display_source_gap=item["gap"],
            display_observation_receipt_age_ms=(
                item["now"] - row["capture_received_monotonic"]
            )
            * 1000,
            display_observation_age_basis=item["age_basis"],
            display_preview_receipt_age_ms=(
                (item["now"] - item["preview_received"]) * 1000
                if item["preview_received"] is not None
                else None
            ),
            prediction_frames=item["gap"] if predicted else 0,
            prediction_ms_estimate=(
                1000.0 * item["gap"] / self.fps if predicted else 0.0
            ),
            display_alignment_reason=(
                "global_motion_source_frame_prediction"
                if predicted
                else "same_frame_model_observation"
            ),
            display_alignment_uses_current_pixels=False,
            pixel_match_confidence=None,
            display_submit_start_monotonic=item["now"],
            preview_capture_received_monotonic=item["preview_received"],
        )
        if predicted:
            result["display_motion_v1"] = dict(
                motion, velocity_px_per_source_frame=list(velocity)
            )
            result.update(
                display_motion_revision=motion["revision"],
                display_motion_state=motion["state"],
                display_motion_anchor_source_frame_id=item["observed"],
                display_velocity_px_per_source_frame=list(velocity),
                display_prediction_can_train=False,
                display_prediction_validation="not_image_validated; display_only",
            )
        return result

    def align(
        self,
        rows,
        display_source_id,
        now,
        preview_capture_received_monotonic=None,
        rgb=None,
    ):

        at = time.perf_counter()
        source = _source(display_source_id)
        if source is None or not _finite(now):
            raise ValueError("Invalid displayed source frame or monotonic timestamp")
        if self.started is None:
            self.started = float(now)
        self.last_rejections = []
        self.pending_observations = set()
        if (
            self.last_received_source is not None
            and frame_delta(source, self.last_received_source) < 0
        ):
            self.counts["display_source_reset"] += 1
            self.observed.clear()
            self.observed_order.clear()
            self.real_observations.clear()
            self.latest_revision = None
            self.last_motion_state = None
        self.last_received_source = source

        reference = (
            now
            if preview_capture_received_monotonic is None
            else preview_capture_received_monotonic
        )
        if not _finite(reference) or not 0 <= now - reference <= self.max_age:
            self.real_observations.clear()
            for row in rows:
                self._reject(row, "missing_or_stale_preview_capture_time")
            self.timings.append((time.perf_counter() - at) * 1000)
            return []
        epochs = {self._epoch(row) for row in rows if row.get("session_id")}
        if len(epochs) > 1:
            for row in rows:
                self._reject(row, "mixed_observation_epochs")
            self.timings.append((time.perf_counter() - at) * 1000)
            return []
        if epochs:
            epoch = next(iter(epochs))
            if epoch in self.retired_epochs:
                for row in rows:
                    self._reject(row, "retired_observation_epoch")
                self.timings.append((time.perf_counter() - at) * 1000)
                return []
            if epoch != self.epoch:
                if self.epoch is not None:
                    self.retired_epochs.append(self.epoch)
                    self.counts["observation_epoch_reset"] += 1
                self.epoch = epoch
                self.latest_revision = None
                self.last_motion_state = None
                self.real_observations.clear()
        keys = [self.identity(row) for row in rows]
        duplicate_keys = {key for key, count in Counter(keys).items() if count > 1}
        prepared = []
        width, height = self.source_size
        selected_rows = rows if self.max_objects is None else rows[: self.max_objects]
        for row, key in zip(selected_rows, keys):
            observed = _source(
                row.get("observed_source_frame_id", row.get("roi_frame_id"))
            )
            raw = row.get("raw_box")
            received = row.get("capture_received_monotonic")
            reason = None
            if (
                row.get("display_prediction")
                or row.get("display_history")
                or row.get("display_pixel_tracked")
            ):
                reason = "display_prediction_input_rejected"
            elif key in duplicate_keys:
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
                not isinstance(raw, (tuple, list))
                or len(raw) != 4
                or not all(_finite(v) for v in raw)
            ):
                reason = "missing_or_invalid_real_raw_box"

            elif (
                not _finite(received)
                or received > now
                or not -1.0 / self.fps <= reference - received <= self.max_age
            ):
                reason = "missing_or_stale_capture_time"
            if reason:
                self.real_observations.pop(key, None)
                self._reject(row, reason)
                continue
            raw = tuple(float(v) for v in raw)
            if not (0 <= raw[0] < raw[2] <= width and 0 <= raw[1] < raw[3] <= height):
                self.real_observations.pop(key, None)
                self._reject(row, "invalid_real_geometry")
                continue

            history = self.real_observations.setdefault(key, deque(maxlen=8))
            for index, prior in enumerate(history):
                if prior["observed_source_frame_id"] == observed:
                    history[index] = dict(
                        row, raw_box=raw, observed_source_frame_id=observed
                    )
                    break
            else:
                if (
                    not history
                    or frame_delta(observed, history[-1]["observed_source_frame_id"])
                    > 0
                ):
                    history.append(
                        dict(row, raw_box=raw, observed_source_frame_id=observed)
                    )
            eligible = [
                (frame_delta(source, prior["observed_source_frame_id"]), prior)
                for prior in history
                if 0
                <= frame_delta(source, prior["observed_source_frame_id"])
                <= self.horizon
                and prior["capture_received_monotonic"] <= now
                and -1.0 / self.fps
                <= reference - prior["capture_received_monotonic"]
                <= self.max_age
            ]
            if eligible:
                gap, selected = min(eligible, key=lambda entry: entry[0])
                if selected["observed_source_frame_id"] != observed:
                    self.counts["earlier_real_observation_selected"] += 1
                row = selected
                observed = row["observed_source_frame_id"]
                raw = row["raw_box"]
                received = row["capture_received_monotonic"]
            gap = frame_delta(source, observed)
            if gap < 0:
                self._reject(row, "observation_from_future_or_reset", source_gap=gap)
                continue
            if gap > self.horizon:
                self._reject(
                    row,
                    "observation_source_gap_exceeded",
                    source_gap=gap,
                    max_source_gap=self.horizon,
                )
                continue
            item = dict(
                row=row,
                observed=observed,
                raw_box=raw,
                gap=gap,
                age_ms=max(0.0, reference - received) * 1000,
                display_source=source,
                now=now,
                age_basis=(
                    "wall_clock_without_preview_timestamp"
                    if preview_capture_received_monotonic is None
                    else "displayed_source_capture_received"
                ),
                preview_received=preview_capture_received_monotonic,
            )
            motion, error = self._snapshot(row, observed)
            if motion is not None and (
                self.latest_revision is None
                or motion["revision"] > self.latest_revision
            ):
                self.latest_revision = motion["revision"]
                self.last_motion_state = motion["state"]
            prepared.append((item, motion, error))
        current_keys = set(keys[: len(selected_rows)]) - duplicate_keys
        self.real_observations = {
            key: history
            for key, history in self.real_observations.items()
            if key in current_keys
        }
        output = []
        for item, motion, error in prepared:
            row = item["row"]
            if item["gap"] == 0:
                output.append(self._result(item, item["raw_box"], False))
                continue
            if not self.enabled:
                self._reject(row, "display_prediction_disabled")
                continue
            if error:
                self._reject(row, error)
                continue
            fps = motion.get("source_fps")
            if not _finite(fps) or float(fps) != self.fps:
                self._reject(row, "global_motion_invalid_source_fps")
                continue
            horizon = motion.get("max_horizon_frames")
            if type(horizon) is not int or horizon < 0:
                self._reject(row, "global_motion_invalid_horizon")
                continue
            invalidated = (
                motion["revision"] != self.latest_revision
                and self.last_motion_state != "tracking"
            )
            local_fallback = invalidated or motion["state"] in (
                "learning",
                "relearning",
            )
            projection_horizon = min(self.horizon, horizon)
            if item["gap"] > projection_horizon:
                self._reject(
                    row,
                    "global_motion_horizon_exceeded",
                    source_gap=item["gap"],
                    max_source_gap=projection_horizon,
                )
                continue
            fallback_sources = None
            fallback_evidence = "two_real_raw_observations"
            if local_fallback:

                tracking = row.get("tracking", {})

                reliable = (
                    tracking.get("stable_hits", 0) >= 2
                    and tracking.get("association_uncertain") is False
                    and tracking.get("image_layout_ambiguous") is False
                    and (
                        tracking.get("real_detection_accepted") is True
                        or tracking.get("image_support", {}).get("accepted") is True
                    )
                )
                previous = None
                if reliable:
                    for prior in reversed(
                        self.real_observations.get(self.identity(row), ())
                    ):
                        delta = frame_delta(
                            item["observed"], prior["observed_source_frame_id"]
                        )
                        prior_tracking = prior.get("tracking", {})
                        if (
                            0 < delta <= self.horizon
                            and prior["capture_received_monotonic"] <= now
                            and -1.0 / self.fps
                            <= reference - prior["capture_received_monotonic"]
                            <= self.max_age
                            and prior_tracking.get("stable_hits", 0) >= 1
                            and prior_tracking.get("association_uncertain") is False
                            and prior_tracking.get("image_layout_ambiguous") is False
                            and (
                                prior_tracking.get("real_detection_accepted") is True
                                or prior_tracking.get("image_support", {}).get(
                                    "accepted"
                                )
                                is True
                            )
                        ):
                            previous = prior
                            break
                if previous is None:

                    measured = tracking.get("measured_motion_px_per_source_frame")
                    if (
                        not reliable
                        or not isinstance(measured, (tuple, list))
                        or len(measured) != 2
                        or not all(_finite(v) for v in measured)
                    ):
                        self._reject(
                            row,
                            "local_real_motion_unavailable",
                            global_motion_state=motion["state"],
                        )
                        continue
                    velocity = list(measured)
                    fallback_sources = [item["observed"]]
                    fallback_evidence = "tracker_real_observation_displacement"
                else:
                    a, b = previous["raw_box"], item["raw_box"]
                    velocity = [
                        ((b[0] + b[2]) - (a[0] + a[2])) / (2 * delta),
                        ((b[1] + b[3]) - (a[1] + a[3])) / (2 * delta),
                    ]
                    fallback_sources = [
                        previous["observed_source_frame_id"],
                        item["observed"],
                    ]
            else:
                if motion.get("valid") is not True or motion["state"] != "tracking":
                    self._reject(row, "global_motion_" + motion["state"])
                    continue
                velocity = motion.get("velocity_px_per_source_frame")
            if (
                not isinstance(velocity, (tuple, list))
                or len(velocity) != 2
                or not all(_finite(v) for v in velocity)
            ):
                self._reject(row, "global_motion_invalid_velocity")
                continue
            dx, dy = (float(value) * item["gap"] for value in velocity)
            left, top, right, bottom = item["raw_box"]
            predicted = [left + dx, top + dy, right + dx, bottom + dy]
            if (
                predicted[2] <= 0
                or predicted[0] >= width
                or predicted[3] <= 0
                or predicted[1] >= height
            ):
                self._reject(row, "predicted_out_of_view")
                continue
            clipped = [
                max(0.0, predicted[0]),
                max(0.0, predicted[1]),
                min(width, predicted[2]),
                min(height, predicted[3]),
            ]
            result = self._result(item, clipped, True, motion, velocity)
            if local_fallback:

                result["display_motion_v1"] = dict(motion)
                result.update(
                    display_alignment_reason="same_identity_real_motion_fallback",
                    display_motion_fallback=True,
                    display_motion_evidence_source=fallback_evidence,
                    display_motion_observation_sources=fallback_sources,
                )
                self.counts["local_real_motion_fallbacks"] += 1
            output.append(result)

        self.pending_observations = {
            (self._epoch(row), row["display_observation_source_frame_id"])
            for row in output
        }
        elapsed = (time.perf_counter() - at) * 1000
        self.timings.append(elapsed)
        if elapsed > self.budget_ms:
            self.counts["display_alignment_over_budget"] += 1
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
        predictions = sum(bool(row.get("display_prediction")) for row in rows)
        self.events.append(
            (
                float(now),
                len(fresh),
                bool(rows),
                predictions,
                max((row.get("display_source_gap", 0) for row in rows), default=0),
                max(
                    (row.get("display_observation_age_ms", 0.0) for row in rows),
                    default=0.0,
                ),
            )
        )
        self.counts["display_fresh_frames"] += 1
        self.counts["prediction_box_updates"] += predictions
        self.counts["same_source_real_box_updates"] += len(rows) - predictions

    def stats(self, now):
        span = min(
            2.0,
            max(
                0.001, float(now) - (self.started if self.started is not None else now)
            ),
        )
        recent = [event for event in self.events if now - 2 < event[0] <= now]
        values = list(self.timings)
        mean = sum(values) / len(values) if values else 0.0
        return dict(
            enabled=self.enabled,
            alignment_mode="trusted_global_motion_source_frame_projection",
            linear_velocity_prediction=True,
            optical_flow_enabled=False,
            ncc_validation_enabled=False,
            counting_uses_display_boxes=False,
            semantic_votes_from_prediction=False,
            feedback_uses_display_predictions=False,
            counts=dict(self.counts),
            active_motion_histories=len(self.real_observations),
            source_image_ring_depth=0,
            display_real_observation_cache_per_identity=8,
            display_max_objects=self.max_objects,
            actual_observation_updates_per_second=sum(event[1] for event in recent)
            / span,
            frames_with_boxes_per_second=sum(event[2] for event in recent) / span,
            pixel_frame_updates_per_second=0.0,
            pixel_box_updates_total=0,
            prediction_frame_updates_per_second=sum(event[3] > 0 for event in recent)
            / span,
            historical_frame_updates_per_second=0.0,
            prediction_box_updates_total=self.counts["prediction_box_updates"],
            historical_box_updates_total=0,
            max_visible_source_gap=max((event[4] for event in recent), default=0),
            max_visible_observation_age_ms=max(
                (event[5] for event in recent), default=0.0
            ),
            pixel_alignment_ms_mean=mean,
            pixel_alignment_ms_p95=_percentile(values, 0.95),
            pixel_alignment_ms_max=max(values, default=0.0),
            pixel_alignment_budget_ms=self.budget_ms,
            max_source_gap=self.horizon,
            max_capture_age_seconds=self.max_age,
            observation_age_basis="displayed_source_capture_received",
            max_preview_receipt_age_seconds=self.max_age,
            last_motion_revision=self.latest_revision,
            last_motion_state=self.last_motion_state,
            sensor_exposure_timestamp_available=False,
            physical_scanout_measured=False,
            geometry_validation="display projection from trusted real-detection global velocity; no pixel self-validation or ground-truth claim",
        )


DisplayAlignment = MotionDisplayAlignment
