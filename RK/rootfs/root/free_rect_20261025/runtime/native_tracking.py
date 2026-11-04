from collections import Counter, deque

import math

import os

import time

import numpy as np

import pose_consensus

import object_outcomes

import passage_ledger

import passage_pixels

import global_motion
import native_conveyor_match

VERSION = "global_conveyor_real_motion_local_v5_entry_identity"

COAST_SECONDS = 0.45

MAX_PREDICTION_FRAMES = 30

UNMATCHED_COST = 0.88

AMBIGUITY_MARGIN = 0.075

MIN_CONFIRMATIONS = 2


def _delta(new, old):

    value = (int(new) - int(old)) & 0xFFFFFFFF

    return value if value < 0x80000000 else value - 0x100000000


def _center(box):
    return ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)


def _iou(a, b):

    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )

    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])

    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    return inter / max(1e-9, area_a + area_b - inter)


def _matrix(objects, boxes):

    if not objects or not boxes:
        return np.empty((len(objects), len(boxes)), np.float64)

    if os.environ.get("VISION_RKNN_C_API") == "1":

        from native_postprocess import iou_matrix

        return iou_matrix([o["bbox"] for o in objects], boxes)

    a = np.asarray([o["bbox"] for o in objects], np.float64)

    b = np.asarray(boxes, np.float64)

    overlap = np.maximum(
        0,
        np.minimum(a[:, None, 2:], b[None, :, 2:])
        - np.maximum(a[:, None, :2], b[None, :, :2]),
    ).prod(axis=2)

    areas_a = np.maximum(0, a[:, 2:] - a[:, :2]).prod(axis=1)

    areas_b = np.maximum(0, b[:, 2:] - b[:, :2]).prod(axis=1)

    return overlap / np.maximum(1e-9, areas_a[:, None] + areas_b[None, :] - overlap)


def _appearance_matrix(objects, tracks, frame, gray=None):

    if frame is None:
        return [None] * len(objects), None

    if not objects:
        return [], np.empty((0, len(tracks)), np.float64) if tracks else None

    import cv2

    if "_real_pixel_features" in objects[0]:
        values = [obj["_real_pixel_features"]["appearance"] for obj in objects]
        descriptors = [
            (
                dict(
                    mean=value["mean"],
                    textured=value["std"] >= 6.0,
                    vector=np.frombuffer(value["vector"], dtype=np.float32, count=384),
                )
                if value
                else None
            )
            for value in values
        ]
    else:
        if gray is None:
            gray = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2GRAY)
        values = passage_pixels.descriptors(
            gray, [obj["bbox"] for obj in objects], (frame.x, frame.y), 0.12
        )
        descriptors = [
            (
                dict(
                    mean=value["mean"],
                    textured=value["std"] >= 6.0,
                    vector=value["vector"],
                )
                if value
                else None
            )
            for value in values
        ]

    if not tracks:
        return descriptors, None

    previous = [track.get("appearance") for track in tracks]

    a = (
        np.stack([d["vector"] if d else np.zeros(384, np.float32) for d in descriptors])
        if descriptors
        else np.empty((0, 384), np.float32)
    )

    b = np.stack([d["vector"] if d else np.zeros(384, np.float32) for d in previous])

    correlation = np.clip(a @ b.T, -1.0, 1.0)

    current_valid = np.fromiter(
        (value is not None for value in descriptors), bool, count=len(descriptors)
    )
    previous_valid = np.fromiter(
        (value is not None for value in previous), bool, count=len(previous)
    )
    valid = current_valid[:, None] & previous_valid[None, :]
    current_means = np.fromiter(
        (value["mean"] if value else 0.0 for value in descriptors),
        np.float64,
        count=len(descriptors),
    )
    previous_means = np.fromiter(
        (value["mean"] if value else 0.0 for value in previous),
        np.float64,
        count=len(previous),
    )
    penalties = 0.05 * np.abs(current_means[:, None] - previous_means[None, :]) / 255
    penalties[~valid] = 0.0
    current_textured = np.fromiter(
        (bool(value and value["textured"]) for value in descriptors),
        bool,
        count=len(descriptors),
    )
    previous_textured = np.fromiter(
        (bool(value and value["textured"]) for value in previous),
        bool,
        count=len(previous),
    )
    textured = valid & current_textured[:, None] & previous_textured[None, :]
    penalties[textured] += 0.22 * (1.0 - correlation.astype(np.float64)[textured]) * 0.5

    return descriptors, penalties


def _assignment(costs, unmatched=UNMATCHED_COST):

    n = len(costs)

    if not n:
        return []

    tracks = len(costs[0])
    m = tracks + n

    table = [row + [unmatched] * n for row in costs]

    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)

    for row in range(1, n + 1):

        p[0] = row
        j0 = 0
        minimum = [math.inf] * (m + 1)
        used = [False] * (m + 1)

        while True:

            used[j0] = True
            i0 = p[j0]
            change = math.inf
            j1 = 0

            for j in range(1, m + 1):

                if used[j]:
                    continue

                value = table[i0 - 1][j - 1] - u[i0] - v[j]

                if value < minimum[j]:
                    minimum[j] = value
                    way[j] = j0

                if minimum[j] < change:
                    change = minimum[j]
                    j1 = j

            for j in range(m + 1):

                if used[j]:
                    u[p[j]] += change
                    v[j] -= change

                else:
                    minimum[j] -= change

            j0 = j1

            if not p[j0]:
                break

        while True:

            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

            if not j0:
                break

    matched = [None] * n

    for col in range(1, m + 1):

        if p[col] and col <= tracks and costs[p[col] - 1][col - 1] < unmatched:

            matched[p[col] - 1] = col - 1

    return matched


