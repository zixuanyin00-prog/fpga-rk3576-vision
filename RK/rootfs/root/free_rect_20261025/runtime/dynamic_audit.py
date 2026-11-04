from pathlib import Path
import hashlib
import json
import queue
import threading
import time

PROTOCOL = "native-effective15-v1"


class DynamicAudit:
    def __init__(self, root):
        self.root = Path(root) / "dynamic_audit"
        self.root.mkdir(exist_ok=True)
        self.queue = queue.Queue(4)
        self.lock = threading.RLock()
        self.request = None
        self.next_poll = 0
        self.next_sample = 0
        self.offered = 0
        self.dropped = 0
        self.error = None
        self.pending = 0
        self.attempts = 0
        self.seen = set()
        self.files = []
        self.rejections = []
        self.state = "idle"
        self.seen_hashes = set()
        self.status_pending = None
        self.status_event = threading.Event()
        (self.root / "protocol.json").write_text(
            json.dumps(
                dict(
                    protocol=PROTOCOL,
                    effective_target_max=15,
                    quality_scope="native transport and fixed ROI metadata; manual readability/all-object review still required",
                )
            )
        )
        self.thread = threading.Thread(
            target=self._write, daemon=True, name="dynamic-audit15-writer"
        )
        self.thread.start()
        threading.Thread(
            target=self._status_writer, daemon=True, name="dynamic-audit15-status"
        ).start()

    def _status(self):
        if not self.request:
            return
        data = dict(
            protocol=PROTOCOL,
            state=self.state,
            written=len(self.files),
            effective_native_originals=len(self.files),
            target=self.request["frames"],
            attempts=self.attempts,
            max_attempts=self.request["max_attempts"],
            files=list(self.files),
            rejections=list(self.rejections),
            pending=self.pending,
            dropped=self.dropped,
            quality_pass_scope="transport_and_scene_only",
            manual_review_required=True,
        )
        self.status_pending = (self.request["id"], data)
        self.status_event.set()

    def _status_writer(self):
        while True:
            self.status_event.wait()
            with self.lock:
                item = self.status_pending
                self.status_pending = None
                self.status_event.clear()
            if item is None:
                continue
            name, data = item
            try:
                folder = self.root / name
                folder.mkdir(exist_ok=True)
                tmp = folder / "status.tmp"
                tmp.write_text(json.dumps(data))
                for attempt in range(20):
                    try:
                        tmp.replace(folder / "status.json")
                        break
                    except PermissionError:
                        if attempt == 19:
                            raise
                        time.sleep(0.005)
                active = self.root / "active.tmp"
                active.write_text(
                    json.dumps(
                        dict(id=name, state=data["state"], pending=data["pending"])
                    )
                )
                active.replace(self.root / "active.json")
            except Exception as exc:
                self.error = repr(exc)

    def _terminal(self):
        if not self.request:
            return
        if self.pending == 0 and (
            len(self.files) >= self.request["frames"]
            or self.attempts >= self.request["max_attempts"]
            or self.state == "cancelled"
        ):
            if self.state != "cancelled":
                self.state = (
                    "complete"
                    if len(self.files) >= self.request["frames"]
                    else "exhausted"
                )
            self._status()
            self.request = None

    def offer(self, frame, objects, key, decisions, done, model_sha256, context=None):
        now = time.monotonic()
        with self.lock:
            if now >= self.next_poll:
                self.next_poll = now + 0.25
                if self.request:
                    cancel = self.root / ("cancel_" + self.request["id"] + ".json")
                    if cancel.exists():
                        cancel.unlink()
                        self.state = "cancelled"
                        self._terminal()
                path = self.root / "request.json"
                if path.exists() and self.request is None:
                    spec = json.loads(path.read_text())
                    path.unlink()
                    if (
                        not str(spec.get("id", "")).replace("_", "").isalnum()
                        or spec.get("protocol") != PROTOCOL
                        or type(spec.get("frames")) is not int
                        or not 1 <= spec["frames"] <= 15
                        or type(spec.get("max_attempts")) is not int
                        or not spec["frames"] <= spec["max_attempts"] <= 45
                        or not 0.2 <= spec.get("hz", 0) <= 2
                        or len(spec.get("native_roi", [])) != 4
                    ):
                        raise ValueError("Invalid bounded v15 capture request")
                    if (self.root / spec["id"]).exists():
                        raise ValueError("Capture transaction already exists")
                    self.request = spec
                    self.state = "running"
                    self.pending = self.attempts = self.offered = self.dropped = 0
                    self.files = []
                    self.rejections = []
                    self.seen = set(spec.get("exclude_source_frame_ids", []))
                    self.seen_hashes = set(spec.get("exclude_image_sha256", []))
                    self.next_sample = now
                    self._status()
            if (
                self.request is None
                or self.state != "running"
                or now < self.next_sample
            ):
                return
            if (
                len(self.files) + self.pending >= self.request["frames"]
                or self.attempts >= self.request["max_attempts"]
            ):
                return
            self.next_sample = now + 1.0 / self.request["hz"]
            self.attempts += 1
            spec = dict(self.request)
            source = getattr(frame, "source_frame_id", None)
            reason = None
            roi = spec["native_roi"]
            context = context or {}
            expected = dict(
                boot_id=spec.get("boot_id"),
                runtime_session_id=spec.get("runtime_session_id"),
                sensor_generation=spec.get("expected_sensor_generation"),
                region_version=spec.get("region_version"),
                isp_generation=spec.get("isp_generation"),
            )
            if not spec.get("environment_profile_digest") or any(
                v is None for v in expected.values()
            ):
                reason = "missing_verified_scene_identity"
            elif (
                any(context.get(k) != v for k, v in expected.items())
                or context.get("sensor_transition") is not False
            ):
                reason = "scene_identity_changed_or_transition"
            elif (
                type(spec.get("sensor_stable_source_id")) is not int
                or type(source) is not int
            ):
                reason = "missing_stable_source_fence"
            elif (
                not 0
                < ((source - spec["sensor_stable_source_id"]) & 0xFFFFFFFF)
                < 0x80000000
            ):
                reason = "source_not_after_stable_fence"
            elif type(source) is not int:
                reason = "missing_source_frame_id"
            elif source in self.seen:
                reason = "duplicate_source_frame_id"
            elif not getattr(frame, "roi", False) or [frame.x, frame.y] != roi[:2]:
                reason = "native_roi_mismatch"
            elif tuple(frame.rgb.shape) != (roi[3], roi[2], 3) or roi[2:] != [512, 288]:
                reason = "native_dimensions_mismatch"

            elif getattr(frame, "quality_ok", True) is False:
                reason = "hardware_quality_rejected"
            if reason:
                self.rejections.append(
                    dict(attempt=self.attempts, source_frame_id=source, reason=reason)
                )
                self._status()
                self._terminal()
                return
            raw = frame.rgb.tobytes()
            name = f"{self.attempts:04}"
            meta = dict(
                request=spec,
                index=self.offered,
                source_frame_id=source,
                origin=[frame.x, frame.y],
                native_roi=True,
                completed_monotonic=done,
                model_sha256=model_sha256,
                primary_track=key,
                objects=objects,
                applied=decisions,
                dropped_before=self.dropped,
                quality_pass_scope="transport_and_scene_only",
                manual_review_required=True,
            )
            meta.update(context)
            meta["environment_profile_digest"] = spec["environment_profile_digest"]
            try:
                self.queue.put_nowait((spec["id"], name, raw, meta))
            except queue.Full:
                self.dropped += 1
                self.rejections.append(
                    dict(attempt=self.attempts, reason="writer_queue_full")
                )
                self._status()
                self._terminal()
                return
            self.seen.add(source)
            self.pending += 1
            self.offered += 1
            self._status()

    def _write(self):
        while True:
            name, stem, pixels, meta = self.queue.get()
            digest = None
            added_hash = False
            try:
                folder = self.root / name
                folder.mkdir(exist_ok=True)
                if len(pixels) != 512 * 288 * 3:
                    raise ValueError("invalid native byte count")
                digest = hashlib.sha256(pixels).hexdigest()
                with self.lock:
                    if digest in self.seen_hashes:
                        raise ValueError("duplicate_image_sha256")
                    self.seen_hashes.add(digest)
                    added_hash = True
                meta["sha256"] = digest
                (folder / (stem + ".rgb")).write_bytes(pixels)
                (folder / (stem + ".json")).write_text(json.dumps(meta))
                with self.lock:
                    self.files.append(stem)
            except Exception as exc:
                with self.lock:
                    self.seen.discard(meta["source_frame_id"])
                    if added_hash:
                        self.seen_hashes.discard(digest)
                    self.rejections.append(
                        dict(file=stem, reason="write_failed", error=repr(exc))
                    )
            finally:
                with self.lock:
                    self.pending -= 1
                    self._status()
                    self._terminal()
                self.queue.task_done()

    def close(self):
        with self.lock:
            if self.request:
                self.state = "cancelled"
                self._terminal()
        self.queue.join()
