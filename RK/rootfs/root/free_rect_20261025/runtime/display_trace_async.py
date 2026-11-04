from collections import deque
from pathlib import Path
import json
import multiprocessing as mp
import os
import queue
import re
import time


def _configure_worker():

    if hasattr(os, "sched_setscheduler"):
        os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
        os.sched_setaffinity(0, {0, 1, 2})
        os.setpriority(os.PRIO_PROCESS, 0, 10)
        if (
            os.sched_getscheduler(0) != os.SCHED_OTHER
            or os.sched_getaffinity(0) != {0, 1, 2}
            or os.getpriority(os.PRIO_PROCESS, 0) != 10
        ):
            raise RuntimeError("Display evidence worker scheduling readback mismatch")


def _writer_main(work, feedback, configure=None):
    saved = {}
    errors = []

    def send(kind, data):
        try:
            feedback.put_nowait((kind, data))
        except queue.Full:
            pass

    def fail(exc):
        errors[:] = (errors + [str(exc)])[-4:]
        send("error", str(exc))

    def write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        temp.replace(path)

    try:
        (configure or _configure_worker)()
    except Exception as exc:
        fail(exc)
        return
    while True:
        item = work.get()
        if item is None:
            return
        try:
            kind, path, data = item
            path = Path(path)
            if kind == "poll":
                if not path.exists():
                    continue
                spec = json.loads(path.read_text())

                path.rename(
                    path.with_name(
                        path.stem + ".consumed_" + str(time.monotonic_ns()) + ".json"
                    )
                )
                name = spec["id"]
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", name):
                    raise ValueError("Invalid capture id")
                if data["active"]:
                    raise ValueError("Evidence capture already active")
                folder = path.parent / (name + "_" + data["monitor"])
                if folder.exists():
                    raise ValueError("Evidence capture id already exists")
                now = data["now"]
                active = dict(
                    folder=str(folder),
                    end=now + min(30.0, max(1.0, float(spec.get("seconds", 10)))),
                    every=max(1, int(spec.get("pixel_every_n_frames", 6))),
                    pixels=bool(spec.get("pixels", False)),
                )
                write_json(
                    folder / "request.json",
                    dict(
                        spec,
                        started_monotonic=now,
                        pixel_format="RGB888",
                        size=list(data["source_size"]),
                        camera_cpu_transforms=False,
                    ),
                )
                send("request", active)
            elif kind == "raw":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                saved[str(path.parent)] = saved.get(str(path.parent), 0) + 1
            elif kind == "append":
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    for row in data:
                        stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            else:
                if kind == "complete":
                    data = dict(
                        data,
                        raw_saved=saved.pop(str(path.parent), 0),
                        errors=(list(data.get("errors", [])) + errors)[-4:],
                    )
                write_json(path, data)
        except Exception as exc:
            fail(exc)


class DisplayTrace:
    def __init__(self, monitor, root=".", source_size=(512, 288)):
        self.monitor = monitor
        self.source_size = tuple(source_size)
        self.root = Path(root) / "display_alignment_evidence"
        self.samples = deque(maxlen=120)
        context = mp.get_context("spawn")
        self.work = context.Queue(8)
        self.feedback = context.Queue(8)
        self.dropped = 0
        self.errors = []
        self.saved = {}
        self.active = None
        self.poll_at = 0.0
        self.closed = False
        self.process = context.Process(
            target=_writer_main,
            args=(self.work, self.feedback),
            name="display-evidence-writer",
            daemon=True,
        )
        self.process.start()

    def _error(self, value):
        self.errors = (self.errors + [str(value)])[-4:]

    def _drain_feedback(self):
        for _ in range(8):
            try:
                kind, data = self.feedback.get_nowait()
            except (queue.Empty, ValueError, OSError):
                break
            if kind == "error":
                self._error(data)
            elif kind == "request":
                if self.active:
                    self._error("Evidence capture already active")
                    continue
                self.active = dict(
                    data,
                    folder=Path(data["folder"]),
                    frames=0,
                    raw_offered=0,
                    pending=[],
                )
        if not self.closed and self.process.exitcode is not None:
            message = "Display evidence worker exited: " + str(self.process.exitcode)
            if message not in self.errors:
                self._error(message)

    def _offer(self, item):
        if self.closed:
            self.dropped += 1
            return False
        try:
            self.work.put_nowait(item)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def poll(self, now):
        self._drain_feedback()
        if now < self.poll_at:
            return
        self.poll_at = now + 1
        self._offer(
            (
                "poll",
                self.root / ("request_" + self.monitor + ".json"),
                dict(
                    now=now,
                    monitor=self.monitor,
                    source_size=self.source_size,
                    active=self.active is not None,
                ),
            )
        )

    def add(self, source, captured, presented, rows, rejected, rgb=None):
        self._drain_feedback()
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
                if self._offer(("raw", active["folder"] / name, rgb.tobytes())):
                    record["preview_file"] = name
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
                        errors=list(self.errors),
                        physical_scanout_measured=False,
                    ),
                )
            )
            self.active = None

    def report(self, stats):
        self._drain_feedback()
        self._offer(
            (
                "json",
                self.root / ("latest_" + self.monitor + ".json"),
                dict(
                    stats=stats,
                    samples=list(self.samples),
                    dropped=self.dropped,
                    errors=list(self.errors),
                    capture_active=self.active is not None,
                ),
            )
        )

    def write_report(self, path, data):

        self._offer(("json", Path(path), data))

    def close(self):
        if self.closed:
            return

        self.work.cancel_join_thread()
        self.feedback.cancel_join_thread()
        try:
            self.work.put(None, timeout=1)
        except queue.Full:
            self._error("Display evidence shutdown queue remained full")
            return
        self.closed = True
        self.process.join(3)
        self._drain_feedback()
        if self.process.is_alive():
            self._error(
                "Display evidence writer did not finish within shutdown deadline"
            )
            return
        self.work.close()
        self.feedback.close()