def _predict(track, source):

    box = track.get("anchor_box", track["box"])

    anchor = track.get("anchor_source", track.get("source", source))

    gap = max(0, min(MAX_PREDICTION_FRAMES, _delta(source, anchor)))

    vx, vy = (
        track.get("velocity", (0.0, 0.0)) if track.get("velocity_ready") else (0.0, 0.0)
    )

    return [box[0] + vx * gap, box[1] + vy * gap, box[2] + vx * gap, box[3] + vy * gap]


def _association_margin(costs, matched, row, col):

    cost = costs[row][col]
    owners = {j: i for i, j in enumerate(matched) if j is not None}

    alternatives = [UNMATCHED_COST - cost]

    for j, value in enumerate(costs[row]):

        if j == col or value >= UNMATCHED_COST:
            continue

        owner = owners.get(j)

        if owner is None:
            alternatives.append(value - cost)

        else:
            alternatives.append(
                value + min(costs[owner][col], UNMATCHED_COST) - cost - costs[owner][j]
            )

    for i, j in enumerate(matched):

        if i != row and j is None and costs[i][col] < UNMATCHED_COST:

            alternatives.append(costs[i][col] - cost)

    return min(alternatives)


def _classified(obj, threshold):

    return bool(
        obj["confidence"] >= threshold
        and obj["label"] not in ("uncertain", "unreadable")
    )


def _pair_cost(
    obj, track, predicted, predicted_overlap, observed_overlap, now, threshold
):

    box = obj["bbox"]
    w, h = box[2] - box[0], box[3] - box[1]

    tw, th = predicted[2] - predicted[0], predicted[3] - predicted[1]

    if min(w, h, tw, th) <= 0:
        return 1e6

    ratio = (w * h) / (tw * th)

    if not 0.35 <= ratio <= 2.8:
        return 1e6

    norm = max(8.0, math.hypot((w + tw) * 0.5, (h + th) * 0.5))

    predicted_distance = math.dist(_center(box), _center(predicted)) / norm

    observed_distance = math.dist(_center(box), _center(track["box"])) / norm

    overlap = max(predicted_overlap, 0.95 * observed_overlap)

    distance = min(predicted_distance, observed_distance + 0.025)

    gap = max(
        1,
        _delta(
            obj.get("_source_frame_id", track.get("source", 0)),
            track.get("anchor_source", track.get("source", 0)),
        ),
    )

    uncertainty = float(track.get("motion_uncertainty", norm * 0.05))

    speed = math.hypot(*track.get("velocity", (0.0, 0.0)))

    adaptive_limit = (
        min(
            1.1,
            max(0.55, 0.35 + (2.5 * uncertainty + 0.25 * speed) * min(gap, 4) / norm),
        )
        if track.get("velocity_ready")
        else 0.55
    )

    if predicted_distance > adaptive_limit and observed_distance > 0.55:
        return 1e6

    if overlap < 0.1 and distance > 0.45:
        return 1e6

    age = max(0.0, now - track.get("seen", now))

    cost = (
        0.65 * (1 - overlap)
        + 0.35 * distance
        + 0.06 * abs(math.log(ratio))
        + min(0.16, age * 0.35)
    )

    return cost


def _class_prior(obj, track, threshold):

    stable = track.get("stable_label")

    return (
        0.16
        if stable and _classified(obj, threshold) and obj["label"] != stable
        else 0.0
    )


def _retire_legacy_aliases(tracks, stats, tracker=None, source=None, now=None):

    retained = {}

    signatures = {}

    for key, t in sorted(
        tracks.items(),
        key=lambda kv: (not kv[1].get("counted", False), -kv[1].get("hits", 0), kv[0]),
    ):

        if t.get("association_version") == VERSION:

            retained[key] = t
            continue

        signature = (t.get("source"), tuple(t["box"]))

        if signature in signatures:

            owner = retained[signatures[signature]]

            if tracker is not None and t.get("object_lifetime"):

                t["object_lifetime"]["legacy_alias_of_track_id"] = signatures[signature]

                object_outcomes.retire(
                    tracker, key, t, source, now, "legacy_duplicate_hypothesis"
                )

            owner["counted"] = bool(owner.get("counted") or t.get("counted"))

            owner["hits"] = max(owner.get("hits", 0), t.get("hits", 0))

            stats["legacy_identical_hypotheses_retired"] += 1

        else:
            retained[key] = t
            signatures[signature] = key

    return retained


def _new_track(obj, source, now, crowded):

    return dict(
        hits=0,
        stable_hits=0,
        box=list(obj["bbox"]),
        counted=False,
        seen=now,
        source=source,
        anchor_box=list(obj["bbox"]),
        anchor_source=source,
        velocity=(0.0, 0.0),
        velocity_ready=False,
        association_version=VERSION,
        pose_history_started_source=source,
        stable_label=None,
        label_streak=0,
        distinct_streak=0,
        crowded_birth=crowded,
    )


def _counter_init(self):

    passage_ledger.ensure(self)


def _passage_update(self, key, track, now):

    passage_ledger.emit(self, key, track, now)


