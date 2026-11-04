from collections import deque
from contextlib import contextmanager
import sys
import threading
import time


class StateLock:
    def __init__(self):
        self._lock = threading.RLock()
        self.owner = None
        self.depth = 0
        self.started = 0.0
        self.label = None
        self.worst_hold = None
        self.worst_wait = None
        self.holds = deque(maxlen=32)
        self.acquisitions = 0

    def acquire(self, blocking=True, timeout=-1):
        at = time.monotonic()
        ok = self._lock.acquire(blocking, timeout)
        if not ok:
            return False
        now = time.monotonic()
        tid = threading.get_native_id()
        if self.owner == tid:
            self.depth += 1
        else:
            self.owner = tid
            self.depth = 1
            self.started = now
            frame = sys._getframe(1)
            if frame.f_code.co_name == "__enter__":
                frame = frame.f_back
            self.label = frame.f_code.co_name
            wait = dict(
                milliseconds=(now - at) * 1000,
                acquired_monotonic=now,
                caller=self.label,
                thread_id=tid,
            )
            if (
                self.worst_wait is None
                or wait["milliseconds"] > self.worst_wait["milliseconds"]
            ):
                self.worst_wait = wait
            self.acquisitions += 1
        return True

    def release(self):
        if self.owner != threading.get_native_id():
            raise RuntimeError("State lock released by non-owner")
        self.depth -= 1
        if self.depth == 0:
            now = time.monotonic()
            item = dict(
                milliseconds=(now - self.started) * 1000,
                started_monotonic=self.started,
                ended_monotonic=now,
                caller=self.label,
                thread_id=self.owner,
            )
            if (
                self.worst_hold is None
                or item["milliseconds"] > self.worst_hold["milliseconds"]
            ):
                self.worst_hold = item
            if item["milliseconds"] >= 20:
                self.holds.append(item)
            self.owner = None
            self.label = None
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

    def _is_owned(self):
        return self.owner == threading.get_native_id()

    def snapshot(self):
        return dict(
            acquisitions=self.acquisitions,
            worst_hold=self.worst_hold,
            worst_wait=self.worst_wait,
            recent_holds_over_20ms=list(self.holds),
            measurement="outermost state-lock interval; real monotonic clock",
        )


@contextmanager
def transaction(pipeline):

    with pipeline.control_io_lock:

        def begin():
            outer = pipeline.control_io_depth == 0
            if outer:
                pipeline.tracker.reset("evidence")

            def commit():
                pipeline.control_io_depth += 1
                if outer:
                    pipeline.control_io_active = True
                    pipeline.control_io_revision += 1
                    pipeline.control_io_started = time.monotonic()
                    pipeline.control_io_transactions += 1
                    pipeline._invalidate()
                    pipeline.rows = []
                    pipeline.last_time = 0.0

            pipeline.refresh_tracking_snapshot(commit)

        pipeline.tracking_control(begin)
        try:
            yield
        finally:

            def finish():
                with pipeline.lock:
                    pipeline.control_io_depth -= 1
                    if pipeline.control_io_depth == 0:
                        pipeline.control_io_revision += 1
                        elapsed = (
                            time.monotonic() - pipeline.control_io_started
                        ) * 1000
                        pipeline.control_io_last_ms = elapsed
                        pipeline.control_io_max_ms = max(
                            pipeline.control_io_max_ms, elapsed
                        )
                        pipeline.control_io_active = False
                        pipeline.result_event.set()

            pipeline.tracking_control(finish)


def snapshot(pipeline):
    return dict(
        state_lock=pipeline.lock.snapshot(),
        control_io=dict(
            active=pipeline.control_io_active,
            revision=pipeline.control_io_revision,
            transactions=pipeline.control_io_transactions,
            last_ms=pipeline.control_io_last_ms,
            maximum_ms=pipeline.control_io_max_ms,
            hardware_wait_outside_state_lock=True,
        ),
        status_storage=pipeline.status_writer.snapshot(),
    )
