from collections import deque
from pathlib import Path
import json
import os
import queue
import re
import threading
import time


class DisplayTrace:
    def __init__(self, monitor, root="."):
        self.monitor = monitor
        self.root = Path(root) / "display_alignment_evidence"
        self.samples = deque(maxlen=120)
        self.work = queue.Queue(8)
        self.dropped = 0
        self.errors = []
        self.saved = {}
        self.active = None
        self.poll_at = 0.0
        self.thread = threading.Thread(
            target=self._writer, name="display-evidence-writer", daemon=True
        )
        self.thread.start()

    def _offer(self, item):
        try:
            self.work.put_nowait(item)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _writer(self):
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, {0, 1, 2, 3})
        while True:
            item = self.work.get()
            if item is None:
                return
            try:
                kind, path, data = item
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "raw":
                    path.write_bytes(data)
                    self.saved[str(path.parent)] = (
                        self.saved.get(str(path.parent), 0) + 1
                    )
                elif kind == "append":
                    with path.open("a", encoding="utf-8") as f:
                        for row in data:
                            f.write(json.dumps(row, separators=(",", ":")) + "\n")
                else:
                    if kind == "complete":
                        data = dict(
                            data,
                            raw_saved=self.saved.pop(str(path.parent), 0),
                            errors=list(self.errors),
                        )
                    temp = path.with_suffix(".tmp")
                    temp.write_text(
                        json.dumps(data, separators=(",", ":")), encoding="utf-8"
                    )
                    temp.replace(path)
            except Exception as exc:
                self.errors = (self.errors + [str(exc)])[-4:]

    def poll(self, now):

        if now < self.poll_at:
            return
        self.poll_at = now + 1
        request = self.root / ("request_" + self.monitor + ".json")
        if not request.exists():
            return
        try:
            spec = json.loads(request.read_text())
            request.unlink()
            name = spec["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", name):
                raise ValueError("Invalid capture id")
            if self.active:
                raise ValueError("Evidence capture already active")
            folder = self.root / (name + "_" + self.monitor)
            if folder.exists():
                raise ValueError("Evidence capture id already exists")
            self.active = dict(
                folder=folder,
                end=now + min(30.0, max(1.0, float(spec.get("seconds", 10)))),
                every=max(1, int(spec.get("pixel_every_n_frames", 6))),
                pixels=bool(spec.get("pixels", False)),
                frames=0,
                raw_offered=0,
                pending=[],
            )
            self._offer(
                (
                    "json",
                    folder / "request.json",
                    dict(
                        spec,
                        started_monotonic=now,
                        pixel_format="RGB888",
                        size=[512, 288],
                        camera_cpu_transforms=False,
                    ),
                )
            )
        except Exception as exc:
            self.errors = (self.errors + [str(exc)])[-4:]

    def add(self, source, captured, presented, rows, rejected, rgb=None):
        record = dict(
            display_source_frame_id=int(source),
            preview_capture_received_monotonic=captured,
            display_submit_return_monotonic=presented,
            results=rows,
            rejected=rejected,
        )
        self.samples.append(record)
        active = self.active
        if not active:
            return
        active["frames"] += 1
        if (
            active["pixels"]
            and rgb is not None
            and active["frames"] % active["every"] == 0
        ):

            if not self.work.full():
                name = f"preview_{int(source):010d}.rgb"
                record["preview_file"] = name
                if self._offer(("raw", active["folder"] / name, rgb.tobytes())):
                    active["raw_offered"] += 1
            else:
                self.dropped += 1
        active["pending"].append(record)
        if len(active["pending"]) >= 30 or presented >= active["end"]:
            self._offer(
                ("append", active["folder"] / "frames.jsonl", active["pending"])
            )
            active["pending"] = []
        if presented >= active["end"]:
            self._offer(
                (
                    "complete",
                    active["folder"] / "complete.json",
                    dict(
                        frames=active["frames"],
                        raw_offered=active["raw_offered"],
                        ended_monotonic=presented,
                        queue_dropped_total=self.dropped,
                        errors=self.errors,
                        physical_scanout_measured=False,
                    ),
                )
            )
            self.active = None

    def report(self, stats):
        self._offer(
            (
                "json",
                self.root / ("latest_" + self.monitor + ".json"),
                dict(
                    stats=stats,
                    samples=list(self.samples),
                    dropped=self.dropped,
                    errors=self.errors,
                    capture_active=self.active is not None,
                ),
            )
        )

    def close(self):
        try:
            self.work.put(None, timeout=1)
        except queue.Full:
            return
        self.thread.join(3)
