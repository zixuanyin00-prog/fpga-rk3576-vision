import numpy as np
import conveyor_layout
import conveyor_features
from passage_flow import (
    forward_backward,
    select_groups,
    patch_descriptor,
    face_edges,
    statistics_backend,
)


def crop(gray, box, origin, inset=0.0):
    l, t, r, b = box
    x, y = origin
    mx = (r - l) * inset
    my = (b - t) * inset
    left = max(0, int(l - x + mx))
    right = min(gray.shape[1], int(r - x - mx))
    top = max(0, int(t - y + my))
    bottom = min(gray.shape[0], int(b - y - my))
    return gray[top:bottom, left:right], (left, top)


def descriptor(gray, box, origin):
    import cv2

    patch, _ = crop(gray, box, origin, 0.1)
    if min(patch.shape, default=0) < 5:
        return None
    patch = (
        cv2.resize(patch, (24, 16), interpolation=cv2.INTER_AREA)
        .astype(np.float32)
        .ravel()
    )
    return patch_descriptor(patch)


def supports(gray, boxes, origin):
    return conveyor_features.extract(gray, boxes, origin, 0)


def descriptors(gray, boxes, origin, inset=0.1):
    return conveyor_features.extract(gray, boxes, origin, 1 if inset == 0.12 else 2)


def correlation(a, b):
    return (
        None
        if a is None or b is None
        else max(-1.0, min(1.0, float(a["vector"] @ b["vector"])))
    )


def face_support(gray, box, origin):

    import cv2

    patch, _ = crop(gray, box, origin)
    if min(patch.shape, default=0) < 10:
        return dict(accepted=False, reason="insufficient_visible_pixels")
    small = cv2.resize(patch, (40, 28), interpolation=cv2.INTER_AREA).astype(np.float32)
    yedge, xedge, side_left, side_right = face_edges(small)

    accepted = yedge >= 11.5 and xedge >= max(6.0, 0.28 * yedge)
    return dict(
        accepted=accepted,
        reason="rectangular_face_edges" if accepted else "rail_or_gap_structure",
        horizontal_boundary=yedge,
        vertical_boundary=xedge,
        side_left=side_left,
        side_right=side_right,
        side_edges_accepted=min(side_left, side_right) >= 6.0,
    )


def interstitial(objects, index, supported_boxes=()):

    obj = objects[index]
    box = obj["bbox"]
    cx = (box[0] + box[2]) * 0.5
    cy = (box[1] + box[3]) * 0.5
    width = box[2] - box[0]
    height = box[3] - box[1]
    neighbours = [
        other["bbox"]
        for i, other in enumerate(objects)
        if i != index and other.get("objectness", 0) > obj.get("objectness", 0) + 0.02
    ]
    neighbours += list(supported_boxes)
    left = []
    right = []
    for other in neighbours:
        center = (other[0] + other[2]) * 0.5
        if abs((other[1] + other[3]) * 0.5 - cy) > 0.6 * height:
            continue
        if not 0.45 * width <= other[2] - other[0] <= 1.8 * width:
            continue
        if center < cx and other[2] > box[0] + width * 0.12:
            left.append(other)
        elif center > cx and other[0] < box[2] - width * 0.12:
            right.append(other)
    return any(
        0 <= b[0] - a[2] < width * 0.82 and a[2] <= cx <= b[0]
        for a in left
        for b in right
    )


