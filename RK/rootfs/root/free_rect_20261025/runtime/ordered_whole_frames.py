from collections import deque
from concurrent.futures import Future
import threading
import os
import time
from ordered_inference import OrderedInference


class OrderedWholeFrames(OrderedInference):
    def __init__(self, execute, publish, fail, capacity=None, setup=None):
        self.minimal_fifo = os.environ.get("VISION_MINIMAL60") == "1"
        if capacity is None:
            capacity = int(os.environ.get("VISION_INFERENCE_CAPACITY", "3"))
        maximum = 6 if self.minimal_fifo else 4
        if type(capacity) is not int or not 3 <= capacity <= maximum:
            raise ValueError(
                "Fresh scheduler capacity must be between 3 and " + str(maximum)
            )
        super().__init__(execute, publish, fail, capacity=capacity, setup=setup)
        self.last_admitted_source = None
        self.last_submitted_source = None
        self.duplicate_admissions = 0
        self.admission_attempts = 0
        self.admitted_count = 0
        self.started_count = 0
        self.execute_completed_count = 0
        self.valid_completed_count = 0
        self.rejected_no_pending = 0
        self.replaced_pending = 0
        self.admission_rejected_closed = 0
        self.admission_rejected_epoch = 0
        self.admission_timeouts = 0
        self.admission_wait_count = 0
        self.admission_wait_ms_total = 0.0
        self.admission_wait_ms_max = 0.0
        self.publishing_key = None
        self.skipped_sources = deque(maxlen=256)
        self.control_commands = deque()
        self.collector_ident = None
        self.control_running = False
        self.control_completed = 0
        self.control_failed = 0
        self.control_max_ms = 0.0
        self.worker_keys = [None, None]
        self.worker_ready = [False, False]
        self.account_started = None
        self.account_last = None
        reasons = (
            "initializing",
            "no_pending_input_slots_available",
            "capacity_held_by_completed_or_publishing",
            "capacity_held_by_other_running",
            "ready_pending_dispatch",
            "executing",
        )
        self.core_demand = [
            dict(core=core, seconds={name: 0.0 for name in reasons}, dispatches=0)
            for core in (0, 1)
        ]
        self.slot_seconds = {
            name: 0.0
            for name in (
                "running",
                "pending",
                "completed_waiting",
                "publishing",
                "occupied",
            )
        }
        self.time_at_occupancy = {str(depth): 0.0 for depth in range(7)}
        self.time_at_running = {str(depth): 0.0 for depth in range(3)}
        self.saturation_rejections = {"completed_or_publishing": 0, "only_running": 0}
        self.phase_samples = deque(maxlen=2048)

    def start(self):
        with self.cv:
            self.account_started = self.account_last = time.monotonic()
        super().start()

    def _account(self, now):

        if self.account_last is None:
            return
        elapsed = now - self.account_last
        self.account_last = now
        occupied = len(set(self.order) | self.active)
        publishing = (
            int(self.publishing_key in self.results)
            if self.publishing_key is not None
            else 0
        )
        completed = len(self.results) - publishing
        for name, depth in (
            ("running", len(self.active)),
            ("pending", len(self.pending)),
            ("completed_waiting", completed),
            ("publishing", publishing),
            ("occupied", occupied),
        ):
            self.slot_seconds[name] += elapsed * depth
        self.time_at_occupancy[str(occupied)] = (
            self.time_at_occupancy.get(str(occupied), 0.0) + elapsed
        )
        self.time_at_running[str(len(self.active))] += elapsed
        for core in (0, 1):
            if self.worker_keys[core] is not None:
                reason = "executing"
            elif not self.worker_ready[core]:
                reason = "initializing"
            elif self.pending:
                reason = "ready_pending_dispatch"
            elif occupied >= self.capacity:
                reason = (
                    "capacity_held_by_completed_or_publishing"
                    if self.results
                    else "capacity_held_by_other_running"
                )
            else:
                reason = "no_pending_input_slots_available"
            self.core_demand[core]["seconds"][reason] += elapsed

    def is_collector_thread(self):
        return self.collector_ident == threading.get_ident()

    def call_control(self, callback):

        future = Future()
        with self.cv:
            if self.closed:
                future.set_exception(RuntimeError("Inference scheduler is closed"))
                return future
            inline = self.is_collector_thread()
            if not inline:
                self.control_commands.append((future, callback))
                self.cv.notify_all()
                return future
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(callback())
            except BaseException as exc:
                future.set_exception(exc)
        return future

    def _skip(self, job, reason, epoch=None):
        frame = job["frame"]
        self.skipped_sources.append(
            dict(
                source_frame_id=int(frame.source_frame_id),
                captured_monotonic=float(frame.capture_received_monotonic),
                skipped_monotonic=time.monotonic(),
                reason=reason,
                epoch=self.epoch if epoch is None else epoch,
                model_executed=False,
            )
        )

    def submit(self, job):
        source = int(job["frame"].source_frame_id)
        with self.cv:
            now = time.monotonic()
            self._account(now)
            self.admission_attempts += 1
            if self.closed:
                self.admission_rejected_closed += 1
                return False
            if self.last_submitted_source is not None:
                step = (source - self.last_submitted_source) & 0xFFFFFFFF
                if not 0 < step < 0x80000000:
                    self.duplicate_admissions += 1
                    return False
            self.last_submitted_source = source
            occupied = len(set(self.order) | self.active)
            if self.minimal_fifo and occupied >= self.capacity:

                epoch = self.epoch
                waiting = now
                self.admission_wait_count += 1
                while (
                    occupied >= self.capacity
                    and not self.closed
                    and self.epoch == epoch
                ):
                    self.cv.wait(0.05)
                    now = time.monotonic()
                    self._account(now)
                    occupied = len(set(self.order) | self.active)
                now = time.monotonic()
                self._account(now)
                waited = (now - waiting) * 1000
                self.admission_wait_ms_total += waited
                self.admission_wait_ms_max = max(self.admission_wait_ms_max, waited)
                if self.closed:
                    self.admission_rejected_closed += 1
                    self._skip(job, "closed_during_admission_wait", epoch)
                    return False
                if self.epoch != epoch:
                    self.admission_rejected_epoch += 1
                    self._skip(job, "epoch_changed_during_admission_wait", epoch)
                    return False
            elif occupied >= self.capacity:
                if not self.pending:
                    self.dropped += 1
                    self.rejected_no_pending += 1
                    reason = (
                        "completed_or_publishing" if self.results else "only_running"
                    )
                    self.saturation_rejections[reason] += 1
                    self._skip(job, "all_slots_running_completed_or_publishing")
                    return False
                key, old = self.pending.popleft()
                self.order.remove(key)
                self.dropped += 1
                self.replaced_pending += 1
                self._skip(old, "superseded_unstarted_pending")
            self.sequence += 1
            key = (self.epoch, self.sequence)
            job["scheduler_admitted_monotonic"] = now
            self.order.append(key)
            self.pending.append((key, job))
            self.admitted_count += 1
            self.last_admitted_source = source
            self.high_water = max(self.high_water, len(set(self.order) | self.active))
            self.cv.notify_all()
            return True

    def _worker(self, core):
        try:
            if self.setup:
                self.setup(core)
            with self.cv:
                self._account(time.monotonic())
                self.worker_ready[core] = True
            while True:
                with self.cv:
                    self.cv.wait_for(lambda: self.closed or self.pending)
                    self._account(time.monotonic())
                    if self.closed:
                        return
                    key, job = self.pending.popleft()
                    self.active.add(key)
                    self.worker_keys[core] = key
                    self.core_demand[core]["dispatches"] += 1
                    self.started_count += 1
                    job["npu_worker_started_monotonic"] = time.monotonic()
                    job["npu_worker_core"] = core
                result = self.execute(core, job)
                job["npu_worker_finished_monotonic"] = time.monotonic()
                with self.cv:
                    self._account(time.monotonic())
                    self.active.discard(key)
                    self.worker_keys[core] = None
                    self.execute_completed_count += 1
                    if key[0] == self.epoch and key in self.order:
                        self.results[key] = (job, result)
                        if result is not None:
                            self.valid_completed_count += 1
                    self.cv.notify_all()
        except BaseException as exc:
            self.fail(exc)
            self.stop()

    def _collect(self):
        try:
            target = int(os.environ.get("VISION_COLLECTOR_CPU", "7"))
            if target not in (6, 7):
                raise ValueError("Collector CPU must be a measured big core 6 or 7")
            tid = threading.get_native_id()
            previous_cpus = sorted(os.sched_getaffinity(0))
            os.sched_setaffinity(0, {target})
            os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))

            os.setpriority(os.PRIO_PROCESS, tid, -10)
            self.collector_scheduling = dict(
                tid=tid,
                cpus=sorted(os.sched_getaffinity(0)),
                previous_cpus=previous_cpus,
                nice=os.getpriority(os.PRIO_PROCESS, tid),
                policy=os.sched_getscheduler(0),
                role="serial-tracking-and-publication",
                preview_cpu=5,
                display_cpu=4,
            )
            self.collector_ident = threading.get_ident()
            while True:
                command = None
                with self.cv:
                    self.cv.wait_for(
                        lambda: self.closed
                        or self.control_commands
                        or (self.order and self.order[0] in self.results)
                    )
                    self._account(time.monotonic())
                    if self.closed:
                        return
                    if self.control_commands:
                        command = self.control_commands.popleft()
                        self.control_running = True
                    else:
                        key = self.order[0]
                        job, result = self.results[key]
                        self.publishing_key = key
                if command is not None:
                    future, callback = command
                    started = time.monotonic()
                    failed = False
                    if future.set_running_or_notify_cancel():
                        try:
                            future.set_result(callback())
                        except BaseException as exc:
                            future.set_exception(exc)
                            failed = True
                    with self.cv:
                        self._account(time.monotonic())
                        self.control_running = False
                        self.control_completed += 1
                        self.control_failed += int(failed)
                        self.control_max_ms = max(
                            self.control_max_ms, (time.monotonic() - started) * 1000
                        )
                        self.cv.notify_all()
                    continue
                entered = time.monotonic()
                if result is not None:
                    job["ordered_publish_entered_monotonic"] = entered
                    self.publish(job, result)
                    job["ordered_publish_completed_monotonic"] = time.monotonic()
                completed = time.monotonic()
                with self.cv:
                    self._account(time.monotonic())
                    if key in self.order:
                        self.order.remove(key)
                    self.results.pop(key, None)
                    if result is not None:
                        self.published += 1
                    self.publishing_key = None
                    self.phase_samples.append(
                        dict(
                            time=completed,
                            epoch=key[0],
                            source_frame_id=int(job["frame"].source_frame_id),
                            core=job["npu_worker_core"],
                            result_available=result is not None,
                            pending_wait_ms=(
                                job["npu_worker_started_monotonic"]
                                - job["scheduler_admitted_monotonic"]
                            )
                            * 1000,
                            worker_execution_ms=(
                                job["npu_worker_finished_monotonic"]
                                - job["npu_worker_started_monotonic"]
                            )
                            * 1000,
                            completed_waiting_ms=(
                                entered - job["npu_worker_finished_monotonic"]
                            )
                            * 1000,
                            publication_callback_ms=(completed - entered) * 1000,
                            occupied_residence_ms=(
                                completed - job["scheduler_admitted_monotonic"]
                            )
                            * 1000,
                        )
                    )
                    self.cv.notify_all()
        except BaseException as exc:
            self.fail(exc)
            self.stop()

    def invalidate(self):
        with self.cv:
            self._account(time.monotonic())
            super().invalidate()
            self.last_admitted_source = None
            self.last_submitted_source = None

    def stop(self):
        with self.cv:
            self._account(time.monotonic())
            commands = list(self.control_commands)
            self.control_commands.clear()
            super().stop()
        for future, _ in commands:
            if future.set_running_or_notify_cancel():
                future.set_exception(
                    RuntimeError("Inference scheduler stopped before control command")
                )

    def timing_snapshot(self, since):
        with self.cv:
            return [dict(row) for row in self.phase_samples if row["time"] > since]

    def snapshot(self):
        with self.cv:
            now = time.monotonic()
            self._account(now)
            result = super().snapshot()
            return dict(
                result,
                mode="two_cores_distinct_complete_native_roi_frames",
                input_transform="native_full_roi_fpn_integer_crop_v1",
                model_calls_per_completed_source=1,
                partial_tile_results=False,
                duplicate_admissions=self.duplicate_admissions,
                worker_capacity=2,
                pending_capacity_when_two_running=self.capacity - 2,
                capacity_scope="running_pending_completed_and_publishing",
                pending_depth=len(self.pending),
                occupied_depth=len(set(self.order) | self.active),
                publishing=self.publishing_key is not None,
                admission_policy=(
                    "bounded_fifo_stop_epoch_wait"
                    if self.minimal_fifo
                    else "nonblocking_replace_oldest_unstarted_pending"
                ),
                admission_attempts=self.admission_attempts,
                admitted_count=self.admitted_count,
                started_count=self.started_count,
                execute_completed_count=self.execute_completed_count,
                valid_completed_count=self.valid_completed_count,
                rejected_no_pending=self.rejected_no_pending,
                replaced_pending=self.replaced_pending,
                admission_rejected_closed=self.admission_rejected_closed,
                admission_rejected_epoch=self.admission_rejected_epoch,
                admission_timeouts=self.admission_timeouts,
                recent_skipped_sources=list(self.skipped_sources)[-16:],
                skipped_source_count=self.rejected_no_pending
                + self.replaced_pending
                + self.admission_timeouts,
                admission_wait_count=self.admission_wait_count,
                admission_wait_ms_total=self.admission_wait_ms_total,
                admission_wait_ms_max=self.admission_wait_ms_max,
                published_is_callback_count=True,
                collector_scheduling=getattr(self, "collector_scheduling", None),
                actual_model_calls_source="pipeline.core_calls; execute callback can be stale before model call",
                executed_results_are_not_discarded_for_age=True,
                control_owner=dict(
                    thread_ident=self.collector_ident,
                    pending=len(self.control_commands),
                    running=self.control_running,
                    completed=self.control_completed,
                    failed=self.control_failed,
                    max_ms=self.control_max_ms,
                ),
                demand_diagnostics=dict(
                    version=1,
                    measured_since_monotonic=self.account_started,
                    measured_until_monotonic=now,
                    seconds=(
                        now - self.account_started
                        if self.account_started is not None
                        else 0.0
                    ),
                    core_demand=[
                        dict(
                            core=row["core"],
                            seconds=dict(row["seconds"]),
                            dispatches=row["dispatches"],
                        )
                        for row in self.core_demand
                    ],
                    slot_seconds=dict(self.slot_seconds),
                    time_at_occupancy_seconds=dict(self.time_at_occupancy),
                    time_at_running_seconds=dict(self.time_at_running),
                    saturation_rejections=dict(self.saturation_rejections),
                    core_scope="worker slot execution and scheduler handoff including preprocessing and decode; actual RKNN time is reported separately",
                    available_slot_idle_scope="no admitted pending input; does not prove the camera lacked fresh frames",
                    whole_pipeline_capacity_includes_publication=True,
                ),
            )
