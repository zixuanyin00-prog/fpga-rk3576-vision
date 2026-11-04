import multiprocessing as mp
import ctypes as C
import json
import os
import time
import queue
import sys
from types import SimpleNamespace
from pathlib import Path
import numpy as np
from two_stage import Frame
from motion_display_alignment import (
    MotionDisplayAlignment as PixelDisplayAlignment,
    frame_delta,
)
from display_trace_async import DisplayTrace
from display_inhibit import DisplayInhibit
from latest_results import LatestResults
from collections import deque

SHAPE = (1080, 1920, 3)
BYTES = 1080 * 1920 * 3
THUMB_SHAPE = (288, 512, 3)
THUMB_BYTES = 288 * 512 * 3


def latest_put(q, item):
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            return
        try:
            q.put_nowait(item)
        except queue.Full:
            pass


def display_worker(
    monitor,
    data,
    thumbnail_data,
    locks,
    versions,
    sources,
    requests,
    captured,
    latest,
    native_enabled,
    event,
    stop,
    states,
    commands,
    initial,
    result_channel,
):
    os.environ["VISION_MONITOR"] = monitor
    os.environ["VISION_PERF_FILE"] = "screen_performance_" + monitor + ".json"
    os.sched_setaffinity(0, {4 if monitor == "HDMI-1" else 5})
    sys.setswitchinterval(0.001)
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(8))
    if (
        os.sched_getscheduler(0) != os.SCHED_FIFO
        or os.sched_getparam(0).sched_priority != 8
    ):
        raise RuntimeError("Display scheduling readback mismatch")
    from native_view import ScreenView

    awake = DisplayInhibit()
    view = ScreenView(commands)
    row = initial
    previous = None
    seen = 0
    gaps = 0
    result_sequence = 0
    result_revision = result_channel.revision.value
    result_row = {}
    result_reads = 0
    result_invalidations = 0
    result_history = deque(maxlen=16)
    exact_source_frames = 0
    earlier_source_frames = 0
    no_result_frames = 0

    presentation_delay = 8
    alignment = PixelDisplayAlignment(budget_ms=6.0)
    trace = DisplayTrace(monitor, source_size=(1920, 1080))
    view.write_report = trace.write_report
    native = {}
    for name in (
        []
        if not hasattr(view, "s")
        else [
            "SDL_RenderPresent",
            "SDL_UpdateTexture",
            "SDL_RenderCopy",
            "SDL_GetWindowPosition",
            "SDL_GetRendererOutputSize",
            "SDL_PollEvent",
        ]
    ):
        original = getattr(view.s, name)
        native[name] = [0, 0.0]

        def timed(*args, _name=name, _original=original):
            at = time.perf_counter()
            value = _original(*args)
            native[_name][0] += 1
            native[_name][1] += time.perf_counter() - at
            return value

        setattr(view.s, name, timed)
    began = last_report = time.monotonic()
    first = None
    last = None
    times = deque(maxlen=120)
    last_sequence = 0
    try:
        while not stop.is_set():
            token = latest.value
            if not token or token // len(locks) <= last_sequence:
                event.wait(0.002 if native_enabled.value else 0.2)
                event.clear()
                token = latest.value
            if not token:
                continue
            newest, newest_index = divmod(token, len(locks))
            pending = [(v, i) for i, v in enumerate(versions) if v > last_sequence]
            if not pending:
                continue

            while True:
                try:
                    row = states.get_nowait()
                except queue.Empty:
                    break
            ready = [item for item in pending if item[0] <= newest - presentation_delay]
            if not ready:
                event.wait(0.002)
                event.clear()
                continue
            seq, idx = min(ready)

            if not locks[idx].acquire(False):
                time.sleep(0.0002)
                continue
            try:
                if versions[idx] != seq:
                    continue
                source = sources[idx]
                capture_received = captured[idx]
                request = requests[idx]
            finally:
                locks[idx].release()
            if source == previous:

                last_sequence = seq
                continue

            revision = result_channel.revision.value
            if revision != result_revision:
                result_revision = revision
                result_row = {}
                result_invalidations += 1
                result_history.clear()
                alignment.real_observations.clear()
                alignment.latest_revision = None
                alignment.last_motion_state = None
            for packet in result_channel.read_after(result_sequence):
                packet_sequence, packet_revision, packet_row = packet
                if packet_revision == result_revision:
                    result_sequence = packet_sequence
                    result_reads += 1
                    result_history.append(packet_row)

            eligible = [
                packet
                for packet in result_history
                if frame_delta(source, packet["observation_source_frame_id"]) >= 0
            ]
            result_row = eligible[-1] if eligible else {}

            display_state = row
            if row.get("display_revision") != result_revision:
                display_state = dict(row, inference_rectangle=None)
            display_state = dict(display_state, **result_row)
            observations = display_state.get("results", [])
            aligned = alignment.align(
                observations, source, time.monotonic(), capture_received
            )
            display_row = dict(
                display_state,
                results=aligned,
                classification_observations=observations,
                display_alignment_state=dict(
                    source_frame_id=int(source),
                    history_boxes=sum(bool(r.get("display_history")) for r in aligned),
                    pixel_boxes=sum(
                        bool(r.get("display_pixel_tracked")) for r in aligned
                    ),
                    predicted_boxes=sum(
                        bool(r.get("display_prediction")) for r in aligned
                    ),
                    current_boxes=sum(
                        not r.get("display_history")
                        and not r.get("display_pixel_tracked")
                        and not r.get("display_prediction")
                        for r in aligned
                    ),
                    max_source_gap=max(
                        (r.get("display_source_gap", 0) for r in aligned), default=0
                    ),
                    max_age_ms=round(
                        max(
                            (r.get("display_observation_age_ms", 0.0) for r in aligned),
                            default=0.0,
                        ),
                        1,
                    ),
                ),
            )
            view.prepare(SimpleNamespace(last_overview=None), display_row)
            if not locks[idx].acquire(False):
                time.sleep(0.0002)
                continue
            try:
                if versions[idx] != seq:
                    continue
                rgb = np.frombuffer(
                    data, dtype=np.uint8, count=BYTES, offset=idx * BYTES
                ).reshape(SHAPE)
                frame = Frame(request, source, False, 0, 0, rgb)
                shown = view.show(
                    SimpleNamespace(last_overview=frame, last_boxes=[], last_roi=None),
                    display_row,
                    prepared=True,
                )
                if shown is not False:
                    submitted = time.monotonic()
                    traced_pixels = bool(trace.active and trace.active["pixels"])
                    if traced_pixels:

                        trace.add(
                            source,
                            capture_received,
                            submitted,
                            aligned,
                            alignment.last_rejections,
                            rgb,
                        )
                    last_sequence = seq
            finally:
                locks[idx].release()
            if shown is False:
                time.sleep(0.0002)
                continue
            selected_source = result_row.get("observation_source_frame_id")
            exact_source_frames += selected_source == source
            earlier_source_frames += (
                selected_source is not None and selected_source != source
            )
            no_result_frames += selected_source is None
            alignment.presented(source, aligned, submitted)
            if not traced_pixels:
                trace.add(
                    source,
                    capture_received,
                    submitted,
                    aligned,
                    alignment.last_rejections,
                )
            now = time.monotonic()
            if previous is not None:
                delta = (source - previous) & 0xFFFFFFFF
                if not 0 < delta < 0x80000000:
                    raise RuntimeError("Display source frame sequence reversed")
                gaps += delta - 1
                times.append(now - last)
            else:
                first = now
            previous = source
            last = now
            seen += 1
            if now - last_report >= 1:
                report = dict(
                    display_scheduling=dict(
                        pid=os.getpid(),
                        cpus=sorted(os.sched_getaffinity(0)),
                        policy=os.sched_getscheduler(0),
                        priority=os.sched_getparam(0).sched_priority,
                    ),
                    monitor=monitor,
                    frames=seen,
                    unique_frames=seen,
                    screen_awake=awake.state,
                    actual_fresh_fps=(seen - 1) / (now - first) if seen > 1 else 0,
                    skipped_source_frames=gaps,
                    last_source_frame=source,
                    elapsed_s=now - first,
                    recorded_monotonic=now,
                    max_present_interval_ms=max(times, default=0) * 1000,
                    p95_present_interval_ms=(
                        float(np.percentile(list(times), 95)) * 1000 if times else 0
                    ),
                    alignment=alignment.stats(now),
                    result_channel=dict(
                        result_channel.stats(),
                        read_sequence=result_sequence,
                        packets_read=result_reads,
                        invalidations_seen=result_invalidations,
                    ),
                    last_preview_capture_received_monotonic=capture_received,
                    last_display_submit_return_monotonic=submitted,
                    native_ms_per_frame={
                        k: v[1] * 1000 / seen for k, v in native.items()
                    },
                    presentation=getattr(view, "native_stats", {}),
                )
                report["source_alignment"] = dict(
                    fixed_delay_source_frames=presentation_delay,
                    fixed_delay_ms=1000 * presentation_delay / 60,
                    result_history_packets=len(result_history),
                    selected_result_source=result_row.get(
                        "observation_source_frame_id"
                    ),
                    exact_source_frames=exact_source_frames,
                    earlier_source_frames=earlier_source_frames,
                    no_result_frames=no_result_frames,
                    preview_age_ms=(submitted - capture_received) * 1000,
                    waits_for_inference=False,
                    repeats_old_frames=False,
                )
                trace.write_report(Path("display_" + monitor + ".json"), report)
                trace.poll(now)
                trace.report(report["alignment"])
                last_report = now
    finally:
        trace.close()
        view.close()
        awake.close()


