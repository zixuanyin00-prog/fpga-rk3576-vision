from pathlib import Path
import json
import os
import pickle
import subprocess
import sys
import threading
import time
import uuid


class Writer:
    def __init__(self):
        self.cv = threading.Condition()
        self.pending = {}
        self.closed = False
        self.error = None
        self.written = 0
        self.coalesced = 0
        self.last_ms = 0.0
        self.max_ms = 0.0
        self.worker_last_ms = 0.0
        self.process = None
        self.thread = threading.Thread(
            target=self._run, name="status-storage", daemon=True
        )
        self.thread.start()

    def offer(self, path, value):
        with self.cv:
            if self.closed:
                raise RuntimeError("Status storage is closed")
            path = Path(path)
            self.coalesced += path in self.pending
            self.pending[path] = value
            self.cv.notify()

    def remove(self, path):
        self.offer(path, None)

    def snapshot(self):
        return dict(
            pending=len(self.pending),
            written=self.written,
            coalesced=self.coalesced,
            error=self.error,
            last_storage_ms=self.last_ms,
            maximum_storage_ms=self.max_ms,
            disk_io_outside_state_lock=True,
            json_outside_inference_process=True,
            worker_pid=self.process.pid if self.process else None,
            worker_last_storage_ms=self.worker_last_ms,
        )

    def _run(self):
        try:
            if hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, {0, 1, 2})

                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 0)
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
                [sys.executable, "-u", str(Path(__file__).resolve()), "--worker"],
                **options
            )
            while True:
                with self.cv:
                    self.cv.wait_for(lambda: self.closed or self.pending)
                    if not self.pending and self.closed:
                        break
                    path = next(iter(self.pending))
                    value = self.pending.pop(path)
                at = time.monotonic()
                pickle.dump((str(path), value), self.process.stdin, protocol=5)
                response = pickle.load(self.process.stdout)
                if response["error"]:
                    raise RuntimeError(response["error"])
                self.worker_last_ms = response["storage_ms"]
                self.last_ms = (time.monotonic() - at) * 1000
                self.max_ms = max(self.max_ms, self.last_ms)
                self.written += 1
            pickle.dump(None, self.process.stdin, protocol=5)
            if not pickle.load(self.process.stdout)["closed"]:
                raise RuntimeError("Status worker did not acknowledge drain")
            self.process.stdin.close()
            self.process.wait(timeout=3)
            if self.process.returncode:
                raise RuntimeError(
                    "Status worker exited with code " + str(self.process.returncode)
                )
        except Exception as exc:
            self.error = str(exc)
        finally:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                self.process.wait(timeout=3)
                for stream in (self.process.stdin, self.process.stdout):
                    if not stream.closed:
                        stream.close()

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()
        self.thread.join(5.0)
        if self.thread.is_alive() and self.process is not None:

            self.process.terminate()
            self.thread.join(1.0)
        if self.thread.is_alive() or self.error:
            raise RuntimeError("Status storage failed to drain: " + str(self.error))


def storage_worker():
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {0, 1, 2})
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    while True:
        item = pickle.load(sys.stdin.buffer)
        if item is None:
            pickle.dump(dict(closed=True), sys.stdout.buffer, protocol=5)
            sys.stdout.buffer.flush()
            return
        name, value = item
        path = Path(name)
        at = time.monotonic()
        try:
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
                temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                temp.replace(path)
            response = dict(error=None, storage_ms=(time.monotonic() - at) * 1000)
        except Exception as exc:
            response = dict(error=str(exc), storage_ms=(time.monotonic() - at) * 1000)
        pickle.dump(response, sys.stdout.buffer, protocol=5)
        sys.stdout.buffer.flush()
        if response["error"]:
            return


if __name__ == "__main__":
    storage_worker()
