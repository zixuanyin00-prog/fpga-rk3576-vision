from collections import Counter
from pathlib import Path
import os
import pickle
import queue
import subprocess
import sys
import threading
import time

WRITER_SCRIPT = Path(__file__).with_name("outcome_writer_process.py")


class ProcessStore:
    def __init__(
        self,
        folder,
        configure=True,
        max_bytes=512 * 1024**2,
        min_free_bytes=256 * 1024**2,
    ):
        self.folder = Path(folder)
        self.configure = configure
        self.max_bytes = max_bytes
        self.min_free_bytes = min_free_bytes
        self.work = queue.Queue(1024)
        self.stats = Counter()
        self.error = None
        self.accepted = 0
        self.closed = False
        self.process = None
        self.reader = None
        self.ready_event = threading.Event()
        self.cached = dict(
            saved_events=0,
            duplicate_events=0,
            storage_limited_events=0,
            processed_events=0,
            worker_alive=False,
            stored_bytes=0,
            storage_paused=False,
        )
        self.final = None
        self.thread = threading.Thread(
            target=self._run, name="outcome-ipc-feeder", daemon=True
        )
        self.thread.start()

    def offer(self, record):
        if self.closed or self.error:
            self.stats["dropped_events"] += 1
            return False
        try:
            self.work.put_nowait(record)
        except queue.Full:
            self.stats["dropped_events"] += 1
            return False
        self.accepted += 1
        return True

    def snapshot(self):
        cached = self.cached
        return dict(
            cached,
            **self.stats,
            backend="dedicated_outcome_writer_process",
            writer_process_pid=self.process.pid if self.process else None,
            pending=max(0, self.accepted - cached.get("processed_events", 0)),
            parent_queue_pending=self.work.qsize(),
            accepted_events=self.accepted,
            error=self.error or cached.get("error"),
            folder=str(self.folder),
            quota_bytes=self.max_bytes,
            minimum_free_bytes=self.min_free_bytes,
            json_and_disk_outside_inference_process=True,
            all_accepted_events_durable=(self.final or {}).get(
                "all_accepted_events_durable"
            )
        )

    def wait_ready(self, timeout=15.0):
        self.ready_event.wait(timeout)
        return bool(self.cached.get("ready") and self.error is None)

    def close(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        if not self.closed:
            self.closed = True
            try:
                self.work.put(None, timeout=max(0.0, deadline - time.monotonic()))
            except queue.Full:
                self.error = "Outcome IPC queue did not drain at shutdown"
                return False
        self.thread.join(max(0.0, deadline - time.monotonic()))
        if self.reader:
            self.reader.join(max(0.0, deadline - time.monotonic()))
        if self.thread.is_alive() or (self.reader and self.reader.is_alive()):
            self.error = "Outcome writer did not acknowledge durable drain before shutdown timeout"
            return False
        durable = bool(
            self.final
            and self.final.get("all_accepted_events_durable")
            and self.final.get("processed_events") == self.accepted
        )
        if not durable and not self.error:
            self.error = "Outcome writer did not durably save every accepted event"
        return durable and self.error is None

    def _read_status(self):
        try:
            while True:
                message = pickle.load(self.process.stdout)
                self.cached = message["status"]
                if self.cached.get("ready") or self.cached.get("error"):
                    self.ready_event.set()
                if self.cached.get("error"):
                    self.error = self.cached["error"]
                if message.get("final"):
                    self.final = self.cached
                    break
        except EOFError:
            if self.final is None:
                self.error = (
                    self.error
                    or "Outcome writer exited without durable drain acknowledgement"
                )
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.ready_event.set()
            self.process.stdout.close()
            code = self.process.wait()
            self.cached = dict(self.cached, worker_alive=False, writer_exit_code=code)
            if code:
                self.error = (
                    self.error or "Outcome writer process exited with code " + str(code)
                )

    def _run(self):
        try:
            if self.configure and hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, {0, 1, 2})
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
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
                    str(WRITER_SCRIPT),
                    str(self.folder),
                    "1" if self.configure else "0",
                    str(self.max_bytes),
                    str(self.min_free_bytes),
                ],
                **options
            )
            self.reader = threading.Thread(
                target=self._read_status, name="outcome-ipc-status", daemon=True
            )
            self.reader.start()
            while True:
                record = self.work.get()
                pickle.dump(record, self.process.stdin, protocol=5)
                if record is None:
                    break
            self.process.stdin.close()
        except Exception as exc:
            self.error = str(exc)
            self.ready_event.set()
