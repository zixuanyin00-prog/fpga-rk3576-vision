from collections import Counter, deque
import math


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


class DisplayAlignment:
    def __init__(
        self,
        fps=60.0,
        max_prediction_frames=6,
        max_capture_age_s=0.15,
        max_speed_px_per_frame=60.0,
        enabled=True,
    ):
        self.fps = float(fps)
        self.horizon = int(max_prediction_frames)
        self.max_age = float(max_capture_age_s)
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

        if t["ready"] and delta <= self.horizon:
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
            delta > self.horizon or speed > self.max_speed or reversal or jump
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
            elif (
                row.get("sensor_transition")
                or row.get("association_uncertain")
                or row.get("association_ambiguous")
            ):
                reason = "transition_or_ambiguous"
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
            t = self._observe(row, int(source), box, now, key)
            if t is None:
                continue
            observations.add((key[0], int(source)))
            velocity = t["velocity"] if t["ready"] and self.enabled else (0.0, 0.0)
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
                display_prediction=bool(dx or dy),
                prediction_frames=gap,
                prediction_ms_estimate=1000 * gap / self.fps,
                display_alignment_reason=(
                    "bounded_velocity" if dx or dy else "observed_hold"
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
            )
        )
        self.counts["display_fresh_frames"] += 1
        self.counts["prediction_box_updates"] += sum(
            r["display_prediction"] for r in rows
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
            sensor_exposure_timestamp_available=False,
            physical_scanout_measured=False,
        )
