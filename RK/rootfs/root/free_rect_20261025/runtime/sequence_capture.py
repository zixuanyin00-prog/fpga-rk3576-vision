from pathlib import Path
import copy
import hashlib
import json
import math
import os
import queue
import shutil
import threading
import time


def _json(path, value):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class SequenceCapture:
    def __init__(self):
        self.lock = threading.Lock()
        self.work = queue.Queue(512)
        self.active = None
        self.thread = threading.Thread(
            target=self._writer, name="sequence-evidence", daemon=True
        )
        self.thread.start()

    def start(self, folder, seconds, metadata, pixels=True):
        if not self.thread.is_alive():
            raise RuntimeError("Sequence writer is unavailable")
        if type(seconds) not in (int, float) or not 1 <= seconds <= 5:
            raise ValueError("Sequence duration must be between 1 and 5 seconds")
        if type(pixels) is not bool:
            raise ValueError("Sequence pixels must be a boolean")
        metadata = dict(metadata, capture_pixels=pixels)

        base = Path("/dev/shm/vision_sequence_evidence")
        base.mkdir(exist_ok=True)
        rect = metadata["settings"]["rectangle"]
        width, height = max(512, rect[2]), max(288, rect[3])
        frames = math.ceil(float(seconds) * 60) + 8
        pixel_bytes = width * height * 3 * frames if pixels else 0
        overhead = frames * 128 * 1024
        reserve = 256 * 1024**2
        available = (
            int(
                next(
                    line.split()[1]
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemAvailable:")
                )
            )
            * 1024
        )
        if (
            shutil.disk_usage(base).free < pixel_bytes + overhead + reserve
            or available < 2 * pixel_bytes + overhead + reserve
            or os.statvfs(base).f_favail < (frames if pixels else 0) + 64
        ):
            raise ValueError(
                "Insufficient memory/tmpfs space or inodes for this bounded sequence"
            )
        folder = base / Path(folder).name
        folder.mkdir(exist_ok=False)
        with self.lock:
            if self.active is not None:
                raise ValueError("A sequence capture is already running")
            self.active = dict(
                folder=folder,
                seconds=float(seconds),
                metadata=metadata,
                pixels=pixels,
                first=None,
                end=None,
                closed=False,
                frames={},
                dropped=0,
                errors=[],
                created=time.monotonic(),
            )
            _json(folder / "sequence_request.json", dict(seconds=seconds, **metadata))
        return dict(
            state="running", folder=str(folder), seconds=seconds, capture_pixels=pixels
        )

    def capture(self, frame, context, version):
        with self.lock:
            active = self.active
            if active is None or active["closed"]:
                return
            if version != active["metadata"]["version"]:
                active["errors"].append("Configuration changed during capture")
                active["closed"] = True
                return
            at = float(frame.capture_received_monotonic)
            if active["first"] is None:
                active["first"] = at
                active["end"] = at + active["seconds"]
            if at >= active["end"]:
                active["closed"] = True
                return
            try:
                if active["pixels"]:

                    self.work.put_nowait(("frame", active, frame, dict(context)))
                else:

                    descriptor = dict(
                        source_frame_id=int(frame.source_frame_id),
                        size=[frame.rgb.shape[1], frame.rgb.shape[0]],
                        origin=[frame.x, frame.y],
                        capture_received_monotonic=at,
                    )
                    self.work.put_nowait(
                        ("metadata", active, descriptor, dict(context))
                    )
            except queue.Full:
                active["dropped"] += 1

    def result(self, frame, objects, rows, sample, version, counting, diagnostics=None):
        with self.lock:
            active = self.active
            if (
                active is None
                or active["first"] is None
                or version != active["metadata"]["version"]
            ):
                return
            if not active["first"] <= frame.capture_received_monotonic < active["end"]:
                return

            counting = counting() if callable(counting) else counting
            if diagnostics is not None:
                sample = dict(
                    sample,
                    tracking_diagnostics=dict(
                        tracking_phases=diagnostics.get("tracking_phases", {}),
                        last_image_rejections=diagnostics.get(
                            "last_image_rejections", []
                        ),
                    ),
                )
            data = copy.deepcopy(
                dict(
                    raw_objects=objects,
                    rows=rows,
                    sample=sample,
                    published_monotonic=time.monotonic(),
                    counting=counting,
                )
            )
            try:
                self.work.put_nowait(
                    ("result", active, int(frame.source_frame_id), data)
                )
            except queue.Full:
                active["dropped"] += 1

    def _writer(self):
        os.sched_setaffinity(0, {0, 1, 2})
        os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        while True:
            try:
                kind, active, value, extra = self.work.get(timeout=0.05)
                if kind == "frame":
                    frame = value
                    source = int(frame.source_frame_id)
                    entry = active["frames"].setdefault(
                        source, dict(source_frame_id=source)
                    )
                    if "file" in entry:
                        raise ValueError("Duplicate physical frame in sequence")
                    raw = frame.rgb.tobytes()
                    name = f"native_{source:010d}.rgb"
                    (active["folder"] / name).write_bytes(raw)
                    entry.update(
                        file=name,
                        sha256=hashlib.sha256(raw).hexdigest(),
                        size=[frame.rgb.shape[1], frame.rgb.shape[0]],
                        origin=[frame.x, frame.y],
                        capture_received_monotonic=frame.capture_received_monotonic,
                        context=extra,
                    )
                elif kind == "metadata":
                    entry = active["frames"].setdefault(
                        value["source_frame_id"],
                        dict(source_frame_id=value["source_frame_id"]),
                    )
                    if "capture_received_monotonic" in entry:
                        raise ValueError("Duplicate physical frame in sequence")
                    entry.update(value, context=extra)
                else:
                    active["frames"].setdefault(
                        value, dict(source_frame_id=value)
                    ).update(extra)
            except queue.Empty:
                pass
            except Exception as exc:
                with self.lock:
                    if self.active is not None:
                        self.active["errors"].append(str(exc))
            with self.lock:
                active = self.active
                if active is None:
                    continue
                deadline = (
                    (active["end"] + 2.0)
                    if active["end"]
                    else (active["created"] + 10.0)
                )
                if time.monotonic() < deadline or not self.work.empty():
                    continue
                active["closed"] = True
                self.active = None
            try:
                rows = sorted(
                    active["frames"].values(), key=lambda row: row["source_frame_id"]
                )
                raw = [row for row in rows if "file" in row]
                captured = [row for row in rows if "capture_received_monotonic" in row]
                with (active["folder"] / "frames.jsonl").open(
                    "w", encoding="utf-8"
                ) as stream:
                    for row in rows:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                ids = [row["source_frame_id"] for row in captured]
                result_ids = [
                    row["source_frame_id"] for row in rows if "raw_objects" in row
                ]
                gaps = sum(max(0, b - a - 1) for a, b in zip(ids, ids[1:]))
                complete = dict(
                    state="complete",
                    requested_seconds=active["seconds"],
                    first_capture_monotonic=active["first"],
                    window_end_monotonic=active["end"],
                    raw_frames=len(raw),
                    captured_frames=len(captured),
                    result_frames=len(result_ids),
                    first_source_frame=ids[0] if ids else None,
                    last_source_frame=ids[-1] if ids else None,
                    missing_source_frames=gaps,
                    queue_dropped=active["dropped"],
                    errors=active["errors"],
                    pixels_recorded=active["pixels"],
                    pixels_unchanged=True if active["pixels"] else None,
                    labels_are_ground_truth=False,
                    metadata=active["metadata"],
                )
                complete["valid"] = (
                    bool(captured)
                    and not gaps
                    and not active["dropped"]
                    and not active["errors"]
                    and ids == result_ids
                )
                _json(active["folder"] / "sequence_complete.json", complete)
            except Exception as exc:
                _json(
                    active["folder"] / "sequence_complete.json",
                    dict(state="failed", error=str(exc)),
                )
