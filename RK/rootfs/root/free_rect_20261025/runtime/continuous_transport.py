import ctypes as C
from dataclasses import dataclass, field
from collections import deque
import concurrent.futures as futures
import errno
import json
import os
import threading
import time
import numpy as np
from transport import RGBTransport
from two_stage import Command, Frame
from capture_scheduling import CaptureScheduling


@dataclass(frozen=True)
class CapturedFrame(Frame):

    capture_received_monotonic: float = 0.0
    transport_dequeued_monotonic: float = 0.0
    python_copy_returned_monotonic: float = 0.0
    channel: int = 0
    enhance_revision: int = 0
    processing_revision: int = 0
    fpga_epoch: int = 0
    processing_config: dict = None


@dataclass
class RoiStream:
    key: tuple = None
    request_id: int = None
    source: int = None
    consumed: int = None
    pending: tuple = None
    frames: deque = field(default_factory=lambda: deque(maxlen=2))
    stats: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RectCommand:
    request_id: int
    roi: bool
    x: int
    y: int
    width: int
    height: int
    parent: int = 0
    channel: int = 1


def owned_rgb(buffer, meta):

    payload, width, height = meta[2], meta[12], meta[13]
    if not (
        0 < width <= 1920
        and 0 < height <= 1080
        and payload == width * height * 3
        and payload <= buffer.nbytes
    ):
        raise RuntimeError("Validated DMA RGB payload geometry mismatch")
    buffer.resize((height, width, 3), refcheck=False)
    buffer.setflags(write=False)
    return buffer


