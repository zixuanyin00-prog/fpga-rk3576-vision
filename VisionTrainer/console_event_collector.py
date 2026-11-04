from pathlib import Path
import bisect
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from console_store import ROOT, now
from console_event_retention import EventRetention

PROTOCOL = "vision-terminal-log-v1"
SPOOL = "/userdata/vision_terminal_logs_v1"
ROUND_BYTES = 4 * 1024**2
RATE_BYTES = 1024**2
QUOTA_BYTES = 3 * 1024**3
MIN_FREE_BYTES = 1024**3


class YieldRound(Exception):
    pass


class EventCollector:
    def __init__(self, settings, busy, stop=None, root=None, connect=None):
        self.settings = settings
        self.busy = busy
        self.stop = stop or threading.Event()
        self.root = Path(root) if root is not None else ROOT / "device_event_logs"
        self.connect = connect
        self.client = None
        self.s = None
        self.connection_key = None
        self.thread = None
        self.current = None
        self.cursor = ""
        self.initialized = False
        self.lock = threading.Lock()
        self.used_bytes = 0
        self.receiver_id = None
        self.retention = EventRetention(self.root, limit=100)
        self.retention_protected = set()
        self.retention_ready = False
        self.quota_bytes = QUOTA_BYTES
        self.minimum_free = MIN_FREE_BYTES
        self.data = dict(
            enabled=True,
            state="not_started",
            connected=False,
            folder=str(self.root),
            spool=SPOOL,
            backlog=None,
            writer_backlog=None,
            queued=None,
            dropped=None,
            writer_queued=None,
            writer_dropped=None,
            received=0,
            duplicates=0,
            acked=0,
            errors=0,
            stored_bytes=0,
            last_success=None,
            last_error=None,
            source={},
            remote_session_priority=None,
            quota_bytes=self.quota_bytes,
            minimum_free_bytes=self.minimum_free,
            retention_limit=100,
            retained_events=0,
            deleted_events=0,
            round_events=64,
            round_bytes=ROUND_BYTES,
            rate_bytes_per_second=RATE_BYTES,
            poll_seconds=2,
            backlog_poll_seconds=0.1,
        )

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(
                target=self._run, name="terminal-log-collector", daemon=True
            )
            self.thread.start()
        return self

    def snapshot(self):
        with self.lock:
            return dict(
                self.data, worker_alive=bool(self.thread and self.thread.is_alive())
            )

    def close(self):
        self.stop.set()
        if self.client:
            self.client.close()
        if self.thread:
            self.thread.join(1)

    def _update(self, **fields):
        with self.lock:
            self.data.update(fields)

    def _error(self, error):
        with self.lock:
            self.data["errors"] += 1
            self.data["last_error"] = str(error)[:500]

    def _write(self, path, raw):
        with path.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "events").mkdir(exist_ok=True)
        (self.root / ".partial").mkdir(exist_ok=True)
        receiver = self.root / "receiver.json"
        if receiver.exists():
            self.receiver_id = json.loads(receiver.read_text(encoding="utf-8"))[
                "receiver_id"
            ]
        else:
            self.receiver_id = uuid.uuid4().hex
            temp = receiver.with_suffix(".tmp")
            self._write(temp, json.dumps(dict(receiver_id=self.receiver_id)).encode())
            os.replace(temp, receiver)
        self.retention.initialize()
        self.used_bytes = sum(
            p.stat().st_size for p in self.root.rglob("*") if p.is_file()
        )
        received = sum(p.is_dir() for p in (self.root / "events").iterdir())
        self._update(
            stored_bytes=self.used_bytes,
            received=received,
            retained_events=received,
            receiver_id=self.receiver_id,
        )
        self.initialized = True

    def _disconnect(self):
        if self.s:
            self.s.close()
        if self.client:
            self.client.close()
        self.s = self.client = None
        if self.current:

            self.current["offset"] = 0
            self.current["digest"] = hashlib.sha256()
        self._update(connected=False)

    def _open(self, settings):
        import paramiko

        self._update(
            remote_session_priority=dict(
                status="opening", scope="this_sftp_server_from_proc_self"
            )
        )
        if self.connect:
            self.client = self.connect(settings)
        else:
            from service import Service

            service = Service()
            service.settings = settings
            self.client = service.connect()

        channel = self.client.get_transport().open_session(timeout=5)
        channel.settimeout(5)
        channel.exec_command(
            "exec taskset -c 0-2 nice -n 15 ionice -c 3 /usr/lib/openssh/sftp-server"
        )
        self.s = paramiko.SFTPClient(channel)
        server_pid = int(self.s.readlink("/proc/self").rsplit("/", 1)[-1])
        with self.s.open("/proc/self/status", "rb") as stream:
            status_raw = stream.read(16384).decode()
        with self.s.open("/proc/self/stat", "rb") as stream:
            stat_raw = stream.read(8192).decode()
        status = {
            key: value.strip()
            for key, value in (
                line.split(":", 1) for line in status_raw.splitlines() if ":" in line
            )
        }
        left = stat_raw.index("(")
        right = stat_raw.rindex(")")
        stat_fields = stat_raw[right + 2 :].split()
        nice = int(stat_fields[16])
        if (
            server_pid <= 1
            or status.get("Name") != "sftp-server"
            or int(status["Pid"]) != server_pid
            or int(stat_raw[:left].strip()) != server_pid
            or stat_raw[left + 1 : right] != "sftp-server"
            or status.get("Cpus_allowed_list") != "0-2"
            or nice != 15
        ):
            raise RuntimeError(
                "Dedicated SFTP process identity/CPU/nice readback differs"
            )

        stdin, stdout, stderr = self.client.exec_command(
            "LC_ALL=C ionice -p " + str(server_pid), timeout=5
        )
        stdin.close()
        try:
            io = stdout.read().decode().strip()
            error = stderr.read().decode(errors="replace")
            code = stdout.channel.recv_exit_status()
        finally:
            stdout.close()
            stderr.close()
        if code or not io.startswith("idle"):
            raise RuntimeError(
                error.strip() or "Dedicated SFTP I/O idle readback differs"
            )
        self._update(
            remote_session_priority=dict(
                status="applied",
                scope="this_sftp_server_from_proc_self",
                scope_confirmed=True,
                target_pid=server_pid,
                process_name="sftp-server",
                after=dict(cpus=[0, 1, 2], nice=nice, io=io),
            )
        )
        self._update(connected=True, state="idle")

    def _guard(self):
        reason = self.busy()
        if self.stop.is_set() or reason:
            self._update(
                state="yielding" if not self.stop.is_set() else "stopping",
                pause_reason=reason or "stopping",
            )
            raise YieldRound()

    def _pace(self, count):
        self.round_used += count
        delay = self.round_used / RATE_BYTES - (time.monotonic() - self.round_started)
        if delay > 0:
            self.stop.wait(delay)

    def _small(self, path, limit):
        self._guard()
        size = self.s.stat(path).st_size
        if size > limit:
            raise ValueError("Oversized control file: " + path)
        if size > ROUND_BYTES - self.round_used:
            raise YieldRound()
        with self.s.open(path, "rb") as stream:
            raw = stream.read(size)
        self._pace(len(raw))
        if len(raw) != size:
            raise OSError("Incomplete control file: " + path)
        return raw

    def _space(self, reserve):
        free = shutil.disk_usage(self.root).free
        limited = (
            self.used_bytes + reserve > self.quota_bytes
            or free - reserve < self.minimum_free
        )
        if limited:
            self._prune()
            free = shutil.disk_usage(self.root).free
            limited = (
                self.used_bytes + reserve > self.quota_bytes
                or free - reserve < self.minimum_free
            )
        self._update(
            storage_paused=limited, free_bytes=free, stored_bytes=self.used_bytes
        )
        if limited:
            self._update(state="storage_paused")
        return not limited

    def _prune(self):
        if not self.retention_ready:
            return
        protected = self.retention_protected.copy()
        if self.current:
            protected.add(self.current["identity"])
        result = self.retention.prune(protected=protected)
        self.used_bytes -= result["freed_bytes"]
        with self.lock:
            self.data["deleted_events"] += result["deleted"]
            self.data.update(
                retained_events=result["retained"],
                stored_bytes=self.used_bytes,
                retention_error=result.get("error"),
            )

    def _account(self):
        if self.current:
            current = sum(
                p.stat().st_size
                for p in self.current["partial"].iterdir()
                if p.is_file()
            )
            self.used_bytes += current - self.current["accounted"]
            self.current["accounted"] = current
            self._update(stored_bytes=self.used_bytes)

    def _ack(self, identity, manifest_sha):
        path = SPOOL + "/acks/" + identity + ".json"
        try:
            old = json.loads(self._small(path, 4096))
        except FileNotFoundError:
            old = None
        if old is not None:
            if (
                old.get("event_id") != identity
                or old.get("manifest_sha256") != manifest_sha
            ):
                raise ValueError("Conflicting remote ACK: " + identity)
        else:
            self._guard()
            raw = json.dumps(
                dict(
                    event_id=identity,
                    manifest_sha256=manifest_sha,
                    receiver_id=self.receiver_id,
                    received_at=now(),
                ),
                separators=(",", ":"),
            ).encode()
            if len(raw) > ROUND_BYTES - self.round_used:
                raise YieldRound()
            temp = path + "." + self.receiver_id + ".tmp"
            with self.s.open(temp, "wb") as stream:
                stream.write(raw)
            self._pace(len(raw))
            self.s.rename(temp, path)
            with self.lock:
                self.data["acked"] += 1
        self.retention_protected.discard(identity)
        self._prune()

    def _begin(self, identity):
        remote = SPOOL + "/outbox/" + identity
        raw = self._small(remote + "/manifest.json", 8192)
        manifest = json.loads(raw)
        if manifest.get("protocol") != PROTOCOL or manifest.get("event_id") != identity:
            raise ValueError("Event protocol/ID mismatch: " + identity)
        object_id = manifest.get("object_id")
        if (
            not isinstance(object_id, str)
            or hashlib.sha256(object_id.encode()).hexdigest() != identity
        ):
            raise ValueError("Event does not match object lifetime ID: " + identity)
        files = manifest.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 2:
            raise ValueError("Invalid event file list")
        seen = set()
        for row in files:
            if not isinstance(row, dict):
                raise ValueError("Invalid event file descriptor")
            name = row.get("name")
            size = row.get("bytes")
            digest = row.get("sha256")
            if name not in ("event.json", "frame.rgb") or name in seen:
                raise ValueError("Invalid event payload filename")
            limit = 65536 if name == "event.json" else 518400 * 3
            if (
                type(size) is not int
                or not 0 < size <= limit
                or not isinstance(digest, str)
                or not re.fullmatch("[0-9a-f]{64}", digest)
            ):
                raise ValueError("Invalid event payload size/hash")
            seen.add(name)
        if "event.json" not in seen:
            raise ValueError("Missing event metadata")
        manifest_sha = hashlib.sha256(raw).hexdigest()
        target = self.root / "events" / identity
        if target.exists():
            receipt = json.loads((target / "receipt.json").read_text(encoding="utf-8"))
            if receipt["manifest_sha256"] != manifest_sha:
                raise ValueError("Conflicting committed event: " + identity)
            self._ack(identity, manifest_sha)
            with self.lock:
                self.data["duplicates"] += 1
            return False
        partial = self.root / ".partial" / identity
        partial.mkdir(exist_ok=True)
        accounted = sum(p.stat().st_size for p in partial.iterdir() if p.is_file())
        expected = sum(row["bytes"] for row in files) + len(raw) + 1024
        if not self._space(max(0, expected - accounted)):
            return False
        self.current = dict(
            identity=identity,
            remote=remote,
            manifest=manifest,
            manifest_raw=raw,
            manifest_sha=manifest_sha,
            files=files,
            index=0,
            offset=0,
            digest=hashlib.sha256(),
            partial=partial,
            target=target,
            accounted=accounted,
            expected=expected,
        )
        return True

    def _download(self):
        current = self.current
        if not self._space(max(0, current["expected"] - current["accounted"])):
            return False
        self._update(state="receiving")
        while current["index"] < len(current["files"]):
            self._guard()
            row = current["files"][current["index"]]
            if self.round_used >= ROUND_BYTES:
                return False
            with self.s.open(current["remote"] + "/" + row["name"], "rb") as remote:
                remote.seek(current["offset"])
                with (current["partial"] / row["name"]).open(
                    "ab" if current["offset"] else "wb"
                ) as local:
                    while (
                        current["offset"] < row["bytes"]
                        and self.round_used < ROUND_BYTES
                    ):
                        self._guard()
                        size = min(
                            65536,
                            row["bytes"] - current["offset"],
                            ROUND_BYTES - self.round_used,
                        )
                        raw = remote.read(size)
                        if not raw:
                            raise OSError(
                                "Incomplete event payload: " + current["identity"]
                            )
                        local.write(raw)
                        current["digest"].update(raw)
                        current["offset"] += len(raw)
                        self._pace(len(raw))
                    if current["offset"] < row["bytes"]:
                        return False
                    if current["digest"].hexdigest() != row["sha256"]:
                        raise ValueError(
                            "Event payload SHA256 mismatch: " + row["name"]
                        )
                    local.flush()
                    os.fsync(local.fileno())
            current["index"] += 1
            current["offset"] = 0
            current["digest"] = hashlib.sha256()
        event = json.loads(
            (current["partial"] / "event.json").read_text(encoding="utf-8")
        )
        if (
            event.get("protocol") != PROTOCOL
            or event.get("event_id") != current["identity"]
            or event.get("object_id") != current["manifest"]["object_id"]
        ):
            raise ValueError("Payload identity differs from committed manifest")
        image = event.get("image", {})
        has_rgb = any(r["name"] == "frame.rgb" for r in current["files"])
        if image.get("available") is True:
            size = image.get("size")
            rgb = next((r for r in current["files"] if r["name"] == "frame.rgb"), None)
            if (
                image.get("format") != "RGB888"
                or not isinstance(size, list)
                or len(size) != 2
                or any(type(v) is not int or v <= 0 for v in size)
                or rgb is None
                or size[0] * size[1] * 3 != rgb["bytes"]
            ):
                raise ValueError("Native RGB geometry differs from payload")
        elif has_rgb:
            raise ValueError("Unexpected RGB payload without available source image")
        self._write(current["partial"] / "manifest.json", current["manifest_raw"])
        receipt = dict(
            event_id=current["identity"],
            manifest_sha256=current["manifest_sha"],
            receiver_id=self.receiver_id,
            received_at=now(),
        )
        self._write(current["partial"] / "receipt.json", json.dumps(receipt).encode())
        self._account()
        os.replace(current["partial"], current["target"])
        self.retention.add(current["identity"])
        self.current = None
        with self.lock:
            self.data["received"] += 1
            self.data.update(last_success=now(), last_error=None)
        self._ack(current["identity"], current["manifest_sha"])
        return True

    def poll(self):

        self.round_used = 0
        self.round_started = time.monotonic()
        self._guard()
        self._update(pause_reason=None)
        try:
            try:
                source = json.loads(self._small(SPOOL + "/status.json", 65536))
            except FileNotFoundError:
                source = {}
            try:
                names = sorted(
                    n
                    for n in self.s.listdir(SPOOL + "/outbox")
                    if re.fullmatch("[0-9a-f]{64}", n)
                )
            except FileNotFoundError:
                self._update(
                    state="waiting_for_spool", backlog=None, writer_backlog=None
                )
                return
            self.retention_protected = set(names)
            self.retention_ready = True
            self._prune()
            self._update(
                backlog=len(names),
                writer_backlog=len(names),
                writer_queued=source.get("queued"),
                writer_dropped=source.get("dropped_events"),
                source=source,
            )
            pivot = bisect.bisect_right(names, self.cursor)
            names = names[pivot:] + names[:pivot]
            attempted = 0
            if self.current:
                names = [n for n in names if n != self.current["identity"]]
                attempted = 1
                try:
                    if not self._download():
                        return
                except (ValueError, FileNotFoundError) as exc:
                    self._account()
                    self.current = None
                    self._error(exc)
            for identity in names:
                if attempted >= 64 or self.round_used >= ROUND_BYTES:
                    break
                self._guard()
                self.cursor = identity
                attempted += 1
                try:
                    if self._begin(identity) and not self._download():
                        return
                except FileNotFoundError:

                    self._account()
                    self.current = None
                except ValueError as exc:
                    self._account()
                    self.current = None
                    self._error(exc)
            self._update(
                state="storage_paused" if self.data.get("storage_paused") else "idle"
            )
        finally:
            self._account()

    def _run(self):
        if os.name == "nt":
            import ctypes

            kernel = ctypes.windll.kernel32
            kernel.GetCurrentThread.restype = ctypes.c_void_p
            kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lowered = kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)
            self._update(thread_priority="below_normal" if lowered else "unavailable")
        backoff = (2, 4, 8, 30)
        failures = 0
        try:
            while not self.stop.is_set():
                try:
                    self._guard()
                    if not self.initialized:
                        self._initialize()
                    settings = self.settings()
                    key = tuple(settings[k] for k in ("host", "username", "key"))
                    if self.s and key != self.connection_key:
                        self._disconnect()
                        self.current = None
                    if not self.s:
                        self._open(settings)
                        self.connection_key = key
                    self.poll()
                    failures = 0
                except YieldRound:
                    pass
                except Exception as exc:
                    if not self.stop.is_set():
                        self._error(exc)
                        self._update(state="offline")
                    self._disconnect()
                    if self.stop.wait(backoff[min(failures, 3)]):
                        break
                    failures += 1
                    continue
                self.stop.wait(0.1 if self.data.get("backlog") else 2)
        finally:
            self._disconnect()
            self._update(state="stopped")
