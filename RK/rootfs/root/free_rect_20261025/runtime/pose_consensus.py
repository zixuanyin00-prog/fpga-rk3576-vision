from collections import Counter, deque

MIN_SUPPORT = 3
MIN_AGREEMENT = 0.75
MAX_OBSERVATIONS = 16


def delta(new, old):
    value = (int(new) - int(old)) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def update(track, obj, source, threshold, geometry_trusted, appearance_penalty=None):
    history = track.setdefault("pose_observations", deque(maxlen=MAX_OBSERVATIONS))
    support = track.setdefault("pose_lifetime_support", Counter())
    weights = track.setdefault("pose_lifetime_weights", Counter())
    diagnostics = track.setdefault("pose_diagnostics", Counter())
    result = dict(
        accepted=False,
        label=None,
        support=0,
        reliable_observations=sum(support.values()),
        window_observations=len(history),
        agreement=0.0,
        mean_support_confidence=None,
        required_support=MIN_SUPPORT,
        reason="identity_uncertain",
        single_frame_label_disagrees=False,
        evidence_preserved=True,
        history_reset_reason=None,
        evidence_scope="measured_identity_lifetime",
    )
    if not geometry_trusted:
        diagnostics["identity_uncertain_observations"] += 1
        result["diagnostics"] = dict(diagnostics)
        return result
    if appearance_penalty is not None and appearance_penalty > 0.12:
        diagnostics["appearance_change_observations"] += 1
        if support:
            diagnostics["appearance_history_preserved"] += 1
    source = int(source)
    last = track.get("pose_last_source")
    distinct = last is None or delta(source, last) > 0
    raw = obj.get("raw_label", obj["label"])
    reliable = bool(
        obj["confidence"] >= threshold
        and obj["label"] not in ("uncertain", "unreadable")
    )
    if distinct:
        track["pose_last_source"] = source
        history.append((source, raw, float(obj["confidence"]), reliable))
        if reliable:
            support[raw] += 1
            weights[raw] += float(obj["confidence"])
            track["pose_latest_reliable_label"] = raw
            track["pose_latest_reliable_source"] = source
        else:
            diagnostics["weak_frames_preserved"] += 1
    else:
        diagnostics["duplicate_or_old_source_ignored"] += 1
    if not support:
        result.update(
            reason="not_enough_reliable_frames", diagnostics=dict(diagnostics)
        )
        return result
    winner = max(
        support, key=lambda label: (support[label], weights[label], str(label))
    )
    total = sum(support.values())
    agreement = support[winner] / total
    runner = max(
        (weight for label, weight in weights.items() if label != winner), default=0.0
    )
    margin = (weights[winner] - runner) / sum(weights.values())
    conflict = track["pose_latest_reliable_label"] != winner
    trusted = (
        support[winner] >= MIN_SUPPORT
        and agreement >= MIN_AGREEMENT
        and margin >= 0.4
        and not conflict
    )
    result.update(
        accepted=trusted,
        label=winner,
        support=support[winner],
        reliable_observations=total,
        window_observations=len(history),
        agreement=agreement,
        mean_support_confidence=weights[winner] / support[winner],
        vote_margin=margin,
        last_support_source_age=delta(source, track["pose_latest_reliable_source"]),
        single_frame_label_disagrees=reliable and raw != winner,
        reason=(
            "measured_multi_frame_consensus"
            if trusted
            else (
                "current_reliable_label_conflict"
                if conflict
                else (
                    "conflicting_labels"
                    if agreement < MIN_AGREEMENT
                    else "not_enough_reliable_frames"
                )
            )
        ),
        diagnostics=dict(diagnostics),
    )
    if not trusted or not reliable or result["single_frame_label_disagrees"]:
        result["evidence"] = [list(sample) for sample in history]
    return result
