from pathlib import Path
import os
import pickle
import select
import subprocess
import sys
import threading
import time

SCRIPT = Path(__file__).with_name("tracking_compute_process.py")


class RemoteTracker:
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
        options = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            bufsize=0,
        )
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen([sys.executable, "-u", str(SCRIPT)], **options)
        if os.name != "nt":
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        self.stderr_tail = []

        def drain_errors():
            for line in self.process.stderr:
                self.stderr_tail.append(line.decode("utf-8", "replace").strip())
                if len(self.stderr_tail) > 12:
                    del self.stderr_tail[:-12]

        self.stderr_thread = threading.Thread(
            target=drain_errors, name="tracking-child-stderr", daemon=True
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
            ),
            timeout=20.0,
        )

    def _write_all(self, data, deadline):
        remaining = memoryview(data)
        while remaining:
            if os.name != "nt":
                if not select.select(
                    [], [self.process.stdin], [], max(0.0, deadline - time.monotonic())
                )[1]:
                    raise TimeoutError("Tracking request write timed out")
                try:
                    written = os.write(self.process.stdin.fileno(), remaining)
                except BlockingIOError:
                    continue
            else:
                written = self.process.stdin.write(remaining)
            if not written:
                raise EOFError("Tracking request pipe closed")
            remaining = remaining[written:]

    def _read_exact(self, size, deadline):
        chunks = []
        remaining = size
        while remaining:
            if os.name != "nt":
                if not select.select(
                    [self.process.stdout], [], [], max(0.0, deadline - time.monotonic())
                )[0]:
                    raise TimeoutError("Tracking response timed out")
                try:
                    data = os.read(self.process.stdout.fileno(), remaining)
                except BlockingIOError:
                    continue
            else:
                data = self.process.stdout.read(remaining)
            if not data:
                raise EOFError("Tracking response pipe closed")
            chunks.append(data)
            remaining -= len(data)
        return b"".join(chunks)

    def _request(self, operation, value=None, *, timeout=None):
        if self._error:
            raise RuntimeError("Tracking process failed: " + self._error)
        if self._closed:
            raise RuntimeError("Tracking process is closed")
        if not self._rpc_lock.acquire(blocking=False):
            raise RuntimeError(
                "Concurrent tracking RPC violates the serial collector contract"
            )
        try:
            self.sequence += 1
            sequence = self.sequence
            started = time.monotonic()
            cpu_started = time.thread_time()
            request = dict(sequence=sequence, operation=operation, value=value)
            data = pickle.dumps(request, protocol=5)
            deadline = started + (self.timeout if timeout is None else timeout)
            self._write_all(len(data).to_bytes(8, "little") + data, deadline)

            size = int.from_bytes(self._read_exact(8, deadline), "little")
            if size > 32 * 1024 * 1024:
                raise RuntimeError("Tracking response exceeded bounded schema size")
            response = pickle.loads(self._read_exact(size, deadline))
            if response["sequence"] != sequence:
                raise RuntimeError("Tracking response sequence mismatch")
            if response.get("error"):
                raise RuntimeError(response["error"])
            self._view = response["view"]
            stats = dict(
                response.get("stats", {}),
                rpc_wall_ms=(time.monotonic() - started) * 1000,
                parent_rpc_cpu_ms=(time.thread_time() - cpu_started) * 1000,
                request_bytes=len(data),
                sequence=sequence,
                child_pid=self.process.pid,
            )
            self._stats = stats
            return response, stats
        except Exception as exc:
            self._error = str(exc)
            if self.process.poll() is None:
                self.process.kill()
            raise RuntimeError(
                "Tracking RPC failed; trajectories were not restarted: " + str(exc)
            ) from exc
        finally:
            self._rpc_lock.release()

    def observe(self, frame, objects, source, now, hits, threshold):
        packed_at = time.monotonic()
        packed_cpu = time.thread_time()
        rgb = frame.rgb
        request = dict(
            rgb=rgb.tobytes(),
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
        packing_cpu_ms = (time.thread_time() - packed_cpu) * 1000
        packing_wall_ms = (time.monotonic() - packed_at) * 1000
        response, stats = self._request("observe", request)
        if response["source"] != int(source):
            self._error = "Tracking source frame mismatch"
            if self.process.poll() is None:
                self.process.kill()
            raise RuntimeError(self._error)
        stats.update(
            parent_frame_pack_cpu_ms=packing_cpu_ms,
            parent_frame_pack_wall_ms=packing_wall_ms,
            observe_rpc_wall_ms=time.monotonic() * 1000 - packed_at * 1000,
        )
        return response["rows"], response["failures"], self._view, stats

    def reset(self, kind="evidence"):
        if kind not in ("evidence", "region", "counts"):
            raise ValueError("Unknown tracking reset kind")
        self._request("reset", kind)
        return self._view

    def bind(self, context):
        self.context = dict(context)
        self._request("bind", self.context)
        return self._view

    def counters(self):
        return self._view["ledger"]

    def snapshot(self):
        return self._view

    @property
    def counts(self):
        return self._view["class_counts"]

    @property
    def region_version(self):
        return self._view["region_version"]

    @property
    def active_count(self):
        return self._view["active_tracks"]

    def diagnostics(self):
        return dict(
            backend="dedicated_serial_tracking_process",
            pid=self.process.pid,
            error=self._error,
            closed=self._closed,
            last_rpc=dict(self._stats),
            original_pixels_transferred=True,
            model_loaded=False,
            auto_restart=False,
        )

    def close(self, reason="runtime_stopped", timeout=5.0):
        if self._closed:
            return self._final or dict(
                durable=False, error=self._error or "Tracking process already closed"
            )
        try:
            response, _ = self._request("close", reason, timeout=timeout)
            self._closed = True
            self.process.stdin.close()
            self.process.stdout.close()
            code = self.process.wait(timeout=timeout)
            self.stderr_thread.join(timeout)
            if code:
                raise RuntimeError("Tracking process exited with code " + str(code))
            self._final = response["durable_ack"]
            return self._final
        except Exception as exc:
            self._closed = True
            if self.process.poll() is None:
                self.process.kill()
            self._error = self._error or str(exc)
            return dict(durable=False, error=self._error)
