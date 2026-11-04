from pathlib import Path
import mmap
import ctypes as C
import fcntl
import os
import pickle
import subprocess
import sys
import threading
import time
import numpy as np
from tracking_rpc import RemoteTracker
from failure_event_cache import DESCRIPTOR_FIELDS

SCRIPT = Path(__file__).with_name("tracking_shared_process.py")
CAPACITY = 518400 * 3


class SharedTracker(RemoteTracker):
    def __init__(
        self,
        initial_state=None,
        parent_context=None,
        *,
        cpu=None,
        configure=True,
        outcome_folder=None,
        timeout=8.0
    ):
        if cpu is None:
            cpu = int(os.environ.get("VISION_TRACKER_CPU", "7"))
        if cpu not in (6, 7):
            raise ValueError("Tracking CPU must be big core6 or7")
        self.timeout = float(timeout)
        self.sequence = 0
        self._rpc_lock = threading.Lock()
        self._view = None
        self._error = None
        self._closed = False
        self._final = None
        self._stats = {}
        self.context = dict(parent_context or {})
        self.stderr_tail = []
        self._exchange = C.CDLL(
            str(Path(__file__).with_name("libtracking_exchange.so"))
        )
        self._exchange.tracking_exchange.argtypes = [
            C.c_int,
            C.c_int,
            C.c_void_p,
            C.c_size_t,
            C.POINTER(C.c_void_p),
            C.POINTER(C.c_size_t),
            C.POINTER(C.c_size_t),
            C.c_double,
        ]
        self._exchange.tracking_exchange.restype = C.c_int
        self._exchange.tracking_exchange_free.argtypes = [C.c_void_p]
        self._exchange.tracking_exchange_free.restype = None
        self._response = C.c_void_p()
        self._response_capacity = C.c_size_t()
        self._response_size = C.c_size_t()
        self._fd = os.memfd_create("vision-tracker-native-rgb", os.MFD_CLOEXEC)
        os.ftruncate(self._fd, CAPACITY)
        self._mapping = mmap.mmap(self._fd, CAPACITY, access=mmap.ACCESS_WRITE)
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                bufsize=0,
                pass_fds=(self._fd,),
            )
            self._pipe_capacity = {
                name: fcntl.fcntl(pipe.fileno(), fcntl.F_SETPIPE_SZ, 65536)
                for name, pipe in (
                    ("request", self.process.stdin),
                    ("response", self.process.stdout),
                )
            }
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)

            def drain_errors():
                for line in self.process.stderr:
                    self.stderr_tail.append(line.decode("utf-8", "replace").strip())
                    if len(self.stderr_tail) > 12:
                        del self.stderr_tail[:-12]

            self.stderr_thread = threading.Thread(
                target=drain_errors, name="shared-tracker-stderr", daemon=True
            )
            self.stderr_thread.start()
            self._request(
                "init",
                dict(
                    state=initial_state or {},
                    context=self.context,
                    cpu=cpu,
                    configure=bool(configure),
                    outcome_folder=(
                        str(outcome_folder) if outcome_folder is not None else None
                    ),
                    shared_fd=self._fd,
                    shared_capacity=CAPACITY,
                ),
                timeout=20.0,
            )
        except BaseException:
            if getattr(self, "process", None) is not None:
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait()
                if getattr(self, "stderr_thread", None) is not None:
                    self.stderr_thread.join(2.0)
                for pipe in (
                    self.process.stdin,
                    self.process.stdout,
                    self.process.stderr,
                ):
                    if not pipe.closed:
                        pipe.close()
            self._exchange.tracking_exchange_free(self._response)
            self._response = C.c_void_p()
            self._mapping.close()
            os.close(self._fd)
            self._fd = None
            raise

    def _request(self, operation, value=None, *, timeout=None, frame=None):
        if self._error:
            raise RuntimeError("Shared tracking failed: " + self._error)
        if self._closed:
            raise RuntimeError("Shared tracker is closed")
        if not self._rpc_lock.acquire(False):
            raise RuntimeError(
                "Concurrent shared tracking violates the serial collector contract"
            )
        try:
            started = time.monotonic()
            cpu_started = time.thread_time()
            copy_wall = copy_cpu = 0.0
            payload_bytes = 0
            if frame is not None:
                copy_started = time.monotonic()
                copy_cpu_started = time.thread_time()
                target = np.ndarray(frame.rgb.shape, np.uint8, buffer=self._mapping)
                np.copyto(target, frame.rgb, casting="no")
                del target
                copy_wall = (time.monotonic() - copy_started) * 1000
                copy_cpu = (time.thread_time() - copy_cpu_started) * 1000
                payload_bytes = frame.rgb.nbytes
            self.sequence += 1
            sequence = self.sequence
            data = pickle.dumps(
                dict(sequence=sequence, operation=operation, value=value), protocol=5
            )
            deadline = started + (self.timeout if timeout is None else timeout)
            wire = len(data).to_bytes(8, "little") + data
            status = self._exchange.tracking_exchange(
                self.process.stdin.fileno(),
                self.process.stdout.fileno(),
                wire,
                len(wire),
                C.byref(self._response),
                C.byref(self._response_capacity),
                C.byref(self._response_size),
                deadline,
            )
            if status:
                raise OSError(-status, os.strerror(-status))
            size = self._response_size.value
            response = pickle.loads(C.string_at(self._response, size))
            if response["sequence"] != sequence:
                raise RuntimeError("Shared tracking response sequence mismatch")
            if response.get("error"):
                raise RuntimeError(response["error"])
            self._view = response["view"]
            stats = dict(
                response.get("stats", {}),
                rpc_wall_ms=(time.monotonic() - started) * 1000,
                parent_rpc_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                request_bytes=len(data),
                response_bytes=size,
                shared_copy_wall_ms=copy_wall,
                shared_copy_cpu_ms=copy_cpu,
                shared_image_bytes=payload_bytes,
                rgb_pipe_bytes=0,
                sequence=sequence,
                child_pid=self.process.pid,
                native_fd_exchange=True,
            )
            self._stats = stats
            return response, stats
        except Exception as exc:
            self._error = str(exc)
            if self.process.poll() is None:
                self.process.kill()
            raise RuntimeError(
                "Shared tracker failed; trajectories were not restarted: " + str(exc)
            ) from exc
        finally:
            self._rpc_lock.release()

    def observe(
        self,
        frame,
        objects,
        source,
        now,
        hits,
        threshold,
        *,
        failure_event_context=None
    ):
        rgb = frame.rgb
        if (
            rgb.dtype != np.uint8
            or rgb.ndim != 3
            or rgb.shape[2] != 3
            or not 0 < rgb.shape[0] * rgb.shape[1] <= 518400
        ):
            raise ValueError("Shared tracker needs native uint8 RGB up to518400pixels")
        started = time.monotonic()
        value = dict(
            shape=rgb.shape,
            x=int(frame.x),
            y=int(frame.y),
            capture_received_monotonic=frame.capture_received_monotonic,
            source=int(source),
            now=float(now),
            objects=objects,
            hits=hits,
            threshold=threshold,
        )
        value["failure_event_context"] = dict(failure_event_context or {})
        value["frame_descriptor"] = {
            name: getattr(frame, name)
            for name in DESCRIPTOR_FIELDS
            if hasattr(frame, name)
        }
        response, stats = self._request("observe", value, frame=frame)
        if response["source"] != int(source):
            self._error = "Shared tracking source frame mismatch"
            if self.process.poll() is None:
                self.process.kill()
            raise RuntimeError(self._error)
        stats.update(
            parent_frame_pack_cpu_ms=stats["shared_copy_cpu_ms"],
            parent_frame_pack_wall_ms=stats["shared_copy_wall_ms"],
            observe_rpc_wall_ms=(time.monotonic() - started) * 1000,
        )
        return response["rows"], response["failures"], self._view, stats

    def diagnostics(self):
        return dict(
            backend="shared_native_rgb_tracking_process",
            pid=self.process.pid,
            error=self._error,
            closed=self._closed,
            last_rpc=dict(self._stats),
            original_pixels_transferred=False,
            original_pixels_shared=True,
            shared_capacity_bytes=CAPACITY,
            memfd_open=self._fd is not None,
            model_loaded=False,
            auto_restart=False,
            pipe_capacity_bytes=dict(self._pipe_capacity),
            exchange_backend="single_CDLL_bounded_pipe_exchange",
        )

    def close(self, reason="runtime_stopped", timeout=5.0):
        result = super().close(reason, timeout)
        if self._fd is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=timeout)
            self.stderr_thread.join(timeout)
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                if not pipe.closed:
                    pipe.close()
            self._exchange.tracking_exchange_free(self._response)
            self._response = C.c_void_p()
            self._mapping.close()
            os.close(self._fd)
            self._fd = None
        return result
