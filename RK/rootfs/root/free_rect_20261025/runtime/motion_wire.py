import math


def freeze(snapshot, source):
    empty = dict(
        valid=False,
        state="learning",
        anchor_source_frame_id=int(source),
        velocity_px_per_source_frame=[0.0, 0.0],
        source_fps=60.0,
        revision=0,
        max_horizon_frames=0,
        evidence_source="trusted_real_detections",
    )
    if not isinstance(snapshot, dict):
        return dict(empty, rejection_reason="missing_global_motion_snapshot")
    try:
        if type(snapshot.get("anchor_source_frame_id")) is not int or snapshot[
            "anchor_source_frame_id"
        ] != int(source):
            raise ValueError("motion_anchor_differs_from_real_detection_source")
        if snapshot.get("evidence_source") != "trusted_real_detections":
            raise ValueError("motion_evidence_is_not_real_detection")
        state = snapshot.get("state")
        if state not in ("learning", "tracking", "relearning"):
            raise ValueError("unknown_global_motion_state")
        velocity = snapshot.get("velocity_px_per_source_frame")
        if not isinstance(velocity, (list, tuple)) or len(velocity) != 2:
            raise ValueError("invalid_global_velocity_shape")
        velocity = [float(v) for v in velocity]
        if not all(math.isfinite(v) for v in velocity):
            raise ValueError("nonfinite_global_velocity")
        fps = float(snapshot.get("source_fps", 0))
        if not math.isfinite(fps) or fps != 60.0:
            raise ValueError("unsupported_source_clock")
        revision = snapshot.get("revision")
        horizon = snapshot.get("max_horizon_frames")
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid_global_motion_revision")
        if type(horizon) is not int or not 0 <= horizon <= 24:
            raise ValueError("invalid_motion_horizon")
        if type(snapshot.get("valid")) is not bool:
            raise ValueError("invalid_motion_valid_flag")
        if snapshot["valid"] and state != "tracking":
            raise ValueError("untrusted_motion_state_claimed_valid")
        result = {key: snapshot[key] for key in empty}
        result["velocity_px_per_source_frame"] = velocity
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        return dict(empty, rejection_reason=str(exc))
