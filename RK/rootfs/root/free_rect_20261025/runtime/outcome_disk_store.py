from collections import Counter
from pathlib import Path
import hashlib
import json
import os
import queue
import errno
import re
import stat
import threading
import time
import uuid

FOLDER = Path("/userdata/vision_int8_20261026_1338/object_outcomes_v3_global_motion")
MAX_BYTES = 512 * 1024**2
MIN_FREE_BYTES = 256 * 1024**2
MIN_FREE_INODES = 128
KEEP_SNAPSHOTS = 100
TITLES = dict(
    later_success="后来成功",
    final_unconfirmed="最终未确认",
    identity_lost="身份丢失",
    observing="跟踪中",
    recognized_waiting_exit="已确认，待离场",
    observation_interrupted="观察中断，结局未知",
)


class DiskStore:
    def __init__(self, folder=FOLDER, configure=True):
        self.folder = Path(folder)
        self.work = queue.Queue(1)
        self.stats = Counter()
        self.error = None
        self.storage_paused = False
        self.storage_pause_reason = None
        self.free_bytes = None
        self.free_inodes = None
        self.last_status_error = None
        self.used_bytes = 0
        self.configure = configure
        self.ready = False
        self.records = {}
        self.sizes = {}
        self.index = None
        self.journal_bytes = 0
        self.appended_since_compact = 0
        self.last_compact = 0.0
        self.boot_id = None
        self.invalid_snapshot_samples = []
        self.thread = threading.Thread(
            target=self._run, name="object-outcomes", daemon=True
        )
        self.thread.start()

    def offer(self, record):

        try:
            self.work.put_nowait(record)
            return True
        except queue.Full:
            self.stats["dropped_events"] += 1
            return False

    def snapshot(self):
        records = list(self.records.values())
        return dict(
            self.stats,
            pending=self.work.qsize(),
            error=self.error,
            worker_alive=self.thread.is_alive(),
            ready=self.ready,
            folder=str(self.folder),
            stored_bytes=self.used_bytes,
            quota_bytes=MAX_BYTES,
            minimum_free_bytes=MIN_FREE_BYTES,
            storage_paused=self.storage_paused,
            storage_pause_reason=self.storage_pause_reason,
            free_bytes=self.free_bytes,
            free_inodes=self.free_inodes,
            minimum_free_inodes=MIN_FREE_INODES,
            last_status_error=self.last_status_error,
            retention_limit=KEEP_SNAPSHOTS,
            retained_total=len(records),
            retention_scope="all_object_snapshots",
            retained_closed=sum(self._closed(record) for record in records),
            retained_active=sum(not self._closed(record) for record in records),
            invalid_snapshot_samples=list(self.invalid_snapshot_samples),
        )

    def _invalid_snapshot(self, path, error):
        self.stats["invalid_existing_snapshots"] += 1
        if len(self.invalid_snapshot_samples) < 8:
            self.invalid_snapshot_samples.append(dict(file=str(path), error=str(error)))

    @staticmethod
    def _closed(record):
        return (
            record.get("terminal") is True
            or record.get("status") == "observation_interrupted"
        )

    def _order(self, key):
        record = self.records[key]
        return (
            record.get("boot_id") == self.boot_id,
            float(record.get("recorded_monotonic", 0.0)),
            int(record.get("last_source_frame_id", 0)),
            key,
        )

    def _prune(self):
        snapshots = sorted(self.records, key=self._order)
        for key in snapshots[:-KEEP_SNAPSHOTS]:
            path = self.folder / (key + ".json")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(
                    "Refusing to remove a non-regular outcome snapshot: " + str(path)
                )
            path.unlink()
            self.used_bytes -= self.sizes.pop(key)
            del self.records[key]
            self.stats["expired_snapshots"] += 1

    def _compact(self):

        path = self.folder / "index.jsonl"
        if self.index is not None:
            self.index.flush()
            os.fsync(self.index.fileno())
            self.index.close()
            self.index = None
        old_size = path.stat().st_size if path.exists() else 0
        temporary = self.folder / "index.compact.tmp"
        stale_temporary_size = temporary.stat().st_size if temporary.exists() else 0
        with temporary.open("w", encoding="utf-8") as stream:
            for key in sorted(self.records, key=self._order):
                stream.write(
                    json.dumps(
                        dict(file=key + ".json", outcome=self.records[key]),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self.journal_bytes = path.stat().st_size
        self.used_bytes += self.journal_bytes - old_size - stale_temporary_size
        self.index = path.open("a", encoding="utf-8")
        self.appended_since_compact = 0
        self.last_compact = time.monotonic()
        self.stats["journal_compactions"] += 1

    def close(self, timeout=3.0):
        try:
            self.work.put(None, timeout=timeout)
        except queue.Full:
            self.error = "Outcome queue did not drain at shutdown"
            return False
        self.thread.join(timeout)
        return not self.thread.is_alive() and self.error is None

    def _run(self):
        try:
            if self.configure and hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, {0, 1, 2})
                os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
            self.folder.mkdir(parents=True, exist_ok=True)
            self.folder = self.folder.resolve()
            self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            for item in self.folder.iterdir():
                info = item.lstat()
                if stat.S_ISREG(info.st_mode):
                    self.used_bytes += info.st_size
                if not re.fullmatch(r"[0-9a-f]{64}\.json", item.name):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(
                        "Outcome snapshot is not a regular file: " + str(item)
                    )
                try:
                    record = json.loads(item.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    self._invalid_snapshot(item, exc)
                    continue
                if not isinstance(record, dict) or not isinstance(
                    record.get("object_id"), str
                ):
                    self._invalid_snapshot(item, "Missing object identity")
                    continue
                key = hashlib.sha256(record["object_id"].encode()).hexdigest()
                if key != item.stem or type(record.get("revision")) is not int:
                    self._invalid_snapshot(item, "Snapshot identity/revision mismatch")
                    continue
                self.records[key] = record
                self.sizes[key] = info.st_size
            self._prune()
            self._compact()
            self._last_expired = self.stats["expired_snapshots"]
            space_checked = status_written = last_sync = time.monotonic()
            space = os.statvfs(self.folder)
            free = int(space.f_bavail) * int(space.f_frsize)
            free_inodes = int(space.f_favail)
            self.free_bytes, self.free_inodes = free, free_inodes
            self.ready = True
            dirty = False
            while True:
                try:
                    record = self.work.get(timeout=0.1)
                except queue.Empty:
                    if dirty:
                        self.index.flush()
                        os.fsync(self.index.fileno())
                        last_sync = time.monotonic()
                        dirty = False
                    if (
                        self.appended_since_compact
                        and time.monotonic() - self.last_compact >= 2.0
                    ):
                        self._compact()
                    continue
                if record is None:
                    if self.appended_since_compact:
                        self._compact()
                    self.index.flush()
                    os.fsync(self.index.fileno())
                    return
                key = hashlib.sha256(record["object_id"].encode()).hexdigest()
                old = self.records.get(key)
                if old is not None and old.get("revision", 0) >= record["revision"]:
                    self.stats["duplicate_events"] += 1
                    continue
                target = self.folder / (key + ".json")
                text = json.dumps(record, ensure_ascii=False)
                encoded = len(text.encode("utf-8"))
                line = (
                    json.dumps(
                        dict(file=key + ".json", outcome=record), ensure_ascii=False
                    )
                    + "\n"
                )
                line_size = len(line.encode("utf-8"))
                now = time.monotonic()
                if self.appended_since_compact and (
                    self.appended_since_compact >= 128 or now - self.last_compact >= 2.0
                ):
                    self._compact()
                    dirty = False
                if (
                    self.used_bytes + encoded + line_size + 512 > MAX_BYTES
                    and self.appended_since_compact
                ):
                    self._compact()
                    dirty = False
                if now - space_checked >= 1.0:
                    space = os.statvfs(self.folder)
                    free = int(space.f_bavail) * int(space.f_frsize)
                    free_inodes = int(space.f_favail)
                    space_checked = now
                was_paused = self.storage_paused
                self.free_bytes, self.free_inodes = free, free_inodes
                self.storage_pause_reason = (
                    "sample_quota"
                    if self.used_bytes + encoded + line_size + 512 > MAX_BYTES
                    else (
                        "free_bytes"
                        if free < MIN_FREE_BYTES + encoded + line_size + 512
                        else (
                            "free_inodes" if free_inodes < MIN_FREE_INODES + 1 else None
                        )
                    )
                )
                self.storage_paused = self.storage_pause_reason is not None
                if self.storage_paused:
                    self.stats["storage_limited_events"] += 1
                if now - status_written >= 1.0 or (
                    self.storage_paused and not was_paused
                ):
                    status = self.folder / "status.json"
                    tempstatus = status.with_suffix(".tmp")
                    try:
                        tempstatus.write_text(
                            json.dumps(self.snapshot(), ensure_ascii=False),
                            encoding="utf-8",
                        )
                        tempstatus.replace(status)
                    except OSError as exc:
                        if exc.errno not in (errno.ENOSPC, errno.EDQUOT):
                            raise
                        self.stats["status_write_errors"] += 1
                        self.last_status_error = str(exc)
                    status_written = now
                if self.storage_paused:
                    continue
                old_size = self.sizes.get(key, 0)
                new_target = old is None
                temporary = target.with_suffix(".tmp")
                with temporary.open("w", encoding="utf-8") as stream:
                    stream.write(text)
                temporary.replace(target)
                self.index.write(line)
                dirty = True
                if now - last_sync >= 0.1:
                    self.index.flush()
                    os.fsync(self.index.fileno())
                    last_sync = time.monotonic()
                    dirty = False
                self.used_bytes += encoded - old_size + line_size
                self.journal_bytes += line_size
                self.records[key] = record
                self.sizes[key] = encoded
                self.appended_since_compact += 1
                before_prune = self.used_bytes
                self._prune()
                freed_snapshots = before_prune - self.used_bytes
                free -= max(0, encoded - old_size + line_size)
                free += freed_snapshots
                if new_target:
                    free_inodes -= 1

                free_inodes += self.stats["expired_snapshots"] - getattr(
                    self, "_last_expired", 0
                )
                self._last_expired = self.stats["expired_snapshots"]
                self.free_bytes, self.free_inodes = free, free_inodes
                self.stats["saved_events"] += 1
        except Exception as exc:
            self.error = str(exc)
            self.stats["errors"] += 1
        finally:
            if self.index is not None:
                self.index.close()
