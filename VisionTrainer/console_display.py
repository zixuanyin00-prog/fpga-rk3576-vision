from collections import Counter, deque
import math
import os


def frame_delta(new, old):
    value = (int(new) - int(old)) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def overlap(a, b):
    area = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    return area / max(
        1e-9, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - area
    )


def first(row, *names):
    return next((row[n] for n in names if row.get(n) is not None), None)


class DisplaySummary:

    def __init__(self, period=0.5):
        self.period = float(period)
        self.started = None
        self.snapshot = dict(available=False)
        self._reset()

    def _reset(self):
        self.frames = 0
        self.visible = 0
        self.history = 0
        self.predicted = 0
        self.current = 0
        self.pixel = 0
        self.gap = 0
        self.age = 0.0
        self.last_box_source = None

    def add(self, state, now):
        if self.started is None:
            self.started = float(now)
        history = int(state.get("history_boxes", 0))
        predicted = int(state.get("predicted_boxes", 0))
        current = int(state.get("current_boxes", 0))
        pixel = int(state.get("pixel_boxes", 0))
        self.frames += 1
        self.visible += bool(history or predicted or current or pixel)
        self.pixel = max(self.pixel, pixel)
        self.history = max(self.history, history)
        self.predicted = max(self.predicted, predicted)
        self.current = max(self.current, current)
        if history or predicted or current or pixel:
            self.gap = max(self.gap, int(state.get("max_source_gap", 0)))
            self.age = max(self.age, float(state.get("max_age_ms", 0.0)))
            self.last_box_source = state.get("source_frame_id")
        elapsed = float(now) - self.started
        if elapsed >= self.period:
            self.snapshot = dict(
                available=True,
                scope="recent_display_interval",
                seconds=round(elapsed, 3),
                presentations=self.frames,
                presentations_with_boxes=self.visible,
                visible_percent=round(100 * self.visible / max(1, self.frames), 1),
                history_boxes=self.history,
                predicted_boxes=self.predicted,
                current_boxes=self.current,
                pixel_boxes=self.pixel,
                max_source_gap=self.gap,
                max_age_ms=round(self.age, 1),
                last_box_source_frame_id=self.last_box_source,
                window_ended_monotonic=float(now),
            )
            self.started = float(now)
            self._reset()
        return dict(self.snapshot)


