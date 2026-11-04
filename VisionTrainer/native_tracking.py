from collections import Counter, deque
import math
import os
import numpy as np
import pose_consensus
import object_outcomes
import passage_ledger
import passage_pixels

VERSION = "pixel_supported_passage_v5"
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
    import cv2

    if gray is None:
        gray = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    descriptors = []
    for obj in objects:
        l, t, r, b = obj["bbox"]
        mx = (r - l) * 0.12
        my = (b - t) * 0.12
        left = max(0, int(l - frame.x + mx))
        right = min(width, int(r - frame.x - mx))
        top = max(0, int(t - frame.y + my))
        bottom = min(height, int(b - frame.y - my))
        if right - left < 5 or bottom - top < 5:
            descriptors.append(None)
            continue
        patch = (
            cv2.resize(
                gray[top:bottom, left:right], (24, 16), interpolation=cv2.INTER_AREA
            )
            .astype(np.float32)
            .ravel()
        )
        mean = float(patch.mean())
        centered = patch - mean
        norm = float(np.linalg.norm(centered))
        textured = float(patch.std()) >= 6.0
        descriptors.append(
            dict(mean=mean, textured=textured, vector=centered / max(norm, 1e-6))
        )
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
    penalties = np.zeros((len(objects), len(tracks)), np.float64)
    for i, current in enumerate(descriptors):
        if current is None:
            continue
        for j, old in enumerate(previous):
            if old is None:
                continue
            penalties[i, j] = 0.05 * abs(current["mean"] - old["mean"]) / 255
            if current["textured"] and old["textured"]:
                penalties[i, j] += 0.22 * (1 - float(correlation[i, j])) * 0.5
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
            getattr(self, "association_stats", {}), version=VERSION
        ),
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


def observe_tracks(self, objects, source, now, hits, threshold, frame=None):

    if self.last_source is not None and _delta(source, self.last_source) <= 0:
        return [], []
    self.last_source = source
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
    if frame is not None:
        gray, pixels = passage_pixels.measure(self, frame, source)
        for key, measurement in pixels.items():
            track = self.tracks[key]
            track.update(
                pixel_box=measurement["box"],
                pixel_source=source,
                pixel_seen=now,
                pixel_measurement=measurement,
            )
            life = track.get("object_lifetime")
            if life is not None:
                life.update(
                    last_pixel_box=measurement["box"],
                    last_pixel_step=measurement["shift"],
                    last_pixel_source_frame_id=source,
                    pixel_identity_trusted=True,
                )
        support_boxes = [
            measurement["box"]
            for key, measurement in pixels.items()
            if self.tracks[key].get("observed_registered")
        ]
        collective = getattr(self, "passage_collective_measurement", None)
        prior = getattr(self, "passage_gray_source", None)
        if collective is not None:
            support_boxes.extend(
                [
                    float(value + collective[i % 2])
                    for i, value in enumerate(track["box"])
                ]
                for key, track in self.tracks.items()
                if track.get("observed_registered")
                and track.get("source") == prior
                and key not in pixels
            )
        retained = []
        for index, obj in enumerate(objects):
            physical = passage_pixels.face_support(
                gray, obj["bbox"], (frame.x, frame.y)
            )
            split = passage_pixels.interstitial(objects, index, support_boxes)
            if not physical["accepted"] or split:
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
            retained.append(dict(obj, _physical_support=physical))
        objects = retained
        stats["image_rejected_observations"] += len(rejected)
        stats["pixel_verified_track_frames"] += len(pixels)
    self.last_image_rejections = rejected[:16]
    for key, track in list(self.tracks.items()):
        if (
            not 0
            <= now - max(track.get("seen", now), track.get("pixel_seen", 0.0))
            < COAST_SECONDS
        ):
            object_outcomes.retire(self, key, track, source, now)
            del self.tracks[key]
            stats["retired_after_coast"] += 1
    self.tracks = _retire_legacy_aliases(self.tracks, stats, self, source, now)
    keys = sorted(self.tracks)
    predicted = [
        pixels[k]["box"] if k in pixels else _predict(self.tracks[k], source)
        for k in keys
    ]
    descriptors, appearance_costs = _appearance_matrix(
        objects, [self.tracks[k] for k in keys], frame, gray
    )
    measured_objects = [dict(obj, _source_frame_id=source) for obj in objects]
    overlaps = _matrix(objects, predicted + [self.tracks[k]["box"] for k in keys])
    geometry_costs = [
        [
            _pair_cost(
                obj,
                self.tracks[key],
                predicted[j],
                float(overlaps[i, j]),
                float(overlaps[i, j + len(keys)]),
                now,
                threshold,
            )
            for j, key in enumerate(keys)
        ]
        for i, obj in enumerate(measured_objects)
    ]
    measurement_costs = []
    for i, row in enumerate(geometry_costs):
        costs = []
        for j, value in enumerate(row):
            appearance = (
                float(appearance_costs[i, j]) if appearance_costs is not None else 0.0
            )
            track = self.tracks[keys[j]]
            collective = getattr(self, "passage_collective_measurement", None)
            current_guidance = collective is not None and track.get(
                "source"
            ) == getattr(self, "passage_gray_source", None)
            life = track.get("object_lifetime", {})
            previous = life.get("last_pixel_box", track["box"])
            step = life.get(
                "last_pixel_step", life.get("last_measured_step", (0.0, 0.0))
            )
            outward_reuse = (
                frame is not None
                and keys[j] not in pixels
                and step[0] > 1
                and previous[2]
                >= frame.x + frame.rgb.shape[1] - (previous[2] - previous[0]) * 0.3
                and _center(objects[i]["bbox"])[0]
                < _center(previous)[0] - (previous[2] - previous[0]) * 0.15
            )
            if outward_reuse:
                value = 1e6
                stats["unsupported_outgoing_identity_reuse_blocked"] += 1
            elif keys[j] in pixels or current_guidance:
                if current_guidance and (
                    keys[j] not in pixels
                    or keys[j] in getattr(self, "passage_pixel_contradicted", ())
                ):
                    measured = [
                        float(value + collective[k % 2])
                        for k, value in enumerate(track["box"])
                    ]
                else:
                    measured = pixels[keys[j]]["box"]
                candidate = objects[i]["bbox"]
                overlap = _iou(candidate, measured)
                norm = max(
                    8.0,
                    math.hypot(measured[2] - measured[0], measured[3] - measured[1]),
                )
                distance = math.dist(_center(candidate), _center(measured)) / norm
                value = (
                    1e6
                    if overlap < 0.18 and distance > 0.35
                    else 0.72 * (1 - overlap) + 0.24 * distance
                )
            elif frame is not None and appearance > 0.17:
                value = 1e6
            costs.append(value + appearance)
        measurement_costs.append(costs)

    costs = measurement_costs
    matched = _assignment(costs)
    owners = {col: i for i, col in enumerate(matched) if col is not None}
    margins = {
        i: _association_margin(measurement_costs, matched, i, col)
        for i, col in enumerate(matched)
        if col is not None
    }
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
        classified = _classified(obj, threshold)
        track["physical_support_observations"] = (
            track.get("physical_support_observations", 0) + 1
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
        track.update(
            hits=track.get("hits", 0) + 1,
            box=list(obj["bbox"]),
            seen=now,
            source=source,
            association_version=VERSION,
        )
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
            and track["physical_support_observations"] >= required
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
                pixel_identity_verified=key in pixels,
                pixel_measurement=pixels.get(key),
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
    if frame is not None:
        passage_pixels.finish(self, gray, frame, source)
    return rows, failures