class ContinuousTransport:
    def __init__(self, library, on_preview=None, *, scheduling=None, preview_sink=None):
        self.library, self.on_preview = library, on_preview
        self.preview_sink = preview_sink
        self.preview_direct = False
        self._preview_sink_snapshot = None
        self._preview_sink_values = (C.c_uint64 * 10)()
        self.scheduling = scheduling or CaptureScheduling()

        self._queue_lib = C.CDLL(str(library), use_errno=True)
        version = self._queue_lib.rgb_queue_api_version
        version.argtypes = []
        version.restype = C.c_uint
        if version() != 3:
            raise RuntimeError("Three-stream transport requires queue API3")
        for symbol in ("rgb_queue_start_configured", "rgb_queue_snapshot"):
            getattr(self._queue_lib, symbol)
        self.condition = threading.Condition()
        self.tasks = deque()
        self.cached = {}
        self.reads = {}
        self.process_configs = {}
        self.process_state = None
        self.latest = None
        self.consumed = None
        self.sequence = 0
        self.closed = False
        self.error = None
        self.stats = dict(
            physical_frames=0,
            consumer_rgb_bytes=0,
            consumer_wait_ms=0.0,
            preview_frames=0,
            source_gaps=0,
        )
        self.roi1_fifo = os.environ.get("VISION_MINIMAL60") == "1"
        self.streams = {
            1: RoiStream(
                frames=deque(maxlen=4 if self.roi1_fifo else 2), stats=self.stats
            ),
            2: RoiStream(),
        }
        self.stats["capture_roi"] = self.streams[2].stats
        self.started = futures.Future()
        self.thread = threading.Thread(
            target=self._run, name="continuous-dma-owner", daemon=True
        )
        self.thread.start()
        try:
            self.started.result(timeout=10)
        except BaseException:
            with self.condition:
                self.closed = True
                self.condition.notify_all()
            self.thread.join(10)
            if self.thread.is_alive():
                raise RuntimeError("Camera startup cleanup did not finish")
            raise

    def _remember_process(self, state):
        self.process_state = state
        from process_controls import effective_mask, decode

        words = list(state["requested"]["words"])
        words[1] = effective_mask(words)
        self.process_configs[state["requested_revision"]] = decode(words)
        self.process_configs[state["effective_revision"]] = state["effective"]
        while len(self.process_configs) > 64:
            self.process_configs.pop(next(iter(self.process_configs)))

    def _check(self):
        if self.error is not None:
            raise RuntimeError("Continuous camera failed") from self.error
        if self.closed:
            raise RuntimeError("Camera closed")

    def _call(self, name, *args):
        f = futures.Future()
        with self.condition:
            self._check()
            self.tasks.append((name, args, f))
            self.condition.notify_all()
        return f

    def _run(self):
        device = None
        queued = False
        try:
            owner = self.scheduling.apply_owner()
            device = RGBTransport(self.library)
            self._remember_process(device.process_status())
            start = device.lib.rgb_queue_start_configured
            start.argtypes = [C.c_void_p, C.c_int, C.c_int]
            start.restype = C.c_int
            if start(device.handle, 5, -20):
                raise OSError(
                    C.get_errno(), "Native DMA scheduling/queue startup failed"
                )
            queued = True
            attach = getattr(device.lib, "rgb_queue_set_preview_sink", None)
            direct_snapshot = getattr(
                device.lib, "rgb_queue_preview_sink_snapshot", None
            )
            if (
                self.preview_sink is not None
                and os.environ.get("VISION_PREVIEW_CODEC", "raw") == "raw"
                and attach is not None
                and direct_snapshot is not None
            ):
                sink = self.preview_sink
                attach.argtypes = [
                    C.c_void_p,
                    C.c_uint32,
                    C.c_void_p,
                    C.c_uint64,
                    C.POINTER(C.c_size_t),
                    C.POINTER(C.c_uint64),
                    C.POINTER(C.c_uint32),
                    C.POINTER(C.c_uint32),
                    C.POINTER(C.c_double),
                    C.POINTER(C.c_uint64),
                ]
                attach.restype = C.c_int
                direct_snapshot.argtypes = [
                    C.c_void_p,
                    C.POINTER(C.c_uint64),
                    C.c_size_t,
                ]
                direct_snapshot.restype = C.c_int

                self.on_preview = None
                if attach(
                    device.handle,
                    sink.slots,
                    C.addressof(sink.rgb),
                    sink.rgb_bytes,
                    sink.sem_handles,
                    C.cast(sink.versions, C.POINTER(C.c_uint64)),
                    sink.sources,
                    sink.requests,
                    sink.captured,
                    C.cast(C.pointer(sink.latest.get_obj()), C.POINTER(C.c_uint64)),
                ):
                    raise OSError(C.get_errno(), "Cannot attach native preview sink")
                self._preview_sink_snapshot = direct_snapshot
                self.preview_direct = True
                sink.enabled.value = 1
                for event in sink.events:
                    event.set()
            snapshot = device.lib.rgb_queue_snapshot
            snapshot.argtypes = [C.c_void_p, C.POINTER(C.c_uint64), C.c_size_t]
            snapshot.restype = C.c_int
            native_stats = (C.c_uint64 * 48)()
            codec_stats = (C.c_uint64 * 9)()
            codec_snapshot = device.lib.preview_decode_snapshot
            codec_snapshot.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
            codec_snapshot.restype = C.c_int
            self.stats["scheduling"] = dict(
                plan=self.scheduling.describe(), owner=owner
            )
            copy = device.lib.rgb_device_copy_queued_timed
            copy.argtypes = [
                C.c_void_p,
                C.c_uint,
                C.c_uint32,
                C.c_void_p,
                C.c_size_t,
                C.c_void_p,
                C.c_void_p,
            ]
            receive_timing = (C.c_uint64 * 2)()
            copy.restype = C.c_int
            rect_request = device.lib.rgb_device_stream_rect_channel
            rect_request.argtypes = [C.c_void_p] + [C.c_uint] * 6
            rect_request.restype = C.c_int
            capacities = (1920 * 1080 * 3, 518400 * 3, 518400 * 3)
            buffers = [np.empty(size, dtype=np.uint8) for size in capacities]
            metas = [(C.c_uint32 * 24)() for _ in range(3)]
            last = [0, 0, 0]
            status = (C.c_uint32 * 4)()
            status_due = 0
            preview_cache_due = 0
            epoch = time.monotonic()
            received = 0
            previous_source = None
            gaps = 0
            self._snapshot_native(snapshot, device.handle, native_stats)
            native_received = self.stats["preview_frames"]
            native_gaps = self.stats["source_gaps"]
            cache_frames = 0
            cache_source_gaps = 0
            self.started.set_result(True)
            while True:
                with self.condition:
                    if self.closed:
                        break
                    task = self.tasks.popleft() if self.tasks else None
                if task is not None:
                    name, args, f = task
                    if name == "_roi":
                        command = args[0]
                        stream = self.streams[command.channel]
                        if stream.pending is not None:
                            raise RuntimeError(
                                "More than one request on the same ROI channel"
                            )
                        stream.pending = (
                            command,
                            f,
                            False,
                            time.monotonic() + device.timeout,
                        )
                    else:
                        try:
                            result = getattr(device, name)(*args)
                            if name in (
                                "process_submit",
                                "set_isp",
                                "enhance_submit",
                                "process_status",
                            ):
                                self._remember_process(
                                    result
                                    if name == "process_status"
                                    else device.process_status()
                                )
                            f.set_result(result)
                        except Exception as exc:
                            f.set_exception(exc)
                now = time.monotonic()
                for channel, stream in self.streams.items():
                    if stream.pending is None:
                        continue
                    command, f, sent, deadline = stream.pending
                    if now > deadline:
                        raise TimeoutError("Native ROI timed out")
                    if not sent:
                        rc = rect_request(
                            device.handle,
                            channel,
                            command.request_id,
                            command.x,
                            command.y,
                            command.width,
                            command.height,
                        )
                        if rc and C.get_errno() != errno.EBUSY:
                            raise OSError(C.get_errno(), "ROI rejected")
                        if not rc:
                            stream.pending = (command, f, True, deadline)
                            with self.condition:
                                stream.key = (
                                    command.x,
                                    command.y,
                                    command.width,
                                    command.height,
                                )
                                stream.request_id = command.request_id
                                stream.stats["roi_epoch_discarded_frames"] = (
                                    stream.stats.get("roi_epoch_discarded_frames", 0)
                                    + len(stream.frames)
                                )
                                stream.frames.clear()
                                stream.source = None
                                stream.consumed = None

                if now >= status_due:
                    if device.lib.jpeg_device_status(device.handle, status):
                        raise OSError(C.get_errno(), "DMA status failed")
                    if status[0] & 4:
                        raise RuntimeError(f"FPGA DMA fault {(status[0]>>4)&15}")
                    status_due = now + 0.05
                progress = False
                self.stats["owner_iterations"] = (
                    self.stats.get("owner_iterations", 0) + 1
                )
                for channel in (0, 1, 2):
                    if channel == 0 and self.preview_direct:
                        if now < preview_cache_due:
                            continue
                        preview_cache_due = now + 0.5
                    stream = self.streams.get(channel)
                    if stream is not None and stream.request_id is None:
                        continue
                    channel_frames = 0
                    burst_deadline = time.monotonic() + 0.002
                    for attempt in range(1 if channel == 0 else 8):
                        if attempt and time.monotonic() >= burst_deadline:
                            break
                        if channel == 1 and self.roi1_fifo:
                            with self.condition:
                                if len(stream.frames) == stream.frames.maxlen:
                                    break
                        rc = copy(
                            device.handle,
                            channel,
                            last[channel],
                            buffers[channel].ctypes.data,
                            buffers[channel].nbytes,
                            metas[channel],
                            receive_timing,
                        )
                        copied_at = time.monotonic()
                        received_at = receive_timing[0] / 1e9
                        dequeued_at = receive_timing[1] / 1e9
                        if rc > 0 and not 0 < received_at <= dequeued_at <= copied_at:
                            raise RuntimeError(
                                "Native/Python monotonic timestamp mismatch"
                            )

                        if rc < 0:
                            raise RuntimeError(
                                f"RGB channel {channel} descriptor error {rc}"
                            )
                        if not rc:
                            break
                        progress = True
                        channel_frames += 1
                        m = tuple(metas[channel])
                        last[channel] = m[1]
                        rgb = owned_rgb(buffers[channel], m)
                        buffers[channel] = np.empty(capacities[channel], dtype=np.uint8)
                        frame = CapturedFrame(
                            m[4],
                            m[5],
                            bool(m[9] & 2),
                            m[8] & 4095,
                            (m[8] >> 12) & 4095,
                            rgb,
                            received_at,
                            transport_dequeued_monotonic=dequeued_at,
                            python_copy_returned_monotonic=copied_at,
                            channel=channel,
                            enhance_revision=m[16],
                            processing_revision=m[17],
                            fpga_epoch=m[18],
                            processing_config=self.process_configs.get(m[17]),
                        )
                        if frame.roi != bool(channel):
                            raise RuntimeError("DMA channel flag mismatch")
                        if channel and ((m[9] >> 8) & 255) != channel:
                            raise RuntimeError("Native ROI channel identity mismatch")
                        if not self.preview_direct:
                            self.stats["physical_frames"] += 1
                        self.stats["consumer_rgb_bytes"] += rgb.nbytes
                        self.stats["last_capture_received_monotonic"] = received_at
                        if channel == 0:
                            received += 1
                            if not self.preview_direct:
                                self.stats["preview_frames"] += 1
                                self.stats["last_preview_source_frame_id"] = (
                                    frame.source_frame_id
                                )
                                self.stats[
                                    "last_preview_capture_received_monotonic"
                                ] = received_at
                            self.stats["last_preview_dma"] = dict(
                                sequence=m[1],
                                source=m[5],
                                start_cycles=m[6],
                                end_cycles=m[7],
                                duration_ms=((m[7] - m[6]) & 0xFFFFFFFF) / 125000,
                            )
                            if previous_source is not None:
                                delta = (
                                    frame.source_frame_id - previous_source
                                ) & 0xFFFFFFFF
                                if not 0 < delta < 0x80000000:
                                    raise RuntimeError(
                                        "Source sequence did not advance"
                                    )
                                gaps += delta - 1
                                if self.preview_direct:
                                    cache_source_gaps += delta - 1
                                else:
                                    self.stats["source_gaps"] += delta - 1
                            previous_source = frame.source_frame_id
                            if self.preview_direct:
                                cache_frames += 1
                                self.stats["console_preview_cache"] = dict(
                                    frames=cache_frames,
                                    source_frame_id=frame.source_frame_id,
                                    capture_received_monotonic=received_at,
                                    enhance_revision=frame.enhance_revision,
                                    processing_revision=frame.processing_revision,
                                    fpga_epoch=frame.fpga_epoch,
                                    source_gaps=cache_source_gaps,
                                    configured_hz=2.0,
                                    sampled=True,
                                )
                                self.stats["preview_cache_frames"] = cache_frames
                            with self.condition:
                                self.latest = frame
                                self.condition.notify_all()
                            if self.on_preview:
                                self.on_preview(frame)
                        else:
                            geometry = (
                                frame.x,
                                frame.y,
                                frame.rgb.shape[1],
                                frame.rgb.shape[0],
                            )
                            if (frame.request_id, geometry) != (
                                stream.request_id,
                                stream.key,
                            ):
                                stream.stats["old_roi_epoch_frames"] = (
                                    stream.stats.get("old_roi_epoch_frames", 0) + 1
                                )
                                continue
                            with self.condition:
                                if stream.source is not None:
                                    step = (
                                        frame.source_frame_id - stream.source
                                    ) & 0xFFFFFFFF
                                    if not 0 < step < 0x80000000:
                                        raise RuntimeError(
                                            "Native source reversed or repeated"
                                        )
                                    stream.stats["roi_source_gaps"] = (
                                        stream.stats.get("roi_source_gaps", 0)
                                        + step
                                        - 1
                                    )
                                stream.source = frame.source_frame_id
                                if len(stream.frames) == stream.frames.maxlen:
                                    stream.stats["roi_buffer_overflows"] = (
                                        stream.stats.get("roi_buffer_overflows", 0) + 1
                                    )
                                    stream.stats["roi_buffer_last_dropped_source"] = (
                                        stream.frames[0].source_frame_id
                                    )
                                stream.frames.append(frame)
                                stream.stats["roi_buffer_depth"] = len(stream.frames)
                                stream.stats["roi_stream_frames"] = (
                                    stream.stats.get("roi_stream_frames", 0) + 1
                                )
                                stream.stats["last_roi_source_frame_id"] = (
                                    frame.source_frame_id
                                )
                                self.condition.notify_all()
                            if stream.pending is not None:
                                command, f, _, _ = stream.pending
                                stream.pending = None
                                f.set_result((command, frame))
                    if stream is not None:
                        stream.stats["roi_owner_burst_high_water"] = max(
                            stream.stats.get("roi_owner_burst_high_water", 0),
                            channel_frames,
                        )
                if now - epoch >= 1:
                    self._snapshot_native(snapshot, device.handle, native_stats)
                    if codec_snapshot(device.handle, codec_stats, 9):
                        raise RuntimeError("Preview decoder counters unavailable")
                    (
                        count,
                        wire_bytes,
                        total_ns,
                        max_ns,
                        format_id,
                        copy_ns,
                        copy_max,
                        roi_copy_ns,
                        roi_copy_max,
                    ) = codec_stats
                    raw_preview = format_id == 0x33424752
                    self.stats["compression"] = dict(
                        location="FPGA before PCIe",
                        codec="rgb888",
                        enabled=False,
                        frames=count,
                        wire_bytes=wire_bytes,
                        compressed_frames=0,
                        compressed_bytes=0,
                        decoded_rgb_bytes=0,
                        compression_ratio_rgb=(
                            (count * 1920 * 1080 * 3 / wire_bytes)
                            if wire_bytes
                            else None
                        ),
                        decoder="native RGB copy",
                        decoder_cpu=5,
                        preview_source_size=[1920, 1080],
                        mean_copy_ms=copy_ns / max(count, 1) / 1e6,
                        max_copy_ms=copy_max / 1e6,
                        roi_copy_max_ms=roi_copy_max / 1e6,
                        mean_decode_ms=total_ns / max(count, 1) / 1e6,
                        max_decode_ms=max_ns / 1e6,
                        roi_compressed=False,
                    )
                    if self.preview_direct:
                        self.stats["preview_cache_fps"] = received / (now - epoch)
                        self.stats["preview_cache_source_gaps"] = cache_source_gaps
                        capture_count = self.stats["preview_frames"]
                        capture_gaps = self.stats["source_gaps"]
                        report = dict(
                            fresh_fps=(capture_count - native_received) / (now - epoch),
                            skipped_source_frames=capture_gaps - native_gaps,
                            last_source_id=self.stats.get(
                                "last_preview_source_frame_id"
                            ),
                            source="native_DMA",
                            console_cache_fps=received / (now - epoch),
                            console_cache_last_source_id=previous_source,
                        )
                        native_received = capture_count
                        native_gaps = capture_gaps
                    else:
                        report = dict(
                            fresh_fps=received / (now - epoch),
                            skipped_source_frames=gaps,
                            last_source_id=previous_source,
                            source="Python_preview",
                        )
                    report["timestamp_ns"] = time.monotonic_ns()
                    print("PREVIEW_CAPTURE " + json.dumps(report), flush=True)
                    epoch = now
                    received = 0
                    gaps = 0
                with self.condition:
                    if not progress and not self.tasks and not self.closed:
                        self.stats["owner_idle_waits"] = (
                            self.stats.get("owner_idle_waits", 0) + 1
                        )
                        self.condition.wait(
                            0.004
                            if self.preview_direct
                            and os.environ.get("VISION_MINIMAL60") == "1"
                            else 0.001
                        )
        except BaseException as exc:
            self.error = exc
            if not self.started.done():
                self.started.set_exception(exc)
        finally:
            if device is not None:
                if queued:
                    stop = device.lib.rgb_queue_stop
                    stop.argtypes = [C.c_void_p]
                    stop.restype = C.c_int
                    if stop(device.handle):
                        self.error = RuntimeError("Native DMA queue did not stop")
                    elif self.preview_direct:
                        self.preview_sink.enabled.value = 0
                try:
                    device.close()
                except BaseException as exc:
                    self.error = exc
            with self.condition:
                for stream in self.streams.values():
                    if stream.pending is not None and not stream.pending[1].done():
                        stream.pending[1].set_exception(
                            self.error or RuntimeError("Camera closed")
                        )
                for _, _, f in self.tasks:
                    if not f.done():
                        f.set_exception(self.error or RuntimeError("Camera closed"))
                self.condition.notify_all()

    def _snapshot_native(self, snapshot, handle, values):
        if snapshot(handle, values, len(values)):
            raise OSError(C.get_errno(), "Cannot read native DMA counters/scheduling")
        v = list(values)
        if v[0] != 3:
            raise RuntimeError("Native DMA statistics API mismatch")
        self.stats["native_dma"] = dict(
            tid=v[1],
            configured_cpu=v[2],
            nice=v[3] - 20,
            policy=v[4],
            frames=v[5:7],
            descriptor_gaps=v[7:9],
            preview_source_gaps=v[9],
            queue_overflows=v[10:12],
            queue_depth=v[12:14],
            queue_high_water=v[14:16],
            max_poll_gap_ms=v[16] / 1e6,
            last_source=v[17:19],
            last_sequence=v[19:21],
            error=v[21],
            scheduling_matches=bool(v[22]),
            roi_native_latest_skipped=v[24],
            roi_native_last_skipped_source=v[25],
            roi_native_last_received_monotonic=v[26] / 1e9,
            roi_native_last_dequeued_monotonic=v[27] / 1e9,
            roi_native_last_dequeued_source=v[28],
            roi_native_capacity=v[29],
            preview_native_capacity=v[30],
            timestamp_basis="validated_DMA_copy_completed_before_native_ring",
            timed_api=bool(v[31]),
        )
        if not v[22]:
            raise RuntimeError("Native DMA thread scheduling changed unexpectedly")
        self.stats["capture_native_dma"] = dict(
            tid=v[32],
            frames=v[33],
            descriptor_gaps=v[34],
            queue_overflows=v[35],
            queue_depth=v[36],
            queue_high_water=v[37],
            last_source=v[38],
            last_sequence=v[39],
            latest_skipped=v[40],
            last_skipped_source=v[41],
            last_received_monotonic=v[42] / 1e9,
            last_dequeued_monotonic=v[43] / 1e9,
            last_dequeued_source=v[44],
            capacity=v[45],
            copy_ms=v[46] / 1e6,
            copy_max_ms=v[47] / 1e6,
        )
        if self.preview_direct:
            if self._preview_sink_snapshot(
                handle, self._preview_sink_values, len(self._preview_sink_values)
            ):
                raise OSError(
                    C.get_errno(), "Cannot read native direct preview counters"
                )
            p = list(self._preview_sink_values)
            self.stats["native_direct_preview"] = dict(
                enabled=bool(p[0]),
                published=p[1],
                dropped=p[2],
                last_source_frame_id=p[3],
                last_request_id=p[4],
                sequence=p[5],
                last_capture_received_monotonic=p[6] / 1e9,
                slots=p[7],
                cache_latest_skipped=p[8],
                last_cache_skipped_source=p[9],
            )

            self.stats["preview_frames"] = v[5]
            self.stats["source_gaps"] = v[9]
            self.stats["last_preview_source_frame_id"] = v[17]
            self.stats["last_preview_capture_received_monotonic"] = p[6] / 1e9
            self.stats["last_preview_capture_source_frame_id"] = p[3]
            self.stats["physical_frames"] = v[5] + v[6] + v[33]
            self.stats["preview_capture_source"] = "native_DMA"
            self.stats["preview_queue_role"] = "sampled_console_cache"

    @property
    def preview_source_id(self):
        if not self.preview_direct:
            frame = self.latest
            return None if frame is None else frame.source_frame_id
        sink = self.preview_sink
        token = sink.latest.value
        if not token:
            return None
        sequence, index = divmod(token, sink.slots)

        candidates = [(sequence, index)] + sorted(
            ((int(v), i) for i, v in enumerate(sink.versions) if v and i != index),
            reverse=True,
        )
        for version, idx in candidates:
            if not sink.locks[idx].acquire(False):
                continue
            try:
                if sink.versions[idx] == version:
                    return int(sink.sources[idx])
            finally:
                sink.locks[idx].release()
        return None

    def prefetch(self):
        self._check()

    def preview_after(self, last_source=None, *, fence=None):

        after = tuple(value for value in (last_source, fence) if value is not None)
        with self.condition:
            deadline = time.monotonic() + 3
            while True:
                self._check()
                frame = self.latest
                if frame is not None and all(
                    0 < ((frame.source_frame_id - value) & 0xFFFFFFFF) < 0x80000000
                    for value in after
                ):
                    return frame
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "No fresh full-RAW frame after the capture fence"
                    )
                self.condition.wait(0.05)

    def acquire(
        self, roi=False, x=0, y=0, parent=None, width=512, height=288, *, channel=1
    ):
        start = time.monotonic()
        if roi:
            if channel not in (1, 2):
                raise ValueError("Native ROI channel must be 1 or 2")
            if any(type(v) is not int for v in (x, y, width, height)) or not (
                0 <= x < 1920
                and 0 <= y < 1080
                and 1 <= width <= 1920 - x
                and 1 <= height <= 1080 - y
                and width * height <= 518400
            ):
                raise ValueError("Native rectangle exceeds frame or 518400 pixels")
            stream = self.streams[channel]
            key = (x, y, width, height)
            if channel == 2 or stream.key != key:
                with self.condition:
                    self.sequence = (self.sequence + 1) & 0xFFFFFFFF or 1
                    command = RectCommand(
                        self.sequence, True, x, y, width, height, parent or 0, channel
                    )
                result = self._call("_roi", command).result(timeout=5)
                with self.condition:
                    stream.consumed = result[1].source_frame_id
                    while (
                        stream.frames
                        and not 0
                        < (
                            (stream.frames[0].source_frame_id - stream.consumed)
                            & 0xFFFFFFFF
                        )
                        < 0x80000000
                    ):
                        stream.frames.popleft()
                    if channel == 1 and self.roi1_fifo:
                        self.condition.notify_all()
            else:
                with self.condition:
                    deadline = start + 3
                    while not stream.frames:
                        self._check()
                        if time.monotonic() > deadline:
                            raise TimeoutError("No fresh native frame")
                        self.condition.wait(0.025)
                    self._check()
                    if stream.key != key:
                        raise RuntimeError(
                            "Native ROI acquisition superseded by a different rectangle"
                        )
                    if channel == 1 and self.roi1_fifo:
                        frame = stream.frames.popleft()
                        self.condition.notify_all()
                    else:
                        discarded = len(stream.frames) - 1
                        if discarded:
                            stream.stats["roi_python_latest_skipped"] = (
                                stream.stats.get("roi_python_latest_skipped", 0)
                                + discarded
                            )
                            stream.stats["roi_python_last_skipped_source"] = (
                                stream.frames[-2].source_frame_id
                            )
                        frame = stream.frames.pop()
                        stream.frames.clear()
                    geometry = (
                        frame.x,
                        frame.y,
                        frame.rgb.shape[1],
                        frame.rgb.shape[0],
                    )
                    if (frame.request_id, geometry) != (stream.request_id, key):
                        raise RuntimeError("Native ROI FIFO epoch mismatch")
                    if stream.consumed is not None:
                        stream.stats["roi_consumer_skips"] = (
                            stream.stats.get("roi_consumer_skips", 0)
                            + ((frame.source_frame_id - stream.consumed) & 0xFFFFFFFF)
                            - 1
                        )
                    stream.consumed = frame.source_frame_id
                result = (
                    RectCommand(
                        frame.request_id,
                        True,
                        x,
                        y,
                        width,
                        height,
                        parent or 0,
                        channel,
                    ),
                    frame,
                )
            stream.stats["roi_buffer_depth"] = len(stream.frames)
        else:
            with self.condition:
                deadline = start + 3
                while (
                    self.latest is None or self.latest.source_frame_id == self.consumed
                ):
                    self._check()
                    if time.monotonic() > deadline:
                        raise TimeoutError("No fresh overview")
                    self.condition.wait(0.05)
                self._check()
                frame = self.latest
                self.consumed = frame.source_frame_id
            result = (Command(frame.request_id), frame)
        self.stats["consumer_wait_ms"] += (time.monotonic() - start) * 1000
        return result

    def _read(self, name):
        previous = self.reads.get(name)
        if previous is not None and previous.done():
            self.cached[name] = previous.result()
            del self.reads[name]
        if name not in self.reads:
            self.reads[name] = self._call(name)
        return self.cached.get(name)

    def feature_snapshot(self):
        return self._read("feature_snapshot")

    def industrial_snapshot(self):
        return self._read("industrial_snapshot")

    def industrial_config(self, value=None):
        return self._call("industrial_config", value).result(timeout=5)

    def set_isp(self, config):
        return self._call("set_isp", config).result(timeout=5)

    def close(self):
        if self.closed:
            return
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("Camera owner did not finish STOP fence")
        if self.error is not None:
            raise RuntimeError("Camera shutdown failed") from self.error