def observed_face_motion(
    previous,
    current,
    before,
    after,
    origin,
    before_cache=None,
    after_cache=None,
    diagnostics=None,
):

    if not before or not after or len(before) * len(after) > 144:
        return None
    old_cache = dict(before_cache or {})
    new_cache = dict(after_cache or {})
    missing = [box for box in before if old_cache.get(tuple(box)) is None]
    old_cache.update(zip(map(tuple, missing), descriptors(previous, missing, origin)))
    missing = [
        obj["bbox"] for obj in after if new_cache.get(tuple(obj["bbox"])) is None
    ]
    new_cache.update(zip(map(tuple, missing), descriptors(current, missing, origin)))
    old = [(box, old_cache[tuple(box)]) for box in before]
    new = [(obj["bbox"], new_cache[tuple(obj["bbox"])]) for obj in after]
    pairs = []
    for i, (box, a) in enumerate(old):
        if a is None:
            continue
        width = box[2] - box[0]
        height = box[3] - box[1]
        for j, (candidate, b) in enumerate(new):
            if b is None:
                continue
            cw = candidate[2] - candidate[0]
            ch = candidate[3] - candidate[1]
            if (
                not 0.55 <= cw / max(width, 1.0) <= 1.8
                or not 0.6 <= ch / max(height, 1.0) <= 1.7
                or not 0.35 <= cw * ch / max(1.0, width * height) <= 2.8
            ):
                continue
            corr = correlation(a, b)
            textured = min(a["std"], b["std"]) >= 6.0
            similarity = (
                max(0.0, corr)
                if textured
                else max(0.0, 0.6 - abs(a["mean"] - b["mean"]) / 100.0)
            )
            if similarity < 0.2:
                continue
            pairs.append(
                (
                    i,
                    j,
                    (candidate[0] + candidate[2] - box[0] - box[2]) * 0.5,
                    (candidate[1] + candidate[3] - box[1] - box[3]) * 0.5,
                    width,
                    height,
                    similarity,
                )
            )
    if not pairs:
        return None
    if conveyor_layout.library() is None:
        raise RuntimeError(
            "Native real-image layout scoring is unavailable: "
            + str(conveyor_layout.backend())
        )
    result = conveyor_layout.select(pairs, len(old), len(new))
    score = result["score"]
    runner = result["runner"]
    chosen = result["chosen"]
    minimum = 1 if min(len(old), len(new)) == 1 else 2
    if len(chosen) < minimum:
        return None
    dx = result["dx"]
    dy = result["dy"]
    width = result["width"]
    margin = score - runner
    required_margin = result["required_margin"]
    if score < (0.78 if minimum == 1 else 1.15):
        return None
    if margin < required_margin:
        if diagnostics is not None:
            diagnostics.update(
                reason="competing_current_image_layouts",
                score=score,
                runner_score=runner,
                margin=margin,
                required_margin=required_margin,
                ambiguous_boxes=[
                    list(new[index][0]) for index in result["ambiguous_indices"]
                ],
            )
        return None
    return dict(
        shift=[dx, dy],
        support=len(chosen),
        score=score,
        runner_score=runner,
        margin=margin,
        method="current_pair_face_layout_and_pixel_correlation",
        _matches=[
            dict(
                previous_box=list(old[pair[0]][0]),
                current_box=list(new[pair[1]][0]),
                similarity=pair[6],
                quality=float(quality),
            )
            for quality, pair in chosen
        ],
    )


