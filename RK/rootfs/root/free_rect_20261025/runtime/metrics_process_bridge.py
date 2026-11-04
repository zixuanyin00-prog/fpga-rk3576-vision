from pathlib import Path
import os
import pickle
import queue
import subprocess
import sys
import threading
import time

SCRIPT = Path(__file__).with_name("metrics_compute_process.py")


class MetricsProcess:
    def __init__(self, commit, configure=True, parent_cpus=(0, 1, 2)):
        self.commit = commit
        self.configure = configure
        self.work = queue.Queue(1)
        self.lock = threading.Lock()
        self.busy = False
        self.closed = False
        self.error = None
        self.accepted = 0
        self.completed = 0
        self.skipped_busy = 0
        self.process = None
        self.reader = None
        self.alive = False
        self.parent_cpus = tuple(parent_cpus)
        self.parent_threads = {}
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="metrics-ipc-feeder", daemon=True
        )
        self.thread.start()

    def available(self):
        with self.lock:
            return (
                self.ready.is_set()
                and not self.busy
                and not self.closed
                and self.error is None
            )

    def register_thread(self, role):
        if self.configure and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, set(self.parent_cpus))
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        status = dict(
            tid=threading.get_native_id(),
            cpus=(
                sorted(os.sched_getaffinity(0))
                if hasattr(os, "sched_getaffinity")
                else None
            ),
            nice=(
                os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
                if hasattr(os, "getpriority")
                else None
            ),
        )
        with self.lock:
            self.parent_threads[role] = status

    def offer(self, value):
        with self.lock:
            if self.closed or self.error or not self.ready.is_set():
                return False
            if self.busy:
                self.skipped_busy += 1
                return False
            self.busy = True
            self.accepted += 1
            self.work.put_nowait(value)
            return True

    def snapshot(self):
        with self.lock:
            return dict(
                backend="dedicated_statistics_process",
                pid=self.process.pid if self.process else None,
                ready=self.ready.is_set(),
                alive=self.alive,
                in_flight=self.busy,
                accepted=self.accepted,
                completed=self.completed,
                busy_ticks=self.skipped_busy,
                error=self.error,
                closed=self.closed,
                model_loaded=False,
                all_window_samples_preserved=True,
                parent_cpus=list(self.parent_cpus),
                parent_threads=dict(self.parent_threads),
                child_cpus=[0, 1, 2] if self.configure else None,
            )

    def close(self, timeout=3.0):
        enqueue = False
        with self.lock:
            if not self.closed:
                self.closed = True
                enqueue = True
        deadline = time.monotonic() + timeout
        if enqueue:
            try:
                self.work.put(None, timeout=max(0.0, deadline - time.monotonic()))
            except queue.Full:
                self.error = self.error or "Statistics feeder did not accept shutdown"
                if self.process and self.process.poll() is None:
                    self.process.kill()
                return False
        self.thread.join(max(0.0, deadline - time.monotonic()))
        if self.reader:
            self.reader.join(max(0.0, deadline - time.monotonic()))
        if self.thread.is_alive() or (self.reader and self.reader.is_alive()):
            self.error = (
                self.error or "Statistics child did not drain before shutdown timeout"
            )
            if self.process and self.process.poll() is None:
                self.process.kill()
            return False
        return self.error is None and self.finished.is_set()

    def _read(self):
        try:
            self.register_thread("reader")
            while True:
                message = pickle.load(self.process.stdout)
                if message.get("ready"):
                    self.ready.set()
                    continue
                if message.get("final"):
                    self.finished.set()
                    break
                if message.get("error"):
                    raise RuntimeError(message["error"])
                self.commit(message["cache"])
                with self.lock:
                    self.completed += 1
                    self.busy = False
        except EOFError:
            if not self.finished.is_set():
                self.error = (
                    self.error or "Statistics child exited before final acknowledgement"
                )
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.ready.set()
            self.alive = False
            self.process.stdout.close()
            if not self.finished.is_set() and self.process.poll() is None:
                self.process.kill()
            code = self.process.wait()
            if code:
                self.error = self.error or "Statistics child exited with code " + str(
                    code
                )

    def _run(self):
        try:
            self.register_thread("feeder")
            options = dict(
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                bufsize=0,
            )
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(SCRIPT), "1" if self.configure else "0"],
                **options
            )
            self.alive = True
            self.reader = threading.Thread(
                target=self._read, name="metrics-ipc-reader", daemon=True
            )
            self.reader.start()
            while True:
                request = self.work.get()
                pickle.dump(request, self.process.stdin, protocol=5)
                if request is None:
                    break
            self.process.stdin.close()
        except Exception as exc:
            self.error = str(exc)
            self.ready.set()