def reset_counters(self):

    object_outcomes.interrupt(self, "explicit_counter_reset")

    self.counts.clear()
    self.observed_total = 0

    passage_ledger.reset(self)

    self.association_stats = Counter()
    self.passage_updates = deque(maxlen=256)
    self.passage_sequence = 0

    for track in self.tracks.values():

        track.update(
            counted=False, observed_registered=False, stable_hits=0, label_streak=0
        )

        for key in ("registered_at", "counted_label", "ledger_terminal_state"):
            track.pop(key, None)


def counters(self):

    result = passage_ledger.summary(self)

    result.update(
        pending_geometry_tracks=sum(
            not t.get("observed_registered", False) for t in self.tracks.values()
        ),
        association_diagnostics=dict(
            getattr(self, "association_stats", {}),
            version=VERSION,
            passage_flow_backend=getattr(self, "passage_flow_backend", None),
            passage_flow_error=getattr(self, "passage_flow_error", None),
            passage_flow_groups_backend=getattr(
                self, "passage_flow_groups_backend", None
            ),
            passage_flow_group_error=getattr(self, "passage_flow_group_error", None),
            patch_statistics_backend=passage_pixels.statistics_backend(),
            real_layout_scoring=passage_pixels.conveyor_layout.backend(),
            numeric_matching=native_conveyor_match.backend(),
            phase_timing=getattr(self, "conveyor_phase_timing", {}),
        ),
        global_motion=global_motion.summary(self),
        object_outcomes=object_outcomes.snapshot(self),
        last_image_rejections=list(getattr(self, "last_image_rejections", [])),
    )

    return result


def _update_motion(track, box, source):

    previous = track.get("anchor_box", track["box"])

    gap = _delta(source, track.get("anchor_source", track.get("source", source)))

    if gap > 0:

        old, new = _center(previous), _center(box)

        vx, vy = ((new[i] - old[i]) / gap for i in (0, 1))

        if math.isfinite(vx) and math.isfinite(vy):

            diagonal = max(8.0, math.hypot(box[2] - box[0], box[3] - box[1]))

            innovation = 0.0

            if track.get("velocity_ready"):

                old_v = track["velocity"]
                innovation = math.hypot(vx - old_v[0], vy - old_v[1])

                alpha = (
                    0.85
                    if innovation
                    > max(diagonal * 0.06, 2 * track.get("motion_uncertainty", 0.0))
                    else 0.5
                )

                vx = (1 - alpha) * old_v[0] + alpha * vx
                vy = (1 - alpha) * old_v[1] + alpha * vy

            track.update(
                velocity=(vx, vy),
                velocity_ready=True,
                motion_uncertainty=0.7
                * track.get("motion_uncertainty", diagonal * 0.04)
                + 0.3 * innovation,
            )

    track.update(anchor_box=list(box), anchor_source=source)


def _fast_global_assignment_ok(
    tracker,
    objects,
    source,
    motion,
    keys,
    predicted,
    matched,
    margins,
    appearance_costs,
    frame,
):

    if not motion.valid:
        return False, "global_model_not_tracking"

    if motion.residual_streak:
        return False, "recent_systematic_real_residual"

    previous = getattr(tracker, "conveyor_previous_source", None)

    if previous is None or not 0 < _delta(source, previous) <= 3:
        return False, "source_gap_requires_image_bootstrap"

    if not objects:
        return True, None

    matched_count = 0

    for index, obj in enumerate(objects):

        col = matched[index]
        box = obj["bbox"]

        if col is None:

            if frame is None:
                return False, "unmatched_real_observation"

            axis = motion.axis
            speed = motion.velocity[axis]

            lo = (frame.x, frame.y)[axis]
            extent = (frame.rgb.shape[1], frame.rgb.shape[0])[axis]

            center = _center(box)[axis]
            size = box[axis + 2] - box[axis]

            edge = (
                (center - lo) <= size * 0.7
                if speed >= 0
                else (lo + extent - center) <= size * 0.7
            )

            if not edge:
                return False, "unmatched_interior_real_observation"

            continue

        track = tracker.tracks[keys[col]]

        if (
            not track.get("identity_trusted_at_source")
            or track.get("stable_hits", 0) < 1
        ):

            return False, "association_identity_not_established"

        gap = _delta(
            source, track.get("state_anchor_source", track.get("source", source))
        )

        if not 0 < gap <= 3:
            return False, "identity_observation_gap"

        if margins.get(index, 0.0) < AMBIGUITY_MARGIN + 0.03:
            return False, "low_real_assignment_margin"

        if appearance_costs is not None and float(appearance_costs[index, col]) > 0.12:

            return False, "real_appearance_disagreement"

        residual = math.dist(_center(box), _center(predicted[col]))

        if residual > max(8.0, (box[2] - box[0]) * 0.18):
            return False, "large_real_observation_residual"

        matched_count += 1

    return True, None


