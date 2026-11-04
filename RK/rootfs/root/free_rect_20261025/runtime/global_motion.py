from collections import Counter, deque

import math
import native_conveyor_match

from statistics import median

SOURCE_FPS = 60.0

MAX_HORIZON_FRAMES = 12

EVIDENCE_MAX_AGE = 30

LEARNING_WINDOW_FRAMES = 300


def delta(new, old):

    value = (int(new) - int(old)) & 0xFFFFFFFF

    return value if value < 0x80000000 else value - 0x100000000


def center(box):
    return ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)


def norm(v):
    return math.hypot(*v)


class Model:

    def __init__(self, revision=0):

        self.state = "learning"
        self.valid = False
        self.velocity = [0.0, 0.0]

        self.revision = int(revision)
        self.last_evidence_source = None

        self.pending = None
        self.pending_count = 0
        self.pending_ids = set()

        self.stats = Counter()
        self.last_cohort = {}
        self.axis = 0

        self.axis_ready = False
        self.axis_source = "unlearned"

        self.residual_streak = 0
        self.residual_direction = None
        self.residual_source = None

        self.recent_real_proposals = {}

    def _state(self, state, valid):

        if self.state != state or self.valid != valid:

            self.state = state
            self.valid = valid
            self.revision += 1

    def tick(self, source):

        if (
            self.last_evidence_source is not None
            and delta(source, self.last_evidence_source) > EVIDENCE_MAX_AGE
        ):

            self._state(
                "relearning" if self.velocity != [0.0, 0.0] else "learning", False
            )

            self.pending = None
            self.pending_count = 0
            self.pending_ids.clear()

    def matching_velocity(self, source):

        self.tick(source)

        if self.valid:
            return list(self.velocity)

        if (
            self.pending is not None
            and self.last_evidence_source is not None
            and delta(source, self.last_evidence_source) <= MAX_HORIZON_FRAMES
        ):

            return list(self.pending)

        return None

    def update(self, source, proposals, residuals=()):

        self.tick(source)

        current = {
            int(p["track_id"]): p
            for p in proposals
            if p.get("evidence_source") == "trusted_real_detections"
        }

        if not current:
            return

        for key, p in current.items():
            self.recent_real_proposals[key] = dict(p, cohort_source=int(source))

        self.recent_real_proposals = {
            key: p
            for key, p in self.recent_real_proposals.items()
            if 0 <= delta(source, p["cohort_source"]) <= LEARNING_WINDOW_FRAMES
        }

        if len(self.recent_real_proposals) > 64:

            keep = sorted(
                self.recent_real_proposals,
                key=lambda key: delta(
                    source, self.recent_real_proposals[key]["cohort_source"]
                ),
            )[:64]

            self.recent_real_proposals = {
                key: self.recent_real_proposals[key] for key in keep
            }

        current_values = [
            p
            for p in current.values()
            if all(math.isfinite(float(v)) for v in p["velocity"])
        ]

        if len(current_values) >= 2:

            values = current_values

        else:

            values = sorted(
                (
                    p
                    for p in self.recent_real_proposals.values()
                    if all(math.isfinite(float(v)) for v in p["velocity"])
                ),
                key=lambda p: delta(source, p["cohort_source"]),
            )[:2]

        if len(values) < 2:

            self.stats["single_identity_cohorts_ignored"] += bool(values)

            return

        velocity = [median([p["velocity"][axis] for p in values]) for axis in (0, 1)]

        speed = norm(velocity)

        if len(values) == 2 and math.dist(
            values[0]["velocity"], values[1]["velocity"]
        ) > max(3.0, speed * 0.2):

            self.stats["incoherent_two_identity_cohorts_rejected"] += 1
            return

        errors = [math.dist(p["velocity"], velocity) for p in values]

        mad = median(errors)

        tolerance = min(max(2.0, mad * 3.0), max(3.0, speed * 0.25))

        inliers = [p for p, e in zip(values, errors) if e <= tolerance]

        if len(inliers) < max(2, math.ceil(len(values) * 0.6)):

            self.stats["incoherent_cohorts_rejected"] += 1
            return

        if not any(p["track_id"] in current for p in inliers):

            self.stats["cohorts_without_current_real_inlier_rejected"] += 1
            return

        velocity = [
            float(median([p["velocity"][axis] for p in inliers])) for axis in (0, 1)
        ]

        change = [velocity[i] - self.velocity[i] for i in (0, 1)]

        if self.valid and norm(change) > 0.05:

            supporting = [
                p
                for p in inliers
                if norm([p["velocity"][i] - self.velocity[i] for i in (0, 1)]) > 0.1
                and sum(
                    (p["velocity"][i] - self.velocity[i]) * change[i] for i in (0, 1)
                )
                > 0
            ]

            if len(supporting) < 2:

                self.stats["single_identity_model_change_rejected"] += 1
                return

        self.stats["outlier_identity_displacements_rejected"] += len(values) - len(
            inliers
        )

        self.stats["trusted_real_cohorts"] += 1

        self.last_evidence_source = int(source)

        residual_by_id = {int(item["track_id"]): item["residual"] for item in residuals}

        measured_residuals = [
            residual_by_id[p["track_id"]]
            for p in inliers
            if p["track_id"] in residual_by_id
        ]

        self.last_cohort = dict(
            source_frame_id=int(source),
            independent_ids=[p["track_id"] for p in inliers],
            velocity=list(velocity),
            rejected_outliers=len(values) - len(inliers),
            residual_pixels=(
                [
                    float(median([r[axis] for r in measured_residuals]))
                    for axis in (0, 1)
                ]
                if measured_residuals
                else None
            ),
            evidence_source="trusted_real_detections",
        )

        systematic = False

        if len(measured_residuals) >= 2:

            residual = [
                float(median([r[axis] for r in measured_residuals])) for axis in (0, 1)
            ]

            magnitude = norm(residual)

            agreeing = [
                r
                for r in measured_residuals
                if norm(r) >= 6.0
                and magnitude >= 6.0
                and sum(r[i] * residual[i] for i in (0, 1)) >= 0.7 * norm(r) * magnitude
            ]

            systematic = len(agreeing) >= 2 and len(agreeing) >= math.ceil(
                len(measured_residuals) * 0.7
            )

            if systematic:

                direction = [r / magnitude for r in residual]

                continuous = (
                    self.residual_source is not None
                    and 0 < delta(source, self.residual_source) <= MAX_HORIZON_FRAMES
                    and self.residual_direction is not None
                    and sum(direction[i] * self.residual_direction[i] for i in (0, 1))
                    >= 0.8
                )

                self.residual_streak = self.residual_streak + 1 if continuous else 1

                self.residual_direction = direction
                self.residual_source = int(source)

                self.last_cohort.update(
                    systematic_residual_ids=len(agreeing),
                    systematic_residual_streak=self.residual_streak,
                )

                if self.residual_streak >= 2 and self.valid:

                    self._state("relearning", False)

                    self.pending = None
                    self.pending_count = 0
                    self.pending_ids.clear()

                    self.stats["persistent_multi_identity_residual_relearning"] += 1

                    self.last_cohort["relearning_reason"] = (
                        "persistent_same_direction_real_cohort_residual"
                    )

        if not systematic:
            self.residual_streak = 0
            self.residual_direction = None

        substantial = not self.valid or math.dist(velocity, self.velocity) > max(
            2.0, norm(self.velocity) * 0.15
        )

        if substantial:

            if self.valid:
                self._state("relearning", False)

            compatible = self.pending is not None and math.dist(
                velocity, self.pending
            ) <= max(2.0, norm(velocity) * 0.15)

            if compatible:

                self.pending = [(self.pending[i] + velocity[i]) * 0.5 for i in (0, 1)]

                self.pending_count += 1
                self.pending_ids.update(p["track_id"] for p in inliers)

            else:

                self.pending = list(velocity)
                self.pending_count = 1
                self.pending_ids = {p["track_id"] for p in inliers}

            if self.pending_count >= 2 and len(self.pending_ids) >= 2:

                self.velocity = list(self.pending)

                self.pending = None
                self.pending_count = 0
                self.pending_ids.clear()

                self._state("tracking", True)
                self.revision += 1

                self.stats["cohort_model_acquisitions"] += 1

            return

        corrected = [self.velocity[i] * 0.75 + velocity[i] * 0.25 for i in (0, 1)]

        if math.dist(corrected, self.velocity) > 0.02:

            self.velocity = corrected
            self.revision += 1
            self.stats["bounded_real_measurement_corrections"] += 1

    def snapshot(self, source):

        self.tick(source)

        return dict(
            valid=bool(self.valid),
            state=self.state,
            anchor_source_frame_id=int(source),
            velocity_px_per_source_frame=[float(v) for v in self.velocity],
            source_fps=SOURCE_FPS,
            revision=self.revision,
            max_horizon_frames=MAX_HORIZON_FRAMES,
            evidence_source="trusted_real_detections",
        )