class DisplayAlignment:
    def __init__(
        self,
        fps=60.0,
        max_prediction_frames=None,
        max_capture_age_s=None,
        max_speed_px_per_frame=60.0,
        enabled=True,
        max_observation_interval_frames=None,
    ):
        self.fps = float(fps)

        self.horizon = max(
            0,
            min(
                12,
                int(
                    max_prediction_frames
                    if max_prediction_frames is not None
                    else os.environ.get("VISION_DISPLAY_MAX_PREDICTION_FRAMES", "12")
                ),
            ),
        )
        self.max_age = max(
            0.0,
            min(
                0.25,
                float(
                    max_capture_age_s
                    if max_capture_age_s is not None
                    else os.environ.get("VISION_DISPLAY_MAX_CAPTURE_AGE_S", ".25")
                ),
            ),
        )
        self.observation_horizon = max(
            1,
            min(
                18,
                int(
                    max_observation_interval_frames
                    if max_observation_interval_frames is not None
                    else os.environ.get(
                        "VISION_DISPLAY_MAX_OBSERVATION_INTERVAL_FRAMES", "18"
                    )
                ),
            ),
        )
        self.max_speed = float(max_speed_px_per_frame)
        self.enabled = enabled
        self.tracks = {}
        self.counts = Counter()
        self.events = deque(maxlen=4096)
        self.residuals = deque(maxlen=512)
        self.started = None
        self.last_display = None
        self.last_observation_event = None
        self.observed_frames_seen = set()
        self.observed_frames_order = deque()

    @staticmethod
    def identity(row):

        return tuple(
            str(row.get(k, ""))
            for k in (
                "session_id",
                "track_id",
                "generation",
                "region_version",
                "boot_id",
                "sensor_generation",
            )
        )

    def _observe(self, row, source, box, now, key):
        t = self.tracks.get(key)
        if t is None:
            t = dict(
                source=source,
                box=box,
                velocity=(0.0, 0.0),
                ready=False,
                steps=0,
                touched=now,
            )
            self.tracks[key] = t
            self.counts["real_observations"] += 1
            return t
        delta = frame_delta(source, t["source"])
        if delta == 0:

            return t
        if delta < 0:
            self.counts["out_of_order_observations"] += 1
            return None
        self.counts["real_observations"] += 1
        old_c, new_c = center(t["box"]), center(box)
        vx, vy = ((new_c[i] - old_c[i]) / delta for i in range(2))
        speed = math.hypot(vx, vy)
        old_v = t["velocity"]
        old_speed = math.hypot(*old_v)
        diag = math.hypot(box[2] - box[0], box[3] - box[1])
        residual = math.hypot(
            new_c[0] - old_c[0] - old_v[0] * delta,
            new_c[1] - old_c[1] - old_v[1] * delta,
        )

        if t["ready"] and delta <= self.observation_horizon:
            self.residuals.append(
                dict(
                    source_frame_id=source,
                    gap=delta,
                    raw_hold_px=math.dist(old_c, new_c),
                    predicted_px=residual,
                )
            )
        reversal = old_speed > 0.6 and speed > 0.6 and vx * old_v[0] + vy * old_v[1] < 0
        jump = residual > max(14.0, diag * 0.45) and t["steps"] > 0
        discontinuity = (
            delta > self.observation_horizon
            or speed > self.max_speed
            or reversal
            or jump
        )
        if discontinuity:
            t.update(velocity=(0.0, 0.0), ready=False, steps=0)
            self.counts["motion_resets"] += 1
        else:

            if speed < 0.35:
                velocity = (0.0, 0.0)
            elif t["steps"] == 0 or speed < old_speed * 0.5:
                velocity = (vx, vy)
            else:
                velocity = (0.75 * vx + 0.25 * old_v[0], 0.75 * vy + 0.25 * old_v[1])
            t.update(velocity=velocity, steps=t["steps"] + 1, ready=t["steps"] + 1 >= 2)
        t.update(source=source, box=box, touched=now)
        return t

    def align(
        self, rows, display_source_id, now, preview_capture_received_monotonic=None
    ):
        if self.started is None:
            self.started = now

        output = []
        self.last_rejections = []
        keys = [self.identity(r) for r in rows]
        duplicate = {k for k, n in Counter(keys).items() if n > 1}
        observations = set()
        for row, key in zip(rows, keys):
            reason = None
            source = first(
                row,
                "observed_source_frame_id",
                "observation_source_frame_id",
                "roi_frame_id",
            )
            raw = first(row, "observed_box", "raw_box", "box")
            received = first(
                row, "capture_received_monotonic", "capture_return_monotonic"
            )
            if key in duplicate:
                reason = "duplicate_identity"
            elif (
                not row.get("session_id")
                or row.get("track_id") is None
                or source is None
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
            if reason is not None:
                self.tracks.pop(key, None)
                self.last_rejections.append(
                    dict(track_id=row.get("track_id"), reason=reason)
                )
                self.counts[reason] += 1
                continue
            box = tuple(float(v) for v in raw)
            if not (0 <= box[0] < box[2] <= 1920 and 0 <= box[1] < box[3] <= 1080):
                self.counts["invalid_geometry"] += 1
                continue
            gap = frame_delta(display_source_id, source)
            if not 0 <= gap <= self.horizon:
                self.last_rejections.append(
                    dict(
                        track_id=row["track_id"],
                        reason="future_or_expired_source",
                        source_gap=gap,
                    )
                )
                self.counts["future_or_expired_source"] += 1
                continue
            ambiguous = bool(
                row.get("association_uncertain") or row.get("association_ambiguous")
            )
            if ambiguous:

                self.tracks.pop(key, None)
                t = dict(velocity=(0.0, 0.0), ready=False)
                self.counts["ambiguous_history_only"] += 1
            else:
                t = self._observe(row, int(source), box, now, key)
            if t is None:
                continue
            observations.add((key[0], int(source)))
            predicted = bool(gap and t["ready"] and self.enabled and not ambiguous)
            velocity = t["velocity"] if predicted else (0.0, 0.0)
            dx, dy = velocity[0] * gap, velocity[1] * gap
            max_shift = min(80.0, 0.8 * math.hypot(box[2] - box[0], box[3] - box[1]))
            shift = math.hypot(dx, dy)
            if shift > max_shift:
                self.counts["displacement_limited"] += 1
                dx *= max_shift / shift
                dy *= max_shift / shift
            candidate = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]

            cx, cy = center(candidate)
            if not 0 <= cx < 1920 or not 0 <= cy < 1080:
                self.counts["out_of_view"] += 1
                continue
            candidate = [
                max(0.0, candidate[0]),
                max(0.0, candidate[1]),
                min(1920.0, candidate[2]),
                min(1080.0, candidate[3]),
            ]
            semantic = row.get("cached_fine")
            if semantic:
                semantic = dict(semantic)

                if any(
                    k in semantic and str(semantic[k]) != str(row.get(k))
                    for k in ("session_id", "track_id", "generation", "region_version")
                ):
                    semantic = None
                    self.counts["semantic_identity_mismatch"] += 1
            out = dict(
                row,
                raw_box=list(box),
                box=candidate,
                cached_fine=semantic,
                display_source_frame_id=int(display_source_id),
                display_prediction=predicted,
                display_history=bool(gap and not predicted),
                display_observation_source_frame_id=int(source),
                display_observation_age_ms=(now - float(received)) * 1000,
                prediction_frames=gap,
                prediction_ms_estimate=1000 * gap / self.fps,
                display_alignment_reason=(
                    "bounded_velocity"
                    if predicted
                    else "historical_observation" if gap else "same_frame_observation"
                ),
                display_velocity_px_per_source_frame=list(velocity),
                capture_to_display_submit_start_ms=(now - float(received)) * 1000,
                display_submit_start_monotonic=now,
                preview_capture_received_monotonic=preview_capture_received_monotonic,
            )
            output.append(out)

        colliding = set()
        for i, a in enumerate(output):
            for j in range(i + 1, len(output)):
                b = output[j]
                if overlap(a["box"], b["box"]) > 0.55 and (
                    a["display_prediction"] or b["display_prediction"]
                ):
                    colliding.update((i, j))
        if colliding:
            self.counts["predicted_collision_suppressed"] += len(colliding)
            output = [r for i, r in enumerate(output) if i not in colliding]
        for key, t in list(self.tracks.items()):
            if now - t["touched"] > self.max_age:
                del self.tracks[key]

        if len(self.tracks) > 256:
            for key in sorted(self.tracks, key=lambda k: self.tracks[k]["touched"])[
                :-256
            ]:
                del self.tracks[key]
        self.pending_observations = observations
        return output

    def presented(self, source, rows, now):
        if self.last_display == source:
            return
        self.last_display = source

        new_observations = self.pending_observations - self.observed_frames_seen
        fresh = len(new_observations)
        for observation in sorted(new_observations):
            self.observed_frames_seen.add(observation)
            self.observed_frames_order.append(observation)
        while len(self.observed_frames_order) > 1024:
            self.observed_frames_seen.discard(self.observed_frames_order.popleft())
        self.events.append(
            (
                now,
                fresh,
                bool(rows),
                any(r["display_prediction"] for r in rows),
                len(rows),
                any(r.get("display_history") for r in rows),
                max((r["prediction_frames"] for r in rows), default=0),
                max((r["display_observation_age_ms"] for r in rows), default=0.0),
            )
        )
        self.counts["display_fresh_frames"] += 1
        self.counts["prediction_box_updates"] += sum(
            r["display_prediction"] for r in rows
        )
        self.counts["historical_box_updates"] += sum(
            r.get("display_history", False) for r in rows
        )

    def stats(self, now):
        span = min(
            2.0, max(0.001, now - (self.started if self.started is not None else now))
        )
        recent = [e for e in self.events if now - 2 < e[0] <= now]
        n = len(self.residuals)
        return dict(
            enabled=self.enabled,
            counts=dict(self.counts),
            active_motion_histories=len(self.tracks),
            actual_observation_updates_per_second=sum(e[1] for e in recent) / span,
            frames_with_boxes_per_second=sum(e[2] for e in recent) / span,
            prediction_frame_updates_per_second=sum(e[3] for e in recent) / span,
            historical_frame_updates_per_second=sum(e[5] for e in recent) / span,
            max_visible_source_gap=max((e[6] for e in recent), default=0),
            max_visible_observation_age_ms=max((e[7] for e in recent), default=0.0),
            historical_box_updates_total=self.counts["historical_box_updates"],
            prediction_box_updates_total=self.counts["prediction_box_updates"],
            residual_reference="later model observation; not human ground truth",
            residual_samples=n,
            hold_residual_mean_px=(
                sum(r["raw_hold_px"] for r in self.residuals) / n if n else None
            ),
            predicted_residual_mean_px=(
                sum(r["predicted_px"] for r in self.residuals) / n if n else None
            ),
            source_period_assumed_seconds=1 / self.fps,
            max_prediction_frames=self.horizon,
            max_observation_interval_frames=self.observation_horizon,
            max_capture_age_seconds=self.max_age,
            historical_color="yellow",
            prediction_color="purple",
            current_observation_color="green_when_classified",
            counting_uses_display_boxes=False,
            sensor_exposure_timestamp_available=False,
            physical_scanout_measured=False,
        )
