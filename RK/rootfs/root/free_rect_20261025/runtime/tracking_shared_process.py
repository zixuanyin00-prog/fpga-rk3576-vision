from types import SimpleNamespace
import mmap
import os
import pickle
import sys
import time
import cv2
import numpy as np
import native_tracking
import object_outcomes
import passage_ledger
from tracking_state import Tracks


def view(tracker):
    ledger = passage_ledger.summary(tracker)
    ledger["pending_geometry_tracks"] = sum(
        not track.get("observed_registered", False) for track in tracker.tracks.values()
    )
    return dict(
        class_counts=dict(tracker.counts),
        ledger=ledger,
        region_version=tracker.region_version,
        active_tracks=len(tracker.tracks),
        failure_event_cache=tracker.failure_event_store.snapshot(),
    )


def main():
    sys.setswitchinterval(0.0005)
    tracker = None
    mapping = None
    shared_fd = None
    source = sys.stdin.buffer
    destination = sys.stdout.buffer
    try:
        while True:
            prefix = source.read(8)
            if not prefix:
                return
            if len(prefix) != 8:
                raise RuntimeError("Truncated shared tracking request header")
            size = int.from_bytes(prefix, "little")
            if size > 32 * 1024 * 1024:
                raise RuntimeError("Shared tracking metadata exceeded bounded schema")
            raw = source.read(size)
            if len(raw) != size:
                raise RuntimeError("Truncated shared tracking metadata")
            request = pickle.loads(raw)
            operation = request["operation"]
            value = request["value"]
            response = dict(sequence=request["sequence"])
            started = time.monotonic()
            cpu_started = time.thread_time()
            try:
                if operation == "init":
                    if tracker is not None:
                        raise RuntimeError("Shared tracker already initialized")
                    shared_fd = value["shared_fd"]
                    capacity = value["shared_capacity"]
                    if capacity != 518400 * 3:
                        raise ValueError("Shared RGB capacity mismatch")
                    mapping = mmap.mmap(shared_fd, capacity, access=mmap.ACCESS_READ)
                    if value["configure"] and hasattr(os, "sched_setaffinity"):
                        os.sched_setaffinity(0, {value["cpu"]})
                        os.setpriority(os.PRIO_PROCESS, 0, -10)
                    cv2.setNumThreads(1)
                    state = value["state"]
                    tracker = Tracks()
                    tracker.counts = dict(state.get("counts", {}))
                    tracker.observed_total = state.get(
                        "observed_total", sum(tracker.counts.values())
                    )
                    if "region_version" in state:
                        tracker.region_version = state["region_version"]
                    tracker.next_id = state.get("next_id", 0)
                    if state.get("restore_record") is not None:
                        passage_ledger.restore(tracker, state["restore_record"])
                    store = object_outcomes.AsyncStore(
                        folder=value["outcome_folder"] or object_outcomes.FOLDER,
                        configure=value["configure"],
                    )
                    object_outcomes.configure(tracker, value["context"], store=store)
                elif tracker is None:
                    raise RuntimeError("Shared tracker is not initialized")
                elif operation == "observe":
                    height, width, channels = value["shape"]
                    if (
                        channels != 3
                        or height <= 0
                        or width <= 0
                        or height * width > 518400
                    ):
                        raise ValueError("Native shared RGB geometry mismatch")
                    rgb = np.frombuffer(
                        mapping, np.uint8, count=height * width * 3
                    ).reshape(height, width, 3)
                    frame = SimpleNamespace(
                        rgb=rgb,
                        x=value["x"],
                        y=value["y"],
                        capture_received_monotonic=value["capture_received_monotonic"],
                    )
                    frame.__dict__.update(value["frame_descriptor"])
                    frame.failure_event_context = value["failure_event_context"]
                    try:
                        rows, failures = native_tracking.observe_tracks(
                            tracker,
                            value["objects"],
                            value["source"],
                            value["now"],
                            value["hits"],
                            value["threshold"],
                            frame=frame,
                        )
                    finally:
                        del frame, rgb
                    response.update(
                        rows=rows, failures=failures, source=value["source"]
                    )
                elif operation == "reset":
                    if value == "evidence":
                        tracker.reset_evidence()
                    elif value == "region":
                        tracker.reset()
                    elif value == "counts":
                        tracker.clear_counts()
                    else:
                        raise ValueError("Unknown shared tracking reset kind")
                elif operation == "bind":
                    object_outcomes.configure(tracker, value)
                elif operation == "close":
                    close_started = time.monotonic()
                    object_outcomes.interrupt(tracker, value)
                    durable = tracker.outcome_store.close(timeout=3.0)
                    terminal_drained = tracker.failure_event_store.close(
                        timeout=max(0.1, 4.0 - (time.monotonic() - close_started))
                    )
                    persistence = tracker.outcome_store.snapshot()
                    response["durable_ack"] = dict(
                        durable=bool(durable),
                        persistence=persistence,
                        error=persistence.get("error"),
                        terminal_logs_drained=terminal_drained,
                        terminal_logs=tracker.failure_event_store.snapshot(),
                    )
                else:
                    raise ValueError("Unknown shared tracking operation")
                response.update(
                    view=view(tracker),
                    stats=dict(
                        child_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                        child_wall_ms=(time.monotonic() - started) * 1000,
                        child_pid=os.getpid(),
                        child_cpus=sorted(os.sched_getaffinity(0)),
                        child_nice=os.getpriority(os.PRIO_PROCESS, 0),
                        child_policy=os.sched_getscheduler(0),
                        optical_flow_enabled=False,
                        model_loaded=False,
                        shared_rgb_readonly=True,
                        tracking_phases=dict(
                            getattr(tracker, "conveyor_phase_timing", {})
                        ),
                        last_image_rejections=list(
                            getattr(tracker, "last_image_rejections", ())
                        )[:16],
                        association=(
                            {
                                name: tracker.association_stats.get(name, 0)
                                for name in (
                                    "active_tracks",
                                    "new_tracks",
                                    "registered_observed_tracks",
                                    "counted_tracks",
                                    "retired_after_coast",
                                    "ambiguous_observations",
                                    "image_rejected_observations",
                                    "counted_reverse_pitch_links_rejected",
                                    "new_tracks_with_predecessor_candidates",
                                    "trusted_global_fast_frames",
                                    "image_bootstrap_frames",
                                    "image_bootstrap_whole_frame_frames",
                                    "image_bootstrap_local_single_identity_frames",
                                )
                            }
                            if hasattr(tracker, "association_stats")
                            else {}
                        ),
                    ),
                )
            except Exception as exc:
                response["error"] = str(exc)
            data = pickle.dumps(response, protocol=5)
            remaining = memoryview(len(data).to_bytes(8, "little") + data)
            while remaining:
                written = destination.write(remaining)
                if not written:
                    raise EOFError("Shared tracking response pipe closed")
                remaining = remaining[written:]
            destination.flush()
            if operation == "close" or response.get("error"):
                return
    finally:
        if mapping is not None:
            mapping.close()
        if shared_fd is not None:
            os.close(shared_fd)


if __name__ == "__main__":
    main()