def ensure(tracker):

    model = getattr(tracker, "conveyor_motion", None)

    epoch = str(getattr(tracker, "region_version", ""))

    if (
        model is None
        or getattr(tracker, "conveyor_motion_epoch", None) != epoch
        or (
            getattr(tracker, "last_source", None) is None
            and model.last_evidence_source is not None
        )
    ):

        model = Model(0 if model is None else model.revision + 1)

        tracker.conveyor_motion = model
        tracker.conveyor_motion_epoch = epoch

        tracker.conveyor_previous_gray = None
        tracker.conveyor_previous_boxes = []

        tracker.conveyor_previous_descriptors = {}
        tracker.conveyor_previous_source = None

    return model


def provisional_velocity(track):

    history = list(track.get("trusted_real_history", ()))

    if len(history) < 2:
        return None

    slopes = []

    for before, after in zip(history, history[1:]):

        gap = delta(after[0], before[0])

        if 0 < gap <= MAX_HORIZON_FRAMES:

            slopes.append([(after[1][axis] - before[1][axis]) / gap for axis in (0, 1)])

    return (
        [float(median([v[axis] for v in slopes])) for axis in (0, 1)]
        if slopes
        else None
    )


def predict(track, source, model, image_velocity=None):

    box = list(track.get("state_anchor_box", track["box"]))

    anchor = track.get("state_anchor_source", track.get("source", source))

    gap = delta(source, anchor)

    if not 0 <= gap <= MAX_HORIZON_FRAMES:
        return box, False

    velocity = (
        image_velocity or model.matching_velocity(source) or provisional_velocity(track)
    )

    if velocity is None:
        return box, False

    return [
        float(value + velocity[index % 2] * gap) for index, value in enumerate(box)
    ], True


