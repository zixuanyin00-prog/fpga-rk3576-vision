from collections import Counter
from pathlib import Path
import hashlib
import json
import os
import queue
import shutil
import threading
import time
import uuid
import passage_ledger

FOLDER = Path("/userdata/vision_int8_20261026_1338/object_outcomes_v6_cpu_process")
MAX_BYTES = 512 * 1024**2
MIN_FREE_BYTES = 256 * 1024**2
TITLES = dict(
    later_success="后来成功",
    final_unconfirmed="最终未确认",
    identity_lost="身份丢失",
    observing="跟踪中",
    recognized_waiting_exit="已确认，待离场",
    observation_interrupted="观察中断，结局未知",
)


from outcome_process_store import ProcessStore


class AsyncStore(ProcessStore):
    def __init__(self, folder=FOLDER, configure=True):
        super().__init__(
            folder, configure, max_bytes=MAX_BYTES, min_free_bytes=MIN_FREE_BYTES
        )

        if not self.wait_ready(timeout=15.0):
            raise RuntimeError(
                "Outcome writer startup failed: " + str(self.snapshot()["error"])
            )


def configure(tracker, metadata, folder=FOLDER, store=None):
    tracker.outcome_context = dict(metadata)
    if not hasattr(tracker, "outcome_store"):
        tracker.outcome_store = store or AsyncStore(folder)
    if not hasattr(tracker, "failure_event_store"):
        from failure_event_cache import FailureEvents

        tracker.failure_event_store = FailureEvents()


def bind(pipeline):
    identity = pipeline.environment.identity() if pipeline.environment else {}
    configure(
        pipeline.tracker,
        dict(
            boot_id=identity.get("boot_id"),
            runtime_session_id=pipeline.session,
            counting_session=pipeline.count_session,
            configuration_version=pipeline.version,
            model_sha256=pipeline.model_sha256,
            run=pipeline.selection.get("run") if pipeline.selection else None,
            pid=os.getpid(),
        ),
    )


def _life(tracker, key, track, source, now):
    if "object_lifetime" not in track:
        context = dict(getattr(tracker, "outcome_context", {}))
        region = str(getattr(tracker, "region_version", "unknown-region"))
        track["object_lifetime"] = dict(
            object_id=region + ":" + str(key) + ":" + uuid.uuid4().hex,
            track_id=int(key),
            region_version=region,
            context=context,
            first_source_frame_id=int(source),
            first_seen_monotonic=float(now),
            last_source_frame_id=int(source),
            last_seen_monotonic=float(now),
            observations=0,
            reliable_observations=0,
            ambiguous_observations=0,
            weak_observations=0,
            appearance_change_observations=0,
            candidate_label_changes=0,
            candidate_predecessor_ids=[],
            ever_unrecognized=False,
            recognition_ever_confirmed=False,
            first_confirmed_source_frame_id=None,
            revision=0,
            first_failure_source_frame_id=None,
            last_box=list(track["box"]),
            last_measured_step=[0.0, 0.0],
            last_status="observing",
        )
    return track["object_lifetime"]


def _emit(tracker, track, status, reason, now, terminal=False):
    life = track.get("object_lifetime")
    if life is None:
        return
    life["revision"] += 1
    life["last_status"] = status
    record = {key: value for key, value in life.items() if key != "context"}
    record.update(life["context"])
    record.update(
        object_id=life["object_id"],
        revision=life["revision"],
        status=status,
        status_title=TITLES[status],
        reason=reason,
        terminal=bool(terminal),
        recorded_monotonic=float(now),
        observed_registered=bool(track.get("observed_registered")),
        counted=bool(track.get("counted")),
        counted_label=track.get("counted_label"),
        counting_semantics_version=getattr(tracker, "counting_semantics_version", 1),
        provisional_identity=not bool(track.get("observed_registered")),
        pose_diagnostics=dict(track.get("pose_diagnostics", {})),
        scope="model_observed_identity; not classification ground truth or unseen-object recall",
    )
    record.update(
        recognition_result="confirmed" if track.get("counted") else "unconfirmed",
        recognition_result_title="曾成功确认" if track.get("counted") else "未曾确认",
        identity_result=(
            "observed_departure"
            if status in ("later_success", "final_unconfirmed")
            else (
                "lost"
                if status == "identity_lost"
                else "censored" if status == "observation_interrupted" else "tracking"
            )
        ),
        identity_result_title=(
            "已观测边缘离场"
            if status in ("later_success", "final_unconfirmed")
            else (
                "离场或身份不明"
                if status == "identity_lost"
                else (
                    "观察中断，离场未验证"
                    if status == "observation_interrupted"
                    else "仍在跟踪"
                )
            )
        ),
    )
    store = getattr(tracker, "outcome_store", None)
    if store is not None:
        store.offer(record)
    terminal_store = getattr(tracker, "failure_event_store", None)
    if (
        terminal_store is not None
        and terminal
        and status in ("final_unconfirmed", "identity_lost")
    ):
        terminal_store.offer(record)

    if not hasattr(tracker, "outcome_recent"):
        from collections import deque

        tracker.outcome_recent = deque(maxlen=128)
    tracker.outcome_recent.append(record)