class SharedPreview:
    def __init__(self, initial, monitors=("HDMI-1",), slots=16):
        self.ctx = mp.get_context("spawn")
        self.n = slots
        self.results = LatestResults(self.ctx)
        self.monitors = tuple(monitors)
        self.data = self.ctx.RawArray(C.c_ubyte, slots * BYTES)
        self.thumbnail_data = self.ctx.RawArray(C.c_ubyte, slots * THUMB_BYTES)
        self.locks = [self.ctx.Lock() for _ in range(slots)]
        self.versions = self.ctx.RawArray(C.c_ulonglong, slots)
        self.sources = self.ctx.RawArray(C.c_uint, slots)
        self.requests = self.ctx.RawArray(C.c_uint, slots)
        self.captured = self.ctx.RawArray(C.c_double, slots)
        self.latest = self.ctx.Value(C.c_ulonglong, 0)
        self.stop = self.ctx.Event()
        self.native_enabled = self.ctx.RawValue(C.c_uint, 0)
        self.sem_handles = (C.c_size_t * slots)(
            *[lock._semlock.handle for lock in self.locks]
        )
        self.commands = self.ctx.Queue(64)
        self.inference_event = self.ctx.Event()
        self.events = [self.ctx.Event() for _ in monitors]
        self.states = [self.ctx.Queue(4) for _ in monitors]
        self.processes = []
        self.sequence = 0
        self.dropped = 0
        for i, monitor in enumerate(monitors):
            p = self.ctx.Process(
                target=display_worker,
                args=(
                    monitor,
                    self.data,
                    self.thumbnail_data,
                    self.locks,
                    self.versions,
                    self.sources,
                    self.requests,
                    self.captured,
                    self.latest,
                    self.native_enabled,
                    self.events[i],
                    self.stop,
                    self.states[i],
                    self.commands,
                    initial,
                    self.results,
                ),
                daemon=False,
            )
            p.start()
            self.processes.append(p)

    def native_preview_sink(self):

        return SimpleNamespace(
            slots=self.n,
            rgb=self.data,
            rgb_bytes=self.n * BYTES,
            sem_handles=self.sem_handles,
            locks=self.locks,
            versions=self.versions,
            sources=self.sources,
            requests=self.requests,
            captured=self.captured,
            latest=self.latest,
            enabled=self.native_enabled,
            events=self.events,
        )

    def preview(self, frame):
        if self.native_enabled.value:
            raise RuntimeError("Native preview already owns publication")
        if frame.rgb.shape != SHAPE:
            raise ValueError("Three-stream preview must be native 1920x1080")
        self.sequence += 1
        for idx in sorted(range(self.n), key=lambda i: self.versions[i]):
            if not self.locks[idx].acquire(False):
                continue
            try:
                np.frombuffer(
                    self.data, dtype=np.uint8, count=BYTES, offset=idx * BYTES
                )[:] = frame.rgb.reshape(-1)
                self.sources[idx] = frame.source_frame_id
                self.requests[idx] = frame.request_id
                self.captured[idx] = getattr(frame, "capture_received_monotonic", 0.0)
                self.versions[idx] = self.sequence
                self.latest.value = self.sequence * self.n + idx
            finally:
                self.locks[idx].release()
            for e in self.events:
                e.set()
            self.inference_event.set()
            return
        self.dropped += 1

    def invalidate_results(self, revision):
        self.results.invalidate(revision)

    def publish_results(
        self, revision, rows, model_id, rectangle, preview_only, source
    ):
        published = time.monotonic()
        packet = dict(
            results=[
                dict(
                    r,
                    result_publish_monotonic=published,
                    direct_display_publish_monotonic=published,
                )
                for r in rows
            ],
            model_id=model_id,
            inference_rectangle=rectangle,
            preview_only=preview_only,
            observation_source_frame_id=source,
        )
        return self.results.publish(revision, packet)

    def show(self, row):

        row = {key: value for key, value in row.items() if key != "results"}
        for p in self.processes:
            if not p.is_alive():
                raise RuntimeError("A display process exited")
        for q in self.states:
            latest_put(q, row)

    def drain_commands(self):
        latest = {}
        while True:
            try:
                key, value = self.commands.get_nowait()
            except queue.Empty:
                return list(latest.items())
            if key == "reset":
                latest.clear()
            latest.pop(key, None)
            latest[key] = value

    def close(self):
        self.stop.set()
        for e in self.events:
            e.set()
        for p in self.processes:
            p.join(4)
            if p.is_alive():
                p.terminate()
                p.join(2)
        for q in self.states + [self.commands]:
            q.cancel_join_thread()
            q.close()