def _bootstrap_scope(
    tracker,
    objects,
    source,
    motion,
    keys,
    predicted,
    matched,
    margins,
    appearance_costs,
    frame,
    threshold,
):

    before = getattr(tracker, "conveyor_previous_boxes", ())

    whole = (before, objects, "whole_frame")

    if not motion.valid or motion.residual_streak:
        return whole

    prior = getattr(tracker, "conveyor_previous_source", None)

    if prior is None or not 0 < _delta(source, prior) <= 3:
        return whole

    settled = []
    unsettled = []

    for index, obj in enumerate(objects):

        col = matched[index]

        if col is None:
            unsettled.append(obj)
            continue

        track = tracker.tracks[keys[col]]

        if (
            track.get("stable_hits", 0) < 3
            or len(track.get("trusted_real_history", ())) < 3
            or not _classified(obj, threshold)
            or track.get("observed_source") != prior
            or not track.get("identity_trusted_at_source")
        ):

            unsettled.append(obj)
            continue

        safe, _ = _fast_global_assignment_ok(
            tracker,
            [obj],
            source,
            motion,
            keys,
            predicted,
            [col],
            {0: margins.get(index, 0.0)},
            (
                appearance_costs[index : index + 1]
                if appearance_costs is not None
                else None
            ),
            frame,
        )

        if safe:
            settled.append(track)

        else:
            unsettled.append(obj)

    if len(settled) < 2 or len(unsettled) != 1:
        return whole

    excluded = {tuple(track["observed_box"]) for track in settled}

    if not excluded.issubset({tuple(box) for box in before}):
        return whole

    target = unsettled[0]
    index = next(i for i, obj in enumerate(objects) if obj is target)

    col = matched[index]

    if col is None:
        return whole

    track = tracker.tracks[keys[col]]

    if track.get("observed_source") != prior or not track.get(
        "identity_trusted_at_source"
    ):
        return whole

    real_before = track.get("observed_box")

    if real_before is None or tuple(real_before) not in {tuple(box) for box in before}:
        return whole

    if tuple(real_before) in excluded:
        return whole

    residual = math.dist(_center(target["bbox"]), _center(predicted[col]))

    if residual > max(12.0, (target["bbox"][2] - target["bbox"][0]) * 0.4):
        return whole

    return [real_before], unsettled, "local_single_identity"


