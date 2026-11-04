from collections import Counter
from pathlib import Path
import copy
import math
import os
import pickle
import queue
import subprocess
import sys
import threading
import time

SPOOL = Path("/userdata/vision_terminal_logs_v1")
CACHE_BYTES = 16 * 1024 * 1024
CACHE_ENTRIES = 128
DESCRIPTOR_FIELDS = (
    "source_frame_id",
    "request_id",
    "channel",
    "fpga_epoch",
    "processing_revision",
    "enhance_revision",
    "capture_received_monotonic",
    "transport_dequeued_monotonic",
    "python_copy_returned_monotonic",
    "processing_config",
)


class FailureEvents:
    def __init__(self, folder=SPOOL, configure=True):
        self.folder = Path(folder)
        self.configure = configure
        self.cache = {}
        self.used = 0
        self.accounting = threading.Lock()
        self.stats = Counter()
        self.work = queue.Queue(64)
        self.closed = False
        self.error = None
        self.final = None
        self.cached = {}
        self.process = None
        self.reader = None
        self.accepted = 0
        self.thread = threading.Thread(
            target=self._run, name="terminal-log-feeder", daemon=True
        )
        self.thread.start()

    def observe(self, life, track, frame, source, box):
        identity = life["object_id"]
        if self.error:
            life["terminal_image_reason"] = "writer_unavailable"
            return
        if (
            identity in self.cache
            or life.get("terminal_image_attempted")
            or frame is None
        ):
            return
        left, top, right, bottom = box
        width = frame.rgb.shape[1]
        height = frame.rgb.shape[0]
        if (
            left < frame.x
            or top < frame.y
            or right > frame.x + width
            or bottom > frame.y + height
            or right <= left
            or bottom <= top
        ):
            life["terminal_image_reason"] = "no_complete_real_box"
            return
        life["terminal_image_attempted"] = True
        metadata = dict(getattr(frame, "failure_event_context", {}) or {})
        metadata.update(
            {
                name: getattr(frame, name)
                for name in DESCRIPTOR_FIELDS
                if hasattr(frame, name)
            }
        )
        required = (
            "boot_id",
            "runtime_session_id",
            "firmware_sha256",
            "fpga_epoch",
            "processing_revision",
            "enhance_revision",
            "processing_config",
            "source_frame_id",
        )
        missing = [name for name in required if metadata.get(name) is None]
        if missing or int(metadata.get("source_frame_id", -1)) != int(source):
            life["terminal_image_reason"] = "source_metadata_unavailable"
            life["terminal_image_missing_fields"] = missing
            return
        x0 = max(0, math.floor(left - frame.x))
        y0 = max(0, math.floor(top - frame.y))
        x1 = min(width, math.ceil(right - frame.x))
        y1 = min(height, math.ceil(bottom - frame.y))
        count = (x1 - x0) * (y1 - y0) * 3
        if not self.accounting.acquire(False):
            life["terminal_image_reason"] = "cache_accounting_busy"
            self.stats["image_cache_busy"] += 1
            return
        try:
            if self.used + count > CACHE_BYTES or len(self.cache) >= CACHE_ENTRIES:
                life["terminal_image_reason"] = "lifetime_cache_limit"
                self.stats["image_cache_limited"] += 1
                return
            self.used += count
        finally:
            self.accounting.release()
        raw = frame.rgb[y0:y1, x0:x1].tobytes(order="C")
        metadata = copy.deepcopy(metadata)
        image = dict(
            available=True,
            reason=None,
            file="frame.rgb",
            format="RGB888",
            size=[x1 - x0, y1 - y0],
            origin=[frame.x + x0, frame.y + y0],
            source_frame_id=int(source),
            metadata=metadata,
            observed_box=list(box),
            capture_policy="first_complete_real_observation",
            captured_registered=bool(track.get("observed_registered")),
            captured_identity_trusted=bool(life.get("last_identity_trusted")),
        )
        self.cache[identity] = (raw, image)
        life["terminal_image_reason"] = None
        self.stats["images_cached"] += 1

    def discard(self, identity):
        item = self.cache.pop(identity, None)
        if item is not None:
            with self.accounting:
                self.used -= len(item[0])

    def offer(self, record):
        item = self.cache.pop(record["object_id"], None)
        if item is None:
            image = dict(
                available=False,
                reason=record.get("terminal_image_reason")
                or "no_eligible_real_observation",
                missing_fields=record.get("terminal_image_missing_fields", []),
                file=None,
                format="RGB888",
            )
            raw = None
            self.stats["image_unavailable_events"] += 1
        else:
            raw, image = item
        if self.closed or self.error:
            self.stats["dropped_events"] += 1
            if raw is not None:
                with self.accounting:
                    self.used -= len(raw)
            return False
        try:
            self.work.put_nowait((record, image, raw))
        except queue.Full:
            self.stats["dropped_events"] += 1
            self.stats["queue_full_events"] += 1
            if raw is not None:
                with self.accounting:
                    self.used -= len(raw)
            return False
        self.accepted += 1
        return True

    def snapshot(self):
        values = dict(self.cached)
        values.update(self.stats)
        values.update(
            enabled=True,
            queued=self.work.qsize(),
            cache_used_bytes=self.used,
            cache_limit_bytes=CACHE_BYTES,
            cache_limit_entries=CACHE_ENTRIES,
            active_cached_images=len(self.cache),
            accepted_events=self.accepted,
            feeder_alive=self.thread.is_alive(),
            worker_pid=self.process.pid if self.process else None,
            error=self.error or self.cached.get("error"),
            status_path=str(self.folder / "status.json"),
            dropped_events=self.stats.get("dropped_events", 0)
            + self.cached.get("dropped_events", 0),
        )
        return values

    def close(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        if not self.closed:
            self.closed = True
            for identity in list(self.cache):
                self.discard(identity)
            try:
                self.work.put(None, timeout=max(0, deadline - time.monotonic()))
            except queue.Full:
                self.error = "Terminal log queue did not admit shutdown sentinel"
                return False
        self.thread.join(max(0, deadline - time.monotonic()))
        if self.reader:
            self.reader.join(max(0, deadline - time.monotonic()))
        return bool(self.final and self.final.get("drained") and not self.error)

    def _read(self):
        try:
            while True:
                value = pickle.load(self.process.stdout)
                self.cached = value
                if value.get("final"):
                    self.final = value
                    break
        except EOFError:
            self.error = self.error or "Terminal writer exited without drain receipt"
        finally:
            self.process.stdout.close()
            code = self.process.wait()
            if code:
                self.error = self.error or "Terminal writer exited with code " + str(
                    code
                )

    def _run(self):
        try:
            if self.configure and hasattr(os, "sched_setaffinity"):

                os.sched_setaffinity(0, {0, 1, 2})
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 0)
                os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
            options = dict(
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                close_fds=True,
            )
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    str(Path(__file__).with_name("failure_event_writer.py")),
                    str(self.folder),
                    "1" if self.configure else "0",
                ],
                **options
            )
            self.reader = threading.Thread(
                target=self._read, name="terminal-log-status", daemon=True
            )
            self.reader.start()
            while True:
                item = self.work.get()
                pickle.dump(item, self.process.stdin, protocol=5)
                if item is None:
                    break
                raw = item[2]
                if raw is not None:
                    with self.accounting:
                        self.used -= len(raw)
                del item, raw
            self.process.stdin.close()
        except (OSError, EOFError, ValueError) as exc:
            self.error = str(exc)