def observe(
    tracker,
    key,
    track,
    source,
    now,
    box,
    classified,
    ambiguous,
    appearance_penalty,
    temporal,
    frame=None,
    predecessors=None,
):
    life = _life(tracker, key, track, source, now)
    if not life["observations"]:
        if predecessors:
            life["candidate_predecessor_ids"] = list(predecessors)
        _emit(tracker, track, "observing", "first_model_observation", now)
    previous_box = life["last_box"]
    if not ambiguous and int(source) != life["last_source_frame_id"]:
        life["last_measured_step"] = [
            (box[i] + box[i + 2] - previous_box[i] - previous_box[i + 2]) * 0.5
            for i in (0, 1)
        ]
    life.update(
        last_source_frame_id=int(source),
        last_seen_monotonic=float(now),
        last_box=list(box),
        last_pose_accepted=bool(temporal["accepted"]),
        last_pose_reason=temporal["reason"],
        last_identity_trusted=not ambiguous,
    )
    if frame is not None:
        life["native_rectangle"] = [
            int(frame.x),
            int(frame.y),
            int(frame.rgb.shape[1]),
            int(frame.rgb.shape[0]),
        ]
        terminal_store = getattr(tracker, "failure_event_store", None)
        if terminal_store is not None:
            terminal_store.observe(life, track, frame, source, box)
    life["observations"] += 1
    life["reliable_observations"] += int(classified and not ambiguous)
    life["ambiguous_observations"] += int(ambiguous)
    life["weak_observations"] += int(not classified)
    life["appearance_change_observations"] += int(
        appearance_penalty is not None and appearance_penalty > 0.12
    )
    candidate = track.get("candidate_label")
    if candidate is not None:
        if life.get("last_candidate_label") not in (None, candidate):
            life["candidate_label_changes"] += 1
        life["last_candidate_label"] = candidate
    failure = bool(
        ambiguous
        or not classified
        or (track.get("observed_registered") and not temporal["accepted"])
    )
    if failure and not life["ever_unrecognized"]:
        life["ever_unrecognized"] = True
        life["first_failure_source_frame_id"] = int(source)
        _emit(tracker, track, "observing", "first_confirmation_problem", now)
    if track.get("counted") and not life["recognition_ever_confirmed"]:
        life["recognition_ever_confirmed"] = True
        life["first_confirmed_source_frame_id"] = int(source)
        _emit(
            tracker,
            track,
            "recognized_waiting_exit",
            "reached_counting_confirmation",
            now,
        )
    return life["object_id"]


def _observed_exit(life):
    rect = life.get("native_rectangle")
    if (
        not rect
        or not (life.get("last_identity_trusted") or life.get("pixel_identity_trusted"))
        or life["observations"] < 2
    ):
        return False
    x, y, width, height = rect

    pixel = bool(
        life.get("pixel_identity_trusted")
        and life.get("last_pixel_box") is not None
        and life.get("last_pixel_source_frame_id") is not None
    )
    if pixel and life.get("last_identity_trusted"):
        step = (
            int(life["last_pixel_source_frame_id"]) - int(life["last_source_frame_id"])
        ) & 0xFFFFFFFF
        pixel = 0 < step < 0x80000000
    if not pixel and not life.get("last_identity_trusted"):
        return False
    left, top, right, bottom = life["last_pixel_box"] if pixel else life["last_box"]
    dx, dy = (
        life.get("last_pixel_step", life["last_measured_step"])
        if pixel
        else life["last_measured_step"]
    )
    mx, my = max(4.0, (right - left) * 0.2), max(4.0, (bottom - top) * 0.2)
    return bool(
        (dx < -1 and left <= x + mx)
        or (dx > 1 and right >= x + width - mx)
        or (dy < -1 and top <= y + my)
        or (dy > 1 and bottom >= y + height - my)
    )


def retire(tracker, key, track, source, now, reason="coast_timeout"):
    life = track.get("object_lifetime")
    if life is None:
        return
    life["retired_source_frame_id"] = int(source)
    exited = reason == "coast_timeout" and _observed_exit(life)
    life["exit_observed_at_boundary"] = exited
    if reason in (
        "runtime_stopped",
        "configuration_changed",
        "sensor_changed",
        "explicit_counter_reset",
        "legacy_duplicate_hypothesis",
    ):
        passage_ledger.retire(tracker, key, track, "observation_interrupted", now)
        _emit(tracker, track, "observation_interrupted", reason, now, terminal=False)
    else:
        status = (
            ("later_success" if track.get("counted") else "final_unconfirmed")
            if exited
            else "identity_lost"
        )
        passage_ledger.retire(tracker, key, track, status, now)
        _emit(
            tracker,
            track,
            status,
            "measured_outward_boundary_then_disappeared" if exited else reason,
            now,
            terminal=True,
        )
    terminal_store = getattr(tracker, "failure_event_store", None)
    if terminal_store is not None:
        terminal_store.discard(life["object_id"])


def interrupt(tracker, reason, now=None):
    now = time.monotonic() if now is None else now
    for key, track in tracker.tracks.items():
        retire(tracker, key, track, track.get("source", 0), now, reason)
        track.pop("object_lifetime", None)


def snapshot(tracker):
    store = getattr(tracker, "outcome_store", None)
    recent = list(getattr(tracker, "outcome_recent", []))[-8:]
    return dict(
        version=1,
        recent=[
            {
                key: record.get(key)
                for key in (
                    "object_id",
                    "track_id",
                    "status",
                    "status_title",
                    "terminal",
                    "recognition_result",
                    "identity_result",
                    "first_source_frame_id",
                    "last_source_frame_id",
                )
            }
            for record in recent
        ],
        persistence=store.snapshot() if store else dict(enabled=False),
        definition="confirmed once plus measured departure / unresolved at departure / identity lost; interrupted observations are censored",
    )