def observe_tracks(self, objects, source, now, hits, threshold, frame=None):

    capture_time = (
        getattr(frame, "capture_received_monotonic", None)
        if frame is not None
        else None
    )

    if capture_time is not None:

        capture_time = float(capture_time)

        if (
            not math.isfinite(capture_time)
            or capture_time < 0
            or capture_time > now + 0.001
        ):

            raise ValueError("Invalid native observation timestamp")

        now = capture_time

    if self.last_source is not None and _delta(source, self.last_source) <= 0:
        return [], []

    total_started = time.thread_time()
    phase_started = total_started
    phase = {}

    motion = global_motion.ensure(self)

    motion.tick(source)

    self.last_source = source

    self.passage_optical_flow_enabled = False

    if not hasattr(self, "association_stats"):
        self.association_stats = Counter()

    _counter_init(self)

    while self.passage_updates and self.passage_updates[0][4] < now - 2.0:
        self.passage_updates.popleft()

    stats = self.association_stats
    stats["observed_source_frames"] += 1

    stats["model_observations"] += len(objects)

    gray = None
    pixels = {}
    rejected = []
    layout = None
    layout_diagnostics = {}
    direct_owners = {}

    descriptors_by_box = {}
    image_velocity = None

    if frame is not None:

        import cv2

        gray = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2GRAY)

        origin = (frame.x, frame.y)
        retained = []

        physical_support = (
            [obj["_real_pixel_features"]["support"] for obj in objects]
            if objects and "_real_pixel_features" in objects[0]
            else passage_pixels.supports(gray, [obj["bbox"] for obj in objects], origin)
        )

        for index, obj in enumerate(objects):

            physical = physical_support[index]

            split = passage_pixels.interstitial(objects, index)

            classified = _classified(obj, threshold)

            if (
                split
                or physical["reason"] == "insufficient_visible_pixels"
                or (not classified and not physical["accepted"])
            ):

                rejected.append(
                    dict(
                        bbox=list(obj["bbox"]),
                        confidence=obj["confidence"],
                        objectness=obj.get("objectness"),
                        raw_label=obj.get("raw_label", obj["label"]),
                        reason="interstitial_split" if split else physical["reason"],
                        image_support=physical,
                    )
                )

                continue

            retained.append(
                dict(
                    obj,
                    _physical_support=physical,
                    _retention_basis="model" if classified else "edges",
                )
            )

        objects = retained

        stats["image_rejected_observations"] += len(rejected)

        previous = getattr(self, "conveyor_previous_gray", None)

        prior = getattr(self, "conveyor_previous_source", None)

    self.last_image_rejections = rejected[:16]

    for key, track in list(self.tracks.items()):

        if (
            not 0
            <= now - track.get("last_real_seen", track.get("seen", now))
            < COAST_SECONDS
        ):

            object_outcomes.retire(self, key, track, source, now)

            del self.tracks[key]
            stats["retired_after_coast"] += 1

    self.tracks = _retire_legacy_aliases(self.tracks, stats, self, source, now)

    if not motion.axis_ready:

        real_centers = [
            _center(obj["bbox"]) for obj in objects if _classified(obj, threshold)
        ]

        learned_axis = None
        axis_source = None

        if len(real_centers) >= 2:

            spread = [
                max(point[axis] for point in real_centers)
                - min(point[axis] for point in real_centers)
                for axis in (0, 1)
            ]

            dominant = int(spread[1] > spread[0])

            if (
                spread[dominant] >= 8.0
                and spread[dominant] >= 2.0 * spread[1 - dominant]
            ):

                learned_axis = dominant
                axis_source = "independent_real_face_layout"

        if (
            learned_axis is None
            and motion.valid
            and math.hypot(*motion.velocity) >= 2.0
        ):

            learned_axis = int(abs(motion.velocity[1]) > abs(motion.velocity[0]))

            axis_source = "trusted_multi_identity_real_displacement"

        if learned_axis is not None:

            if learned_axis != motion.axis:

                for rank, key in enumerate(
                    sorted(
                        self.tracks,
                        key=lambda key: _center(
                            self.tracks[key].get(
                                "state_anchor_box", self.tracks[key]["box"]
                            )
                        )[learned_axis],
                    )
                ):

                    self.tracks[key]["conveyor_order"] = float(rank)

            motion.axis = learned_axis
            motion.axis_ready = True
            motion.axis_source = axis_source

            stats["rail_order_axis_learned"] += 1

    keys = sorted(self.tracks)

    phase["real_support_cpu_ms"] = (time.thread_time() - phase_started) * 1000

    phase_started = time.thread_time()

    prior_global_by_key = {}

    prior_global_valid = motion.valid
    prior_global_velocity = list(motion.velocity)

    for key in keys:

        anchor = self.tracks[key].get("state_anchor_box", self.tracks[key]["box"])

        interval = _delta(
            source,
            self.tracks[key].get(
                "state_anchor_source", self.tracks[key].get("source", source)
            ),
        )

        if prior_global_valid and 0 <= interval <= global_motion.MAX_HORIZON_FRAMES:

            prior_global_by_key[key] = [
                float(value + prior_global_velocity[i % 2] * interval)
                for i, value in enumerate(anchor)
            ]

    axis = motion.axis

    track_states = [self.tracks[key] for key in keys]
    descriptors, appearance_costs = _appearance_matrix(
        objects, track_states, frame, gray
    )

    phase["appearance_cpu_ms"] = (time.thread_time() - phase_started) * 1000

    def associate(image_velocity=None):

        predicted = []
        prediction_valid = []
        predicted_by_key = {}

        direction_velocity = image_velocity or (
            list(motion.velocity) if motion.valid else None
        )

        for key, track in zip(keys, track_states):

            box, usable = global_motion.predict(track, source, motion, image_velocity)

            predicted.append(box)
            prediction_valid.append(usable)
            predicted_by_key[key] = box

        if any("conveyor_order" not in self.tracks[key] for key in keys):

            for rank, key in enumerate(
                sorted(keys, key=lambda key: _center(predicted_by_key[key])[axis])
            ):

                self.tracks[key].setdefault("conveyor_order", float(rank))

        overlaps = _matrix(objects, predicted)

        columns = []
        for track, anchor, usable in zip(track_states, predicted, prediction_valid):
            interval = _delta(
                source, track.get("state_anchor_source", track.get("source", source))
            )
            real = track.get("state_anchor_box", track["box"])
            columns.append(
                (
                    interval,
                    anchor[2] - anchor[0],
                    anchor[3] - anchor[1],
                    _center(anchor),
                    1.15 if usable else 0.6,
                    track.get("counted"),
                    _center(real)[axis],
                    real[axis + 2] - real[axis],
                )
            )
        lower = (frame.x, frame.y)[axis] if frame is not None else 0.0
        span = (
            (frame.rgb.shape[1], frame.rgb.shape[0])[axis] if frame is not None else 0.0
        )

        measurement_costs = []
        observed_positions = []

        for i, obj in enumerate(objects):

            costs = []
            candidate = obj["bbox"]
            w, h = candidate[2] - candidate[0], candidate[3] - candidate[1]
            candidate_center = _center(candidate)
            observed_positions.append(candidate_center[axis])
            owner = direct_owners.get(tuple(candidate))
            candidate_extent = candidate[axis + 2] - candidate[axis]

            for j, key in enumerate(keys):

                (
                    interval,
                    tw,
                    th,
                    anchor_center,
                    gate,
                    counted,
                    real_center,
                    real_extent,
                ) = columns[j]

                if not 0 < interval <= global_motion.MAX_HORIZON_FRAMES:

                    costs.append(1e6)
                    continue

                ratio = (w * h) / max(1.0, tw * th)

                distance = math.dist(candidate_center, anchor_center) / max(
                    8.0, math.hypot((w + tw) * 0.5, (h + th) * 0.5)
                )

                appearance = (
                    float(appearance_costs[i, j])
                    if appearance_costs is not None
                    else 0.0
                )

                if owner is not None:
                    value = 0.0 if owner == key else 1e6

                elif (
                    not 0.35 <= ratio <= 2.8
                    or distance > gate
                    or (frame is not None and appearance > 0.2)
                ):
                    value = 1e6

                else:
                    value = (
                        0.56 * (1 - float(overlaps[i, j]))
                        + 0.34 * distance
                        + 0.06 * abs(math.log(ratio))
                        + appearance * 0.8
                    )

                if (
                    owner is None
                    and value < UNMATCHED_COST
                    and frame is not None
                    and counted
                    and direction_velocity is not None
                ):

                    movement = candidate_center[axis] - real_center

                    body = min(candidate_extent, real_extent)

                    entry = (
                        candidate[axis] <= lower + 0.5 * body
                        if direction_velocity[axis] > 0
                        else candidate[axis + 2] >= lower + span - 0.5 * body
                    )

                    if (
                        entry
                        and movement * direction_velocity[axis] < 0
                        and abs(movement) > 0.5 * body
                    ):

                        value = 1e6
                        stats["counted_reverse_pitch_links_rejected"] += 1

                costs.append(value)

            measurement_costs.append(costs)

        matched, margins = global_motion.ordered_assignment(
            measurement_costs,
            observed_positions,
            [self.tracks[key]["conveyor_order"] for key in keys],
            UNMATCHED_COST,
        )

        return (
            predicted,
            prediction_valid,
            predicted_by_key,
            measurement_costs,
            matched,
            margins,
        )

    phase["assignment_cpu_ms"] = 0.0
    assignment_done = False

    if motion.valid:

        phase_started = time.thread_time()

        (
            predicted,
            prediction_valid,
            predicted_by_key,
            measurement_costs,
            matched,
            margins,
        ) = associate()

        assignment_done = True

        phase["assignment_cpu_ms"] = (time.thread_time() - phase_started) * 1000

        fast, fast_reason = _fast_global_assignment_ok(
            self,
            objects,
            source,
            motion,
            keys,
            predicted,
            matched,
            margins,
            appearance_costs,
            frame,
        )

    else:
        fast, fast_reason = False, "global_model_not_tracking"

    phase["bootstrap_scope"] = "not_run"

    if (
        frame is not None
        and previous is not None
        and previous.shape == gray.shape
        and not fast
    ):

        previous_boxes = getattr(self, "conveyor_previous_boxes", ())

        if not objects or not previous_boxes:

            phase["bootstrap_scope"] = "unavailable_no_real_pair"

            stats["image_bootstrap_empty_pairs_skipped"] += 1

        else:

            phase_started = time.thread_time()

            before, after, scope = _bootstrap_scope(
                self,
                objects,
                source,
                motion,
                keys,
                predicted if assignment_done else [],
                matched if assignment_done else [],
                margins if assignment_done else {},
                appearance_costs,
                frame,
                threshold,
            )

            phase["bootstrap_scope"] = scope

            descriptors_by_box = dict(
                zip(
                    (tuple(obj["bbox"]) for obj in after),
                    passage_pixels.descriptors(
                        gray, [obj["bbox"] for obj in after], origin
                    ),
                )
            )

            layout = passage_pixels.observed_face_motion(
                previous,
                gray,
                before,
                after,
                origin,
                getattr(self, "conveyor_previous_descriptors", None),
                descriptors_by_box,
                layout_diagnostics,
            )

            if layout is not None:

                interval = _delta(source, prior)

                if (
                    scope == "whole_frame"
                    and 0 < interval <= global_motion.MAX_HORIZON_FRAMES
                ):

                    measured_velocity = [value / interval for value in layout["shift"]]

                    expected = [value * interval for value in motion.velocity]

                    widths = [obj["bbox"][2] - obj["bbox"][0] for obj in objects]

                    boundary = (
                        max(10.0, float(np.median(widths)) * 0.3) if widths else 10.0
                    )

                    if (
                        not motion.valid
                        or math.dist(layout["shift"], expected) > boundary
                    ):

                        image_velocity = measured_velocity

                        stats["independent_image_bootstrap_or_speed_change_priors"] += 1

                for match in layout.pop("_matches"):

                    if match["similarity"] < 0.7:
                        continue

                    owners = [
                        key
                        for key, track in self.tracks.items()
                        if track.get("observed_source") == prior
                        and track.get("identity_trusted_at_source")
                        and list(track.get("observed_box", ())) == match["previous_box"]
                    ]

                    if len(owners) == 1:
                        direct_owners[tuple(match["current_box"])] = owners[0]

            phase["image_bootstrap_cpu_ms"] = (
                time.thread_time() - phase_started
            ) * 1000

            stats["image_bootstrap_frames"] += 1

            stats["image_bootstrap_" + scope + "_frames"] += 1

            stats["image_bootstrap_reason_" + str(fast_reason)] += 1

    elif fast:

        stats["trusted_global_fast_frames"] += 1

        stats["trusted_global_fast_real_object_frames"] += bool(objects)

    if not assignment_done or layout is not None:

        phase_started = time.thread_time()

        (
            predicted,
            prediction_valid,
            predicted_by_key,
            measurement_costs,
            matched,
            margins,
        ) = associate(image_velocity)

        phase["assignment_cpu_ms"] += (time.thread_time() - phase_started) * 1000

    phase["fast_path"] = fast
    phase["fallback_reason"] = None if fast else fast_reason

    costs = measurement_costs

    phase_started = time.thread_time()

    owners = {col: i for i, col in enumerate(matched) if col is not None}

    proposals = []
    cohort_residuals = []

    rows = []
    failures = []

    required = max(MIN_CONFIRMATIONS, int(hits))

    for index, obj in enumerate(objects):

        col = matched[index]
        ambiguous = False
        margin = math.inf
        cost = None
        crowded = False
        predecessors = []

        if col is not None:

            key = keys[col]
            track = self.tracks[key]
            cost = costs[index][col]

            margin = margins[index]

            ambiguous = margin < AMBIGUITY_MARGIN

        else:

            crowded = any(
                _iou(obj["bbox"], other["bbox"]) > 0.45
                for i, other in enumerate(objects)
                if i != index
            )

            self.next_id += 1
            key = self.next_id

            track = _new_track(obj, source, now, crowded)
            self.tracks[key] = track

            track["conveyor_order"] = global_motion.assign_order(
                self, key, obj["bbox"], predicted_by_key, axis
            )

            stats["new_tracks"] += 1

            stats["pose_history_started_new_identity"] += 1

            ambiguous = any(
                value < UNMATCHED_COST
                and (
                    j not in owners
                    or margins.get(owners[j], 0.0) < AMBIGUITY_MARGIN
                    or value - measurement_costs[owners[j]][j] < AMBIGUITY_MARGIN
                )
                for j, value in enumerate(measurement_costs[index])
            )

            for j, old_key in enumerate(keys):

                old = self.tracks[old_key]

                if j in owners and margins.get(owners[j], 0.0) >= AMBIGUITY_MARGIN:
                    continue

                gap = _delta(source, old.get("source", source))

                norm = max(
                    8.0,
                    math.hypot(
                        obj["bbox"][2] - obj["bbox"][0], obj["bbox"][3] - obj["bbox"][1]
                    ),
                )

                if (
                    not 0 < gap <= 6
                    or math.dist(_center(obj["bbox"]), _center(old["box"])) > norm * 1.2
                ):
                    continue

                if (
                    appearance_costs is not None
                    and float(appearance_costs[index, j]) > 0.12
                ):
                    continue

                old_life = old.get("object_lifetime")

                if old_life:
                    predecessors.append(old_life["object_id"])

            if predecessors:
                stats["new_tracks_with_predecessor_candidates"] += 1

        layout_ambiguous = (
            tuple(obj["bbox"])
            in {tuple(box) for box in layout_diagnostics.get("ambiguous_boxes", ())}
            and not motion.valid
        )

        if layout_ambiguous:

            ambiguous = True
            stats["competing_image_layout_observations"] += 1

        classified = _classified(obj, threshold)

        track["real_detector_observations"] = (
            track.get("real_detector_observations", 0) + 1
        )

        if ambiguous:

            track["stable_hits"] = 0
            stats["ambiguous_observations"] += 1

        else:

            _update_motion(track, obj["bbox"], source)

            if descriptors[index] is not None:
                track["appearance"] = descriptors[index]

            if frame is not None:

                track.update(
                    pixel_box=list(obj["bbox"]), pixel_source=source, pixel_seen=now
                )

            track["stable_hits"] = track.get("stable_hits", 0) + 1

        track.update(hits=track.get("hits", 0) + 1, association_version=VERSION)

        if not ambiguous:
            track.update(box=list(obj["bbox"]), seen=now, source=source)

        other_close = any(
            _iou(obj["bbox"], other["bbox"]) > 0.45
            for i, other in enumerate(objects)
            if i != index
        )

        track["distinct_streak"] = (
            0 if other_close else track.get("distinct_streak", 0) + 1
        )

        if classified and not ambiguous:

            label = obj["label"]

            if track.get("candidate_label") == label:
                track["label_streak"] = track.get("label_streak", 0) + 1

            else:
                track["candidate_label"] = label
                track["label_streak"] = 1

        elif not classified:
            track["label_streak"] = 0

        appearance_penalty = (
            float(appearance_costs[index, col])
            if appearance_costs is not None and col is not None
            else None
        )

        previous_pose_diagnostics = dict(track.get("pose_diagnostics", {}))

        temporal = pose_consensus.update(
            track, obj, source, threshold, not ambiguous, appearance_penalty
        )

        for name, value in track.get("pose_diagnostics", {}).items():

            stats["pose_" + name] += value - previous_pose_diagnostics.get(name, 0)

        track["stable_label"] = temporal["label"] if temporal["accepted"] else None

        if (
            col is not None
            and not ambiguous
            and classified
            and key in prior_global_by_key
        ):

            cohort_residuals.append(
                dict(
                    track_id=key,
                    residual=[
                        _center(obj["bbox"])[a] - _center(prior_global_by_key[key])[a]
                        for a in (0, 1)
                    ],
                )
            )

        proposal = global_motion.record_real(
            key, track, source, obj["bbox"], now, classified, not ambiguous
        )

        if proposal is not None:
            proposals.append(proposal)

        class_ready = temporal["accepted"] and (
            track.get("counted") or (classified and obj["label"] == temporal["label"])
        )

        confirmed = track["stable_hits"] >= required and not ambiguous

        distinct = (
            not track.get("crowded_birth")
            or track.get("distinct_streak", 0) >= required
        )

        eligible = (
            confirmed
            and class_ready
            and distinct
            and track["real_detector_observations"] >= required
        )

        current_failure = (
            ambiguous
            or not classified
            or temporal["single_frame_label_disagrees"]
            or (track.get("observed_registered") and not class_ready)
            or not distinct
        )

        if current_failure:

            previous_failure = track.get("failure_last_source")

            consecutive = (
                previous_failure is not None
                and 0 < _delta(source, previous_failure) <= 3
            )

            track["failure_observations"] = (
                track.get("failure_observations", 0) + 1 if consecutive else 1
            )

            if not consecutive:
                track["failure_first_source"] = source

            track["failure_last_source"] = source

        else:

            track["failure_observations"] = 0
            track.pop("failure_last_source", None)
            track.pop("failure_first_source", None)

        passage_changed = False

        if confirmed and distinct and not track.get("observed_registered"):

            self.observed_total += 1
            track["observed_registered"] = True
            stats["registered_observed_tracks"] += 1

            track["registered_at"] = float(now)
            passage_changed = True

        if eligible and not track.get("counted"):

            trusted_label = temporal["label"]

            self.counts[trusted_label] = self.counts.get(trusted_label, 0) + 1

            track.update(counted=True, counted_label=trusted_label)
            stats["counted_tracks"] += 1

            if "registered_at" in track:
                passage_changed = True

        if passage_changed:
            _passage_update(self, key, track, now)

        status = (
            "association_uncertain"
            if ambiguous
            else (
                "confirming"
                if not confirmed
                else (
                    "classification_pending"
                    if not class_ready
                    else "crowded_pending" if not distinct else "confirmed"
                )
            )
        )

        object_id = object_outcomes.observe(
            self,
            key,
            track,
            source,
            now,
            obj["bbox"],
            classified,
            ambiguous,
            appearance_penalty,
            temporal,
            frame=frame,
            predecessors=predecessors,
        )

        row = dict(
            track_id=key,
            box=list(obj["bbox"]),
            raw_box=list(obj["bbox"]),
            observed_box=list(obj["bbox"]),
            observed_source_frame_id=int(source),
            roi_frame_id=source,
            object_id=object_id,
            tracking_hits=track["hits"],
            confirmed=confirmed,
            association_uncertain=ambiguous,
            classification=dict(
                label=obj["label"],
                raw_label=obj.get("raw_label", obj["label"]),
                confidence=obj["confidence"],
                accepted=classified,
            ),
            classification_temporal=dict(temporal, used_for_counting=bool(eligible)),
            tracking=dict(
                status=status,
                association_uncertain=ambiguous,
                counting_eligible=eligible,
                image_layout_ambiguous=layout_ambiguous,
                association_version=VERSION,
                stable_hits=track["stable_hits"],
                required_hits=required,
                classification_consistent=class_ready,
                association_cost=cost,
                association_margin=margin if math.isfinite(margin) else None,
                appearance_penalty=appearance_penalty,
                candidate_predecessor_object_ids=predecessors,
                pose_history_reset_reason=temporal.get("history_reset_reason"),
                measured_motion_px_per_source_frame=list(
                    track.get("velocity", (0.0, 0.0))
                ),
                motion_uncertainty_px=track.get("motion_uncertainty"),
                failure_observations=track.get("failure_observations", 0),
                failure_source_span=(
                    max(
                        0, _delta(source, track.get("failure_first_source", source)) + 1
                    )
                    if current_failure
                    else 0
                ),
                observed_registered=bool(track.get("observed_registered")),
                counted=bool(track.get("counted")),
                image_support=obj.get("_physical_support"),
                pixel_identity_verified=direct_owners.get(tuple(obj["bbox"])) == key,
                real_detection_accepted=True,
                retention_basis=obj.get("_retention_basis", "model"),
                pixel_measurement=None,
                optical_flow_enabled=False,
                predicted_state=(
                    dict(
                        box=list(predicted[col]),
                        source_frame_id=int(source),
                        is_real_measurement=False,
                    )
                    if col is not None and prediction_valid[col]
                    else None
                ),
                cumulative_allocation=(
                    "confirmed"
                    if track.get("counted")
                    else (
                        "pending"
                        if track.get("observed_registered")
                        else "unresolved_geometry"
                    )
                ),
            ),
            cached_fine=dict(label=obj["label"], score=obj["confidence"], age_ms=0),
        )

        rows.append(row)

        if ambiguous or not classified:

            failures.append(
                dict(
                    row,
                    reason=(
                        "association_uncertain"
                        if ambiguous
                        else "low_confidence_or_unreadable"
                    ),
                )
            )

    visible = {r["track_id"] for r in rows}

    provisional = [
        (t.get("seen", now), key)
        for key, t in self.tracks.items()
        if not t.get("observed_registered")
    ]

    limit = max(16, 4 * len(objects))

    excess = max(0, len(provisional) - limit)

    for _, key in sorted(provisional):

        if not excess:
            break

        if key in visible:
            continue

        object_outcomes.retire(
            self, key, self.tracks[key], source, now, "provisional_capacity_eviction"
        )

        del self.tracks[key]
        excess -= 1
        stats["retired_unconfirmed_overflow"] += 1

    stats["active_tracks"] = len(self.tracks)

    stats["maximum_active_tracks"] = max(
        stats.get("maximum_active_tracks", 0), len(self.tracks)
    )

    stats["last_frame_ambiguous"] = sum(r["association_uncertain"] for r in rows)

    stats["last_frame_observations"] = len(rows)

    motion.update(source, proposals, cohort_residuals)

    for row in rows:
        row["display_motion_v1"] = motion.snapshot(source)

    if frame is not None:

        self.conveyor_previous_gray = gray
        self.conveyor_previous_source = int(source)

        self.conveyor_previous_boxes = [list(obj["bbox"]) for obj in objects]

        self.conveyor_previous_descriptors = {
            tuple(obj["bbox"]): descriptors_by_box.get(tuple(obj["bbox"]))
            for obj in objects
        }

    phase["real_evidence_and_outcomes_cpu_ms"] = (
        time.thread_time() - phase_started
    ) * 1000

    phase["total_cpu_ms"] = (time.thread_time() - total_started) * 1000

    self.conveyor_phase_timing = phase

    return rows, failures