def record_real(key, track, source, box, now, reliable, identity_trusted):

    track["observed_box"] = list(box)
    track["observed_source"] = int(source)

    track["identity_trusted_at_source"] = bool(identity_trusted)

    if not identity_trusted:
        return None

    track.update(
        state_anchor_box=list(box),
        state_anchor_source=int(source),
        last_real_seen=float(now),
    )

    if not reliable:
        return None

    history = track.setdefault("trusted_real_history", deque(maxlen=6))

    if history and delta(source, history[-1][0]) <= 0:
        return None

    current = center(box)
    previous = history[-1] if history else None

    history.append((int(source), [float(v) for v in current]))

    if len(history) < 3 or track.get("stable_hits", 0) < 2:
        return None

    recent = list(history)[-3:]
    slopes = []

    for index, before in enumerate(recent):

        for after in recent[index + 1 :]:

            gap = delta(after[0], before[0])

            if 0 < gap <= MAX_HORIZON_FRAMES:

                slopes.append(
                    [(after[1][axis] - before[1][axis]) / gap for axis in (0, 1)]
                )

    if len(slopes) < 3:
        return None

    return dict(
        track_id=int(key),
        source_frame_id=int(source),
        previous_source_frame_id=previous[0],
        velocity=[float(median([v[axis] for v in slopes])) for axis in (0, 1)],
        reliable_real_frames=len(history),
        slope_source_frames=[item[0] for item in recent],
        slope_method="three_real_source_theil_sen",
        evidence_source="trusted_real_detections",
    )


def ordered_assignment(costs, observed_positions, track_orders, unmatched=0.88):
    return native_conveyor_match.ordered_assignment(
        costs, observed_positions, track_orders, unmatched
    )


def assign_order(tracker, key, box, predicted_by_key, axis):

    value = center(box)[axis]
    neighbours = []

    for other, t in tracker.tracks.items():

        if other == key or "conveyor_order" not in t:
            continue

        position = center(
            predicted_by_key.get(other, t.get("state_anchor_box", t["box"]))
        )[axis]

        neighbours.append((position, t["conveyor_order"]))

    left = [rank for position, rank in neighbours if position < value]

    right = [rank for position, rank in neighbours if position >= value]

    if left and right and max(left) < min(right):
        return (max(left) + min(right)) * 0.5

    if left:
        return max(left) + 1.0

    if right:
        return min(right) - 1.0

    return 0.0


def summary(tracker):

    model = getattr(tracker, "conveyor_motion", None)

    return dict(
        enabled=True,
        optical_flow_enabled=False,
        source_fps=SOURCE_FPS,
        rail_order_axis=model.axis if model else 0,
        rail_order_axis_ready=bool(model and model.axis_ready),
        rail_order_axis_source=model.axis_source if model else "unlearned",
        state=model.state if model else "learning",
        valid=bool(model and model.valid),
        velocity_px_per_source_frame=list(model.velocity) if model else [0.0, 0.0],
        revision=model.revision if model else 0,
        stats=dict(model.stats) if model else {},
        latest_real_cohort=model.last_cohort if model else {},
        prediction_is_not_measurement=True,
    )
