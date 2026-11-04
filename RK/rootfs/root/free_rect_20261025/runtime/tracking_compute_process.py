from pathlib import Path
from types import SimpleNamespace
import os
import pickle
import sys
import threading
import time
import cv2
import numpy as np
import native_tracking
import object_outcomes
import passage_ledger
from tracking_state import Tracks


def view(tracker):
    return dict(
        class_counts=dict(tracker.counts),
        ledger=native_tracking.counters(tracker),
        region_version=tracker.region_version,
        active_tracks=len(tracker.tracks),
    )


def main():
    tracker = None
    source = sys.stdin.buffer
    destination = sys.stdout.buffer
    while True:
        prefix = source.read(8)
        if not prefix:
            return
        if len(prefix) != 8:
            raise RuntimeError("Truncated tracking request header")
        size = int.from_bytes(prefix, "little")
        if size > 32 * 1024 * 1024:
            raise RuntimeError("Tracking request exceeds bounded native frame size")
        raw = source.read(size)
        if len(raw) != size:
            raise RuntimeError("Truncated tracking request")
        request = pickle.loads(raw)
        operation = request["operation"]
        value = request["value"]
        response = dict(sequence=request["sequence"])
        started = time.monotonic()
        cpu_started = time.thread_time()
        try:
            if operation == "init":
                if tracker is not None:
                    raise RuntimeError("Tracking owner already initialized")
                if value["configure"] and hasattr(os, "sched_setaffinity"):
                    os.sched_setaffinity(0, {value["cpu"]})
                    os.setpriority(os.PRIO_PROCESS, 0, 0)
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
                folder = value["outcome_folder"]
                store = object_outcomes.AsyncStore(
                    folder=folder or object_outcomes.FOLDER,
                    configure=value["configure"],
                )
                object_outcomes.configure(tracker, value["context"], store=store)
            elif tracker is None:
                raise RuntimeError("Tracking owner is not initialized")
            elif operation == "observe":
                height, width, channels = value["shape"]
                if channels != 3 or len(value["rgb"]) != height * width * channels:
                    raise ValueError("Native RGB payload geometry mismatch")
                frame = SimpleNamespace(
                    rgb=np.frombuffer(value["rgb"], np.uint8).reshape(height, width, 3),
                    x=value["x"],
                    y=value["y"],
                    capture_received_monotonic=value["capture_received_monotonic"],
                )
                rows, failures = native_tracking.observe_tracks(
                    tracker,
                    value["objects"],
                    value["source"],
                    value["now"],
                    value["hits"],
                    value["threshold"],
                    frame=frame,
                )
                response.update(rows=rows, failures=failures, source=value["source"])
            elif operation == "reset":
                if value == "evidence":
                    tracker.reset_evidence()
                elif value == "region":
                    tracker.reset()
                elif value == "counts":
                    tracker.clear_counts()
                else:
                    raise ValueError("Unknown tracking reset kind")
            elif operation == "bind":
                object_outcomes.configure(tracker, value)
            elif operation == "close":
                object_outcomes.interrupt(tracker, value)
                durable = tracker.outcome_store.close(timeout=3.0)
                response["durable_ack"] = dict(
                    durable=bool(durable),
                    persistence=tracker.outcome_store.snapshot(),
                    error=tracker.outcome_store.snapshot().get("error"),
                )
            else:
                raise ValueError("Unknown private tracking operation")
            response.update(
                view=view(tracker),
                stats=dict(
                    child_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                    child_wall_ms=(time.monotonic() - started) * 1000,
                    child_pid=os.getpid(),
                    child_cpus=(
                        sorted(os.sched_getaffinity(0))
                        if hasattr(os, "sched_getaffinity")
                        else None
                    ),
                    optical_flow_enabled=False,
                    model_loaded=False,
                ),
            )
        except Exception as exc:
            response["error"] = str(exc)
        data = pickle.dumps(response, protocol=5)
        remaining = memoryview(len(data).to_bytes(8, "little") + data)
        while remaining:
            written = destination.write(remaining)
            if not written:
                raise EOFError("Tracking response pipe closed")
            remaining = remaining[written:]
        destination.flush()
        if operation == "close" or response.get("error"):
            return


if __name__ == "__main__":
    main()
