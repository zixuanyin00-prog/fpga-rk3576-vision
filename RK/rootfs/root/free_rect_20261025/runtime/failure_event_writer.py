from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
import ctypes
import copy
import hashlib
import json
import os
import pickle
import queue
import shutil
import sys
import threading
import time
from failure_event_sd import inspect_sd

PROTOCOL = "vision-terminal-log-v1"
MEMORY_PENDING_BYTES = 16 * 1024 * 1024
MEMORY_PENDING_EVENTS = 512
DEFAULTS = dict(
    max_bytes=128 * 1024 * 1024,
    max_events=16,
    min_free_bytes=256 * 1024 * 1024,
    min_free_inodes=32,
    sd=dict(
        enabled=False,
        mount_point="/media/vision_sd",
        receive_history=False,
        max_bytes=128 * 1024 * 1024,
        min_free_bytes=256 * 1024 * 1024,
    ),
)


def atomic_json(path, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if os.name == "posix":
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return raw


class Writer:
    def __init__(self, folder, config=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        config_path = self.folder / "config.json"
        self.config = dict(DEFAULTS)
        if config is not None:
            self.config.update(config)
        elif config_path.exists():
            self.config.update(json.loads(config_path.read_text(encoding="utf-8")))
        else:
            atomic_json(config_path, self.config)
        self.config["max_events"] = min(16, self.config["max_events"])
        self.config["max_bytes"] = min(DEFAULTS["max_bytes"], self.config["max_bytes"])
        self.config["min_free_bytes"] = max(
            DEFAULTS["min_free_bytes"], self.config["min_free_bytes"]
        )
        self.config["min_free_inodes"] = max(32, self.config["min_free_inodes"])
        if self.config["sd"].get("receive_history", False):
            raise ValueError("SD history backfill is not enabled by this policy")
        self.config_mtime = (
            config_path.stat().st_mtime_ns if config_path.exists() else None
        )
        self.outbox = self.folder / "outbox"
        self.acks = self.folder / "acks"
        self.sd_receipts = self.folder / "sd_receipts"
        self.outbox.mkdir(exist_ok=True)
        self.acks.mkdir(exist_ok=True)
        self.sd_receipts.mkdir(exist_ok=True)
        self.stats = Counter()
        self.error = None
        self.storage_paused = False
        self.pause_reason = None
        self.pending = deque()
        self.pending_ids = set()
        self.pending_bytes = 0
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.spool_bytes = sum(
            p.stat().st_size for p in self.outbox.rglob("*") if p.is_file()
        )
        self.spool_events = sum(p.is_dir() for p in self.outbox.iterdir())
        self.sd = dict(enabled=False, available=False, reason="disabled")
        self.free_bytes = None
        self.free_inodes = None
        self.sd_write_state = dict(state="idle", reason=None)

    def save_sd(self, bundle, event, manifest_raw):
        expected = event["sd"]
        try:
            current = inspect_sd(self.config["sd"])
        except OSError as exc:
            self.sd = dict(
                enabled=True, available=False, reason="not_present", error=str(exc)
            )
            self.stats["sd_errors"] += 1
            self.sd_write_state = dict(
                state="waiting", reason="not_present", event_id=event["event_id"]
            )
            return False
        self.sd = current
        if (
            not current["available"]
            or current["mount_point"] != expected["mount_point"]
            or current.get("block_device") != expected.get("block_device")
        ):
            self.stats["sd_waiting_events"] += 1
            self.sd_write_state = dict(
                state="waiting",
                reason="eligible_sd_unavailable",
                event_id=event["event_id"],
            )
            return False
        destination = (
            Path(current["mount_point"])
            / "vision_terminal_logs_v1"
            / "outbox"
            / event["event_id"]
        )
        try:
            source_manifest = json.loads(manifest_raw)
            if (destination / "manifest.json").is_file():
                if (destination / "manifest.json").read_bytes() != manifest_raw:
                    self.sd.update(reason="existing_sd_manifest_conflict")
                    self.stats["sd_errors"] += 1
                    self.sd_write_state = dict(
                        state="error",
                        reason="existing_sd_manifest_conflict",
                        event_id=event["event_id"],
                    )
                    return False
            else:
                sd_root = destination.parent
                used = (
                    sum(p.stat().st_size for p in sd_root.rglob("*") if p.is_file())
                    if sd_root.exists()
                    else 0
                )
                needed = sum(row["bytes"] for row in source_manifest["files"]) + len(
                    manifest_raw
                )
                limits = self.config["sd"]
                if used + needed > limits.get(
                    "max_bytes", DEFAULTS["sd"]["max_bytes"]
                ) or shutil.disk_usage(
                    current["mount_point"]
                ).free - needed < limits.get(
                    "min_free_bytes", DEFAULTS["sd"]["min_free_bytes"]
                ):
                    self.sd.update(reason="sd_storage_limit")
                    self.stats["sd_storage_limited_events"] += 1
                    self.sd_write_state = dict(
                        state="paused",
                        reason="sd_storage_limit",
                        event_id=event["event_id"],
                    )
                    return False
                destination.mkdir(parents=True, exist_ok=True)
                for row in source_manifest["files"]:
                    temporary = destination / (row["name"] + ".tmp")
                    with (bundle / row["name"]).open("rb") as source, temporary.open(
                        "wb"
                    ) as target:
                        shutil.copyfileobj(source, target)
                        target.flush()
                        os.fsync(target.fileno())
                    temporary.replace(destination / row["name"])
                atomic_json(destination / "manifest.json", source_manifest)
                self.stats["sd_saved_events"] += 1
            atomic_json(
                self.sd_receipts / (event["event_id"] + ".json"),
                dict(
                    event_id=event["event_id"],
                    manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                    mount_point=current["mount_point"],
                ),
            )
            self.sd_write_state = dict(
                state="saved", reason=None, event_id=event["event_id"]
            )
            return True
        except OSError as exc:
            self.sd.update(available=False, reason="sd_write_error", error=str(exc))
            self.stats["sd_errors"] += 1
            self.sd_write_state = dict(
                state="error",
                reason="sd_write_error",
                event_id=event["event_id"],
                error=str(exc),
            )
            return False

    def snapshot(self):
        return dict(
            self.stats,
            protocol=PROTOCOL,
            enabled=True,
            worker_alive=True,
            worker_pid=os.getpid(),
            spool=str(self.folder),
            spool_bytes=self.spool_bytes,
            spool_events=self.spool_events,
            spool_limit_bytes=self.config["max_bytes"],
            spool_limit_events=self.config["max_events"],
            minimum_free_bytes=self.config["min_free_bytes"],
            free_bytes=self.free_bytes,
            minimum_free_inodes=self.config["min_free_inodes"],
            free_inodes=self.free_inodes,
            memory_pending_events=len(self.pending),
            memory_pending_bytes=self.pending_bytes,
            memory_pending_limit_events=MEMORY_PENDING_EVENTS,
            memory_pending_limit_bytes=MEMORY_PENDING_BYTES,
            memory_pending_limits=dict(
                events=MEMORY_PENDING_EVENTS, image_bytes=MEMORY_PENDING_BYTES
            ),
            deferred_events=self.stats["deferred_events"],
            actual_dropped_events=self.stats["actual_dropped_events"],
            stats_scope="writer_process_lifetime; counters reset at restart, committed spool survives",
            started_at=self.started_at,
            storage_paused=self.storage_paused,
            storage_pause_reason=self.pause_reason,
            sd=dict(self.sd),
            sd_write_state=dict(self.sd_write_state),
            error=self.error,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )

    def housekeeping(self):
        config_path = self.folder / "config.json"
        if config_path.exists() and config_path.stat().st_mtime_ns != self.config_mtime:
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            for key in ("max_bytes", "max_events", "min_free_bytes", "min_free_inodes"):
                if type(updated[key]) is not int or updated[key] < (
                    0 if key == "min_free_bytes" else 1
                ):
                    raise ValueError("Invalid terminal log quota: " + key)
            if updated["sd"].get("receive_history", False):
                raise ValueError("SD history backfill is not enabled by this policy")
            updated["max_events"] = min(16, updated["max_events"])
            updated["max_bytes"] = min(DEFAULTS["max_bytes"], updated["max_bytes"])
            updated["min_free_bytes"] = max(
                DEFAULTS["min_free_bytes"], updated["min_free_bytes"]
            )
            updated["min_free_inodes"] = max(32, updated["min_free_inodes"])
            self.config = updated
            self.config_mtime = config_path.stat().st_mtime_ns
        for ack in self.acks.glob("*.json"):
            identity = ack.stem
            if len(identity) != 64 or any(
                c not in "0123456789abcdef" for c in identity
            ):
                continue
            bundle = self.outbox / identity
            manifest = bundle / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                receipt = json.loads(ack.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.stats["invalid_ack_observations"] += 1
                continue
            if (
                receipt.get("event_id") != identity
                or not receipt.get("receiver_id")
                or not receipt.get("received_at")
                or receipt.get("manifest_sha256")
                != hashlib.sha256(manifest.read_bytes()).hexdigest()
            ):
                self.stats["invalid_ack_observations"] += 1
                continue
            event = json.loads((bundle / "event.json").read_text(encoding="utf-8"))
            if event.get("sd", {}).get("eligible"):
                saved = self.sd_receipts / (identity + ".json")
                saved_receipt = (
                    json.loads(saved.read_text(encoding="utf-8"))
                    if saved.exists()
                    else {}
                )
                if saved_receipt.get("manifest_sha256") != receipt[
                    "manifest_sha256"
                ] and not self.save_sd(bundle, event, manifest.read_bytes()):
                    continue
            size = sum(p.stat().st_size for p in bundle.iterdir() if p.is_file())
            shutil.rmtree(bundle)
            ack.unlink()
            self.spool_bytes -= size
            self.spool_events -= 1
            (self.sd_receipts / (identity + ".json")).unlink(missing_ok=True)
            self.stats["acked_events"] += 1
        self.free_bytes = shutil.disk_usage(self.folder).free
        self.free_inodes = (
            os.statvfs(self.folder).f_favail if hasattr(os, "statvfs") else None
        )
        self.pause_reason = (
            "spool_bytes"
            if self.spool_bytes >= self.config["max_bytes"]
            else (
                "spool_events"
                if self.spool_events >= self.config["max_events"]
                else (
                    "free_bytes"
                    if self.free_bytes < self.config["min_free_bytes"]
                    else (
                        "free_inodes"
                        if self.free_inodes is not None
                        and self.free_inodes < self.config["min_free_inodes"] + 6
                        else None
                    )
                )
            )
        )
        self.storage_paused = self.pause_reason is not None
        try:
            self.sd = inspect_sd(self.config["sd"])
        except OSError as exc:
            self.sd = dict(
                enabled=True, available=False, reason="not_present", error=str(exc)
            )
        self.flush_pending()

    def prepare(self, item):

        outcome, image, raw = item
        if not outcome.get("terminal") or outcome["status"] not in (
            "final_unconfirmed",
            "identity_lost",
        ):
            raise ValueError(
                "Only selected object terminal outcomes may enter this spool"
            )
        identity = hashlib.sha256(outcome["object_id"].encode("utf-8")).hexdigest()
        if (
            identity in self.pending_ids
            or (self.outbox / identity / "manifest.json").is_file()
        ):
            self.stats["duplicate_events"] += 1
            return None
        if raw is not None and (
            not image["available"]
            or len(raw) != image["size"][0] * image["size"][1] * 3
        ):
            raise ValueError("Native crop payload differs from its geometry")
        try:
            self.sd = inspect_sd(self.config["sd"])
        except OSError as exc:
            self.sd = dict(
                enabled=True, available=False, reason="not_present", error=str(exc)
            )
        received_at = datetime.now(timezone.utc).isoformat()
        event = dict(
            protocol=PROTOCOL,
            event_id=identity,
            object_id=outcome["object_id"],
            outcome=copy.deepcopy(outcome),
            image=copy.deepcopy(image),
            recorded_at=received_at,
            received_at=received_at,
            clock_basis="board_wall_clock_unverified; order by boot_id/source_frame_id; PC receipt time is receiver wall clock",
            image_scope="one native crop from an earlier real observation; not the disappearance frame",
            training_use="review_only; not automatically added to training",
            sd=dict(
                eligible=bool(self.sd["available"]),
                mount_point=self.sd.get("mount_point"),
                block_device=self.sd.get("block_device"),
                policy="new_events_only",
            ),
        )
        event_raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return dict(
            identity=identity,
            event=event,
            event_raw=event_raw,
            raw=raw,
            raw_bytes=len(raw) if raw is not None else 0,
            deferred=False,
        )

    def save(self, item):

        envelope = self.prepare(item)
        self.stats["received_events"] += 1
        if envelope is None:
            return True
        identity = envelope["identity"]
        self.flush_pending()
        if (
            len(self.pending) >= MEMORY_PENDING_EVENTS
            or self.pending_bytes + envelope["raw_bytes"] > MEMORY_PENDING_BYTES
        ):
            self.stats["memory_limited_events"] += 1
            self.stats["actual_dropped_events"] += 1
            self.stats["dropped_events"] += 1
            return False
        self.pending.append(envelope)
        self.pending_ids.add(identity)
        self.pending_bytes += envelope["raw_bytes"]
        self.stats["memory_admitted_events"] += 1
        self.flush_pending()
        if identity in self.pending_ids and not envelope["deferred"]:
            envelope["deferred"] = True
            self.stats["deferred_events"] += 1
            self.stats["storage_limited_events"] += 1
        return True

    def flush_pending(self):
        while self.pending:
            envelope = self.pending[0]
            if not self._save_prepared(envelope):
                if not envelope["deferred"]:
                    envelope["deferred"] = True
                    self.stats["deferred_events"] += 1
                    self.stats["storage_limited_events"] += 1
                break
            self.pending.popleft()
            self.pending_ids.remove(envelope["identity"])
            self.pending_bytes -= envelope["raw_bytes"]
        return not self.pending

    def _save_prepared(self, envelope):
        identity = envelope["identity"]
        event = envelope["event"]
        event_raw = envelope["event_raw"]
        raw = envelope["raw"]
        image = event["image"]
        bundle = self.outbox / identity
        if (bundle / "manifest.json").is_file():
            self.stats["duplicate_events"] += 1
            return True
        reserve = len(event_raw) + (len(raw) if raw is not None else 0) + 4096
        self.free_bytes = shutil.disk_usage(self.folder).free
        self.free_inodes = (
            os.statvfs(self.folder).f_favail if hasattr(os, "statvfs") else None
        )
        self.pause_reason = (
            "spool_bytes"
            if self.spool_bytes + reserve > self.config["max_bytes"]
            else (
                "spool_events"
                if self.spool_events >= self.config["max_events"]
                else (
                    "free_bytes"
                    if self.free_bytes - reserve < self.config["min_free_bytes"]
                    else (
                        "free_inodes"
                        if self.free_inodes is not None
                        and self.free_inodes < self.config["min_free_inodes"] + 6
                        else None
                    )
                )
            )
        )
        self.storage_paused = self.pause_reason is not None
        if self.storage_paused:
            return False
        bundle.mkdir(exist_ok=True)
        files = []
        for name, payload in [("event.json", event_raw)] + (
            [("frame.rgb", raw)] if raw is not None else []
        ):
            temporary = bundle / (name + ".tmp")
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(bundle / name)
            files.append(
                dict(
                    name=name,
                    bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
        manifest_raw = atomic_json(
            bundle / "manifest.json",
            dict(
                protocol=PROTOCOL,
                event_id=identity,
                object_id=event["object_id"],
                files=files,
            ),
        )
        self.spool_bytes += sum(row["bytes"] for row in files) + len(manifest_raw)
        self.spool_events += 1
        self.stats["saved_events"] += 1
        if raw is None:
            self.stats["image_unavailable_events"] += 1
        if event["sd"]["eligible"]:
            self.save_sd(bundle, event, manifest_raw)
        return True


def main():
    folder = Path(sys.argv[1])
    configure = sys.argv[2] == "1"
    if configure and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {0, 1, 2})
        os.setpriority(os.PRIO_PROCESS, 0, 15)
        os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))

        machine = os.uname().machine
        number = (
            30
            if machine in ("aarch64", "arm64")
            else 251 if machine == "x86_64" else None
        )
        if number is not None:
            ctypes.CDLL(None).syscall(number, 1, 0, 3 << 13)
    writer = Writer(folder)
    inbox = queue.Queue(1)

    def receive():
        try:
            while True:
                value = pickle.load(sys.stdin.buffer)
                inbox.put(value)
                if value is None:
                    return
        except EOFError:
            inbox.put(None)

    reader = threading.Thread(target=receive, name="terminal-log-input", daemon=True)
    reader.start()
    processed = 0
    last = 0.0
    final = False
    try:
        while not final:
            now = time.monotonic()
            if now - last >= 1.0:
                writer.housekeeping()
                last = now
            try:
                item = inbox.get(timeout=0.25)
            except queue.Empty:
                item = False
            if item is None:
                final = True
            elif item is not False:
                writer.save(item)
                processed += 1
            if time.monotonic() - last >= 0.9 or final or item is not False:
                status = writer.snapshot()
                status.update(
                    processed_events=processed,
                    final=final,
                    drained=final and not writer.pending,
                )
                atomic_json(writer.folder / "status.json", status)
                pickle.dump(status, sys.stdout.buffer, protocol=5)
                sys.stdout.buffer.flush()
    except (OSError, ValueError) as exc:
        writer.error = str(exc)
        status = writer.snapshot()
        status.update(final=True, drained=False, processed_events=processed)
        pickle.dump(status, sys.stdout.buffer, protocol=5)
        sys.stdout.buffer.flush()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