def measure(tracker, frame, source, objects=()):

    import cv2

    gray = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2GRAY)
    tracker.passage_pixel_contradicted = set()
    tracker.passage_collective_measurement = None
    previous = getattr(tracker, "passage_gray", None)
    prior = getattr(tracker, "passage_gray_source", None)
    origin = (frame.x, frame.y)
    measurements = {}
    tracker.passage_observed_motion = None
    tracker.passage_current_face_descriptors = {}
    tracker.passage_direct_face_owners = {}
    tracker.passage_layout_diagnostics = {}
    tracker.passage_layout_ambiguous_boxes = set()
    tracker.passage_current_support = [
        face_support(gray, obj["bbox"], origin) for obj in objects
    ]
    if (
        previous is None
        or previous.shape != gray.shape
        or getattr(tracker, "passage_gray_origin", None) != origin
    ):
        return gray, measurements
    supported = [
        obj
        for index, obj in enumerate(objects)
        if tracker.passage_current_support[index]["accepted"]
        and not interstitial(objects, index)
    ]
    current_descriptors = {
        tuple(obj["bbox"]): descriptor(gray, obj["bbox"], origin) for obj in supported
    }
    tracker.passage_current_face_descriptors = current_descriptors
    tracker.passage_observed_motion = observed_face_motion(
        previous,
        gray,
        getattr(tracker, "passage_observed_boxes", ()),
        supported,
        origin,
        getattr(tracker, "passage_observed_descriptors", None),
        current_descriptors,
        tracker.passage_layout_diagnostics,
    )
    tracker.passage_layout_ambiguous_boxes = {
        tuple(box)
        for box in tracker.passage_layout_diagnostics.get("ambiguous_boxes", ())
    }
    if tracker.passage_observed_motion is not None:
        tracker.passage_collective_measurement = tracker.passage_observed_motion[
            "shift"
        ]
        for match in tracker.passage_observed_motion.pop("_matches"):
            if match["similarity"] < 0.7:
                continue
            owners = [
                key
                for key, track in tracker.tracks.items()
                if track.get("source") == prior
                and track.get("pixel_source") == prior
                and track.get("object_lifetime", {}).get("last_identity_trusted")
                and list(track["box"]) == match["previous_box"]
            ]
            if len(owners) != 1:
                continue
            key = owners[0]
            box = match["current_box"]
            before = match["previous_box"]
            shift = [
                (box[i] + box[i + 2] - before[i] - before[i + 2]) * 0.5 for i in (0, 1)
            ]
            measurements[key] = dict(
                box=box,
                shift=shift,
                ncc=match["similarity"],
                source_frame_id=int(source),
                support=tracker.passage_observed_motion["support"],
                support_kind="independent_current_image_faces",
                method="current_image_pair_layout_and_face_match",
            )
            tracker.passage_direct_face_owners[tuple(box)] = key
            tracker.tracks[key].pop("pixel_points", None)
            tracker.tracks[key].pop("pixel_points_source", None)
    points = []
    groups = []
    for key, track in tracker.tracks.items():
        if key in measurements:
            continue
        if track.get("pixel_source") != prior:
            continue
        box = track.get("pixel_box", track["box"])
        patch, offset = crop(previous, box, origin, 0.05)
        if min(patch.shape, default=0) < 8:
            continue
        saved = (
            track.get("pixel_points")
            if track.get("pixel_points_source") == prior
            else None
        )
        if saved is not None:
            inside = (
                (saved[:, 0] >= offset[0])
                & (saved[:, 0] < offset[0] + patch.shape[1])
                & (saved[:, 1] >= offset[1])
                & (saved[:, 1] < offset[1] + patch.shape[0])
            )
            saved = saved[inside]
        if saved is not None and len(saved) >= 6:
            features = saved - np.array(offset, dtype=np.float32)
        else:
            features = cv2.goodFeaturesToTrack(
                patch, maxCorners=12, qualityLevel=0.035, minDistance=4, blockSize=3
            )
            if features is None or len(features) < 3:
                continue
        group = []
        for point in features.reshape(-1, 2):
            group.append(len(points))
            points.append(point + np.array(offset, dtype=np.float32))
        groups.append((key, box, group))
    if not points:
        return gray, measurements
    a = np.asarray(points, np.float32).reshape(-1, 1, 2)
    options = dict(
        winSize=(25, 25),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 15, 0.03),
    )
    b, status, back, back_status = forward_backward(tracker, previous, gray, a, options)
    if b is None:
        return gray, measurements
    if back is None:
        return gray, measurements
    selected = select_groups(tracker, a, b, status, back, back_status, groups)
    if selected is None:
        valid = (
            (status.ravel() != 0)
            & (back_status.ravel() != 0)
            & (np.linalg.norm(back - a, axis=2).ravel() < 1.8)
        )
        movement = (b - a).reshape(-1, 2)
    for key, box, group in groups:
        if selected is None:
            good = [index for index in group if valid[index]]
            if len(good) < 3:
                continue
            shift = np.median(movement[good], axis=0)
            scatter = np.linalg.norm(movement[good] - shift, axis=1)
            coherent = scatter < max(2.0, float(np.linalg.norm(shift)) * 0.12)
            if int(coherent.sum()) < 3:
                continue
            kept = np.asarray(good)[coherent]
            shift = np.median(movement[kept], axis=0)
        else:
            selection = selected.get(key)
            if selection is None:
                continue
            shift, kept = selection
        measured = [float(value + shift[i % 2]) for i, value in enumerate(box)]
        ncc = correlation(
            descriptor(previous, box, origin), descriptor(gray, measured, origin)
        )
        if ncc is None or ncc < 0.55:
            continue
        tracker.tracks[key]["pixel_points"] = b[kept].reshape(-1, 2).copy()
        tracker.tracks[key]["pixel_points_source"] = source
        measurements[key] = dict(
            box=measured,
            shift=[float(v) for v in shift],
            ncc=ncc,
            source_frame_id=int(source),
            support=len(kept),
        )

    active_shifts = [
        value["shift"]
        for key, value in measurements.items()
        if tracker.tracks[key].get("source") == prior
    ]
    observed = tracker.passage_observed_motion
    if observed is not None or len(active_shifts) >= 3:
        shifts = np.asarray(active_shifts, np.float32)
        collective = (
            np.asarray(observed["shift"], np.float32)
            if observed
            else np.median(shifts, axis=0)
        )
        radius = max(10.0, float(np.linalg.norm(collective)) * 0.4)
        agreeing = (
            np.linalg.norm(shifts - collective, axis=1) <= radius
            if len(shifts)
            else np.zeros(0, bool)
        )
        if observed is not None or (
            int(agreeing.sum()) >= 3 and int(agreeing.sum()) > len(shifts) / 2
        ):
            if observed is None:
                collective = np.median(shifts[agreeing], axis=0)
            tracker.passage_collective_measurement = [
                float(value) for value in collective
            ]
            for key, box, _ in groups:
                existing = measurements.get(key)
                if (
                    existing
                    and np.linalg.norm(np.asarray(existing["shift"]) - collective)
                    <= radius
                ):
                    continue
                if existing is not None:
                    tracker.passage_pixel_contradicted.add(key)
                template, offset = crop(previous, box, origin, 0.1)
                if min(template.shape, default=0) < 8:
                    continue
                expected = np.asarray(offset, np.float32) + collective
                search_radius = max(6, int(float(np.linalg.norm(collective)) * 0.2))
                left = max(0, int(expected[0]) - search_radius)
                top = max(0, int(expected[1]) - 5)
                right = min(
                    gray.shape[1],
                    int(expected[0]) + template.shape[1] + search_radius + 1,
                )
                bottom = min(gray.shape[0], int(expected[1]) + template.shape[0] + 6)
                search = gray[top:bottom, left:right]
                if (
                    search.shape[0] < template.shape[0]
                    or search.shape[1] < template.shape[1]
                ):
                    measurements.pop(key, None)
                    continue
                scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
                _, quality, _, position = cv2.minMaxLoc(scores)
                if quality < 0.65:
                    measurements.pop(key, None)
                    continue
                shift = np.asarray(
                    [left + position[0] - offset[0], top + position[1] - offset[1]],
                    np.float32,
                )
                measured = [float(value + shift[i % 2]) for i, value in enumerate(box)]
                measurements[key] = dict(
                    box=measured,
                    shift=[float(v) for v in shift],
                    ncc=float(quality),
                    source_frame_id=int(source),
                    support=observed["support"] if observed else int(agreeing.sum()),
                    method="current_collective_flow_and_individual_pixel_match",
                )
                tracker.passage_pixel_contradicted.discard(key)
                tracker.tracks[key].pop("pixel_points", None)

    for key, track in tracker.tracks.items():
        if key in measurements or track.get("pixel_source") != prior:
            continue
        box = track.get("pixel_box", track["box"])
        w = box[2] - box[0]
        h = box[3] - box[1]
        last = track.get("pixel_measurement", {}).get(
            "shift", track.get("velocity", (0.0, 0.0))
        )
        if not (last[0] > 1 and box[2] >= origin[0] + gray.shape[1] - 0.6 * w):
            continue
        part = [box[0], box[1] + h * 0.1, box[0] + w * 0.5, box[3] - h * 0.1]
        template, offset = crop(previous, part, origin)
        if min(template.shape, default=0) < 8 or float(template.std()) < 4.0:
            continue
        left = max(0, offset[0] - 3)
        right = gray.shape[1]
        top = max(0, offset[1] - int(h * 0.3))
        bottom = min(gray.shape[0], offset[1] + template.shape[0] + int(h * 0.3))
        search = gray[top:bottom, left:right]
        if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
            continue
        _, quality, _, position = cv2.minMaxLoc(
            cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        )
        shift = [
            float(left + position[0] - offset[0]),
            float(top + position[1] - offset[1]),
        ]
        if quality < 0.8 or shift[0] <= 1:
            continue
        measured = [float(value + shift[i % 2]) for i, value in enumerate(box)]
        if (
            measured[0] >= origin[0] + gray.shape[1]
            or measured[2] < origin[0] + gray.shape[1] - 0.25 * w
        ):
            continue
        measurements[key] = dict(
            box=measured,
            shift=shift,
            ncc=float(quality),
            source_frame_id=int(source),
            support=1,
            method="visible_boundary_half_pixel_match",
        )
    return gray, measurements


def finish(tracker, gray, frame, source, objects=()):
    tracker.passage_gray = gray
    tracker.passage_gray_source = int(source)
    tracker.passage_gray_origin = (frame.x, frame.y)

    tracker.passage_observed_boxes = [list(obj["bbox"]) for obj in objects]
    tracker.passage_observed_descriptors = {
        tuple(obj["bbox"]): getattr(
            tracker, "passage_current_face_descriptors", {}
        ).get(tuple(obj["bbox"]))
        for obj in objects
    }
