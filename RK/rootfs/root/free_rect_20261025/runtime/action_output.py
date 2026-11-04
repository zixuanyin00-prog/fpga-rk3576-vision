from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
import heapq
import json
import math
import os
from pathlib import Path
import re
import socket
import time

MAX_START_LATENESS_MS = 10


@dataclass(frozen=True)
class ActionConfig:
    schema_version: int = 1
    enabled: bool = False
    active_high: bool | None = None
    target_labels: list[str] = field(default_factory=list)
    model_sha256: str | None = None
    delay_ms: float = 0
    pulse_ms: float = 50
    trigger_mode: str = "center_line"

    def validate(self):
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or type(self.enabled) is not bool
        ):
            raise ValueError("Invalid action schema/enabled")
        if self.active_high is not None and type(self.active_high) is not bool:
            raise ValueError("active_high must be true, false or null")
        if (
            type(self.target_labels) is not list
            or len(self.target_labels) > 32
            or any(
                type(label) is not str or not 1 <= len(label) <= 128
                for label in self.target_labels
            )
            or len(set(self.target_labels)) != len(self.target_labels)
        ):
            raise ValueError("Select distinct model label IDs")
        if self.model_sha256 is not None and (
            type(self.model_sha256) is not str
            or re.fullmatch("[0-9a-f]{64}", self.model_sha256) is None
        ):
            raise ValueError("Invalid action model SHA256")
        for name, minimum, maximum in [("delay_ms", 0, 5000), ("pulse_ms", 1, 1000)]:
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise ValueError(name + " is outside its supported range")
        if self.trigger_mode != "center_line":
            raise ValueError("Action delay must start at the common ROI center line")
        if self.enabled and (
            self.active_high is None
            or not self.target_labels
            or self.model_sha256 is None
        ):
            raise ValueError(
                "Choose active level, target poses and model before enabling"
            )
        return self

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8"))).validate()

    def save(self, path):
        self.validate()
        path = Path(path)
        tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(asdict(self), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)


class ActionClient:

    def __init__(self, socket_path="/run/vision-action/events.sock"):
        self.socket_path = str(socket_path)
        self.sent = 0
        self.dropped = 0
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.setblocking(False)

    def publish(self, packet):
        packet = dict(packet, kind="source")
        packet.setdefault("sent_monotonic", time.monotonic())
        return self._send(packet)

    def _send(self, packet):
        if self.socket is None:
            return False
        raw = json.dumps(packet, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
        if len(raw) > 32768:
            self.dropped += 1
            return False
        try:
            self.socket.sendto(raw, self.socket_path)
        except OSError:
            self.dropped += 1
            return False
        self.sent += 1
        return True

    def invalidate(self):
        return self._send(dict(kind="disarm"))

    def snapshot(self):
        return dict(
            sent=self.sent, dropped=self.dropped, nonblocking=True, image_payload=False
        )

    def close(self):
        if self.socket is not None:
            self.invalidate()
            self.socket.close()
            self.socket = None


def validate_source(packet, now):

    if type(packet) is not dict or packet.get("kind") != "source":
        raise ValueError("Expected source event")
    if any(
        type(packet.get(name)) is not str or not 1 <= len(packet[name]) <= 1024
        for name in ("session", "epoch")
    ):
        raise ValueError("Invalid source session/epoch")
    source = packet.get("source_frame_id")
    if type(source) is not int or not 0 <= source <= 0xFFFFFFFF:
        raise ValueError("Invalid source frame")
    for name in ("capture_monotonic", "sent_monotonic"):
        value = packet.get(name)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= now - value <= 0.5
        ):
            raise ValueError("Stale or invalid source timestamp")
    if packet["capture_monotonic"] > packet["sent_monotonic"]:
        raise ValueError("Source timing reversed")
    if (
        type(packet.get("model_sha256")) is not str
        or re.fullmatch("[0-9a-f]{64}", packet["model_sha256"]) is None
    ):
        raise ValueError("Invalid model SHA256")
    rectangle = packet.get("rectangle")
    if (
        type(rectangle) is not list
        or len(rectangle) != 4
        or any(type(v) is not int for v in rectangle)
        or rectangle[0] < 0
        or rectangle[1] < 0
        or rectangle[2] <= 0
        or rectangle[3] <= 0
        or rectangle[0] + rectangle[2] > 1920
        or rectangle[1] + rectangle[3] > 1080
    ):
        raise ValueError("Invalid source rectangle")
    rows = packet.get("rows")
    if type(rows) is not list or len(rows) > 128:
        raise ValueError("Too many source rows")
    ids = set()
    for row in rows:
        if type(row) is not dict:
            raise ValueError("Invalid source row")
        identity = row.get("track_id")
        if type(identity) not in (int, str) or len(str(identity)) > 128:
            raise ValueError("Invalid track ID")
        identity = str(identity)
        if not identity or identity in ids:
            raise ValueError("Duplicate track ID")
        ids.add(identity)
        label = row.get("label")
        if label is not None and (type(label) is not str or not 1 <= len(label) <= 128):
            raise ValueError("Invalid pose label")
        if (
            type(row.get("confirmed")) is not bool
            or type(row.get("association_uncertain")) is not bool
        ):
            raise ValueError("Invalid identity confirmation")
        box = row.get("box")
        if (
            type(box) is not list
            or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or not 0 <= box[0] < box[2] <= 1920
            or not 0 <= box[1] < box[3] <= 1080
        ):
            raise ValueError("Invalid real observation box")
    return packet


class PulseScheduler:

    def __init__(self, config, line, clock=time.monotonic):
        self.config = config
        self.line = line
        self.clock = clock
        self.pending = []
        self.seen = OrderedDict()
        self.geometry = {}
        self.awaiting_confirmation = {}
        self.metrics = Counter()
        self.session = None
        self.epoch = None
        self.last_source = None
        self.last_capture = None
        self.last_heartbeat = None
        self.active = False
        self.active_since = None
        self.active_until = 0.0
        self.prime = True
        self.max_start_lateness_ms = 0.0
        self.last_event = None
        self.center_x = None
        self.model_matches = False
        self.last_drop = None
        self.last_clear = None
        self.last_completed_pulse = None
        self.line.set(False)

    def _drop(self, reason, now, track, source, due, deadline_kind="start"):
        deadline = (
            due + (MAX_START_LATENESS_MS / 1000 if deadline_kind == "start" else 0)
            if due is not None
            else None
        )
        self.last_drop = dict(
            reason=reason,
            recorded_monotonic=now,
            track_id=track,
            source_frame_id=source,
            deadline_kind=deadline_kind,
            deadline_delta_ms=(now - deadline) * 1000 if deadline is not None else None,
            due_monotonic=due,
        )
        self.metrics["dropped_events"] += 1

    def _finish_low(self):
        if self.active:
            self.line.set(False)
            ended = self.clock()
            self.last_completed_pulse = dict(
                started_monotonic=self.active_since,
                ended_monotonic=ended,
                low_duration_ms=(ended - self.active_since) * 1000,
                requested_ms=(self.active_until - self.active_since) * 1000,
                stop_lateness_ms=(ended - self.active_until) * 1000,
            )
            self.metrics["inactive_edges"] += 1
        self.active = False
        self.active_since = None

    def inactive(self, reason="external_inactive"):
        if self.active or self.pending or self.awaiting_confirmation:
            self.last_clear = dict(
                reason=reason,
                recorded_monotonic=self.clock(),
                pending_cleared=len(self.pending),
                awaiting_cleared=len(self.awaiting_confirmation),
                was_active=self.active,
            )
            self.metrics["clear_events"] += 1
            self.metrics["cancelled_events"] += len(self.pending)
            self.metrics["cancelled_confirmations"] += len(self.awaiting_confirmation)
            self.metrics["cancelled_active_pulses"] += int(self.active)
        self._finish_low()
        self.pending.clear()
        self.awaiting_confirmation.clear()
        self.active_until = 0.0

    def reconfigure(self, config):
        self.inactive("configuration_changed")
        self.config = config
        self.geometry.clear()
        self.prime = True
        self.metrics["configuration_changes"] += 1

    def _expire_confirmations(self, now):
        for track, (_, source, due) in list(self.awaiting_confirmation.items()):
            if now > due:
                del self.awaiting_confirmation[track]
                self.metrics["confirmation_expired"] += 1
                self._drop(
                    "confirmation_expired", now, track, source, due, "confirmation"
                )

    def receive(self, packet):
        now = self.clock()
        if type(packet) is dict and packet.get("kind") == "disarm":
            self.inactive("disarm")
            self.last_heartbeat = None
            self.geometry.clear()
            self.prime = True
            self.metrics["disarm_commands"] += 1
            return
        packet = validate_source(packet, now)
        identity = (packet["session"], packet["epoch"])
        if identity != (self.session, self.epoch):
            self.inactive("source_epoch_changed")
            self.session, self.epoch = identity
            self.last_source = None
            self.last_capture = None
            self.geometry.clear()
            self.seen.clear()
            self.prime = True
        source = packet["source_frame_id"]
        captured = packet["capture_monotonic"]
        if self.last_source is not None and (
            not 0 < ((source - self.last_source) & 0xFFFFFFFF) < 0x80000000
            or captured <= self.last_capture
        ):
            self.metrics["duplicate_or_old_source"] += 1
            return
        self.last_source = source
        self.last_capture = captured
        self.last_heartbeat = captured
        self.metrics["source_frames"] += 1
        self.model_matches = packet["model_sha256"] == self.config.model_sha256
        if not self.model_matches:
            self.inactive("model_mismatch")
            self.geometry.clear()
            self.prime = True
            self.metrics["model_mismatch"] += 1
            return
        self._expire_confirmations(now)
        center = packet["rectangle"][0] + packet["rectangle"][2] / 2
        self.center_x = center
        rows = packet["rows"]

        geometry = {
            track: prior
            for track, prior in self.geometry.items()
            if 0 < captured - prior[1] <= 0.25
            and ((source - prior[2]) & 0xFFFFFFFF) <= 8
        }
        for row in rows:
            track = str(row["track_id"])
            box = row["box"]
            x = (box[0] + box[2]) / 2
            previous = geometry.get(track)
            prior = self.geometry.get(track)
            if (
                previous is None
                and prior is not None
                and not self.prime
                and track not in self.seen
                and not row["association_uncertain"]
                and ((prior[0] < center <= x) or (prior[0] > center >= x))
            ):
                self.metrics["crossing_gap_rejected"] += 1
                self._drop("crossing_gap_rejected", now, track, source, None)
            if row["association_uncertain"]:
                geometry.pop(track, None)
                lost = self.awaiting_confirmation.pop(track, None)
                if lost is not None:
                    self.metrics["confirmation_identity_lost"] += 1
                    self._drop(
                        "confirmation_identity_lost",
                        now,
                        track,
                        lost[1],
                        lost[2],
                        "confirmation",
                    )
            else:
                geometry[track] = (x, captured, source)
            if self.prime:
                continue
            waiting = self.awaiting_confirmation.get(track)
            if track in self.seen and waiting is None:
                continue
            recovering = waiting is not None
            event_time = None
            crossing_source = source
            if recovering:
                event_time, crossing_source, due = waiting
            elif previous is not None and not row["association_uncertain"]:
                old_x, old_time, old_source = previous
                crossed = (old_x < center <= x) or (old_x > center >= x)
                if crossed:
                    self.seen[track] = None
                    if (
                        not 0 < captured - old_time <= 0.25
                        or ((source - old_source) & 0xFFFFFFFF) > 8
                    ):
                        self.metrics["crossing_gap_rejected"] += 1
                        continue
                    event_time = old_time + (captured - old_time) * (center - old_x) / (
                        x - old_x
                    )
            if event_time is None:
                continue
            self.seen[track] = None
            if not row["confirmed"] or row["association_uncertain"]:
                if not recovering:
                    self.metrics["crossing_unconfirmed_or_uncertain"] += 1
                    due = event_time + self.config.delay_ms / 1000
                    if self.config.enabled:
                        if now > due:
                            self.metrics["confirmation_expired"] += 1
                            self._drop(
                                "confirmation_expired",
                                now,
                                track,
                                crossing_source,
                                due,
                                "confirmation",
                            )
                        elif len(self.awaiting_confirmation) >= 512:
                            self.metrics["confirmation_capacity_dropped"] += 1
                            self._drop(
                                "confirmation_capacity_dropped",
                                now,
                                track,
                                crossing_source,
                                due,
                                "confirmation",
                            )
                        else:
                            self.awaiting_confirmation[track] = (
                                event_time,
                                crossing_source,
                                due,
                            )
                            self.metrics["awaiting_confirmation_events"] += 1
                continue
            if recovering:
                self.awaiting_confirmation.pop(track)
            if row["label"] not in self.config.target_labels:
                if recovering:
                    self.metrics["confirmation_nonselected"] += 1
                self.metrics["nonselected_pose"] += 1
                continue
            if not self.config.enabled:
                self.metrics["disabled_event"] += 1
                continue
            due = event_time + self.config.delay_ms / 1000
            end = due + self.config.pulse_ms / 1000
            covered = self.active and self.active_since <= due <= self.active_until
            if now > due + MAX_START_LATENESS_MS / 1000 and not covered:
                self.metrics["late_event"] += 1
                self._drop(
                    "start_deadline_at_receive", now, track, crossing_source, due
                )
                continue
            if len(self.pending) >= 128:
                self._drop("queue_overflow", now, track, crossing_source, due)
                self.inactive("queue_overflow")
                self.metrics["queue_overflow_inactive"] += 1
                break
            event_id = (crossing_source, track)
            heapq.heappush(self.pending, (due, end, event_id))
            self.last_event = dict(
                track_id=track,
                label=row["label"],
                source_frame_id=crossing_source,
                reference_monotonic=event_time,
                due_monotonic=due,
                end_monotonic=end,
                received_monotonic=now,
                trigger_mode=self.config.trigger_mode,
                confirmation_source_frame_id=source,
                confirmation_recovered=recovering,
            )
            self.metrics["scheduled_events"] += 1
            if recovering:
                self.metrics["recovered_confirmed_events"] += 1
        if len(geometry) > 512:
            geometry = dict(
                sorted(geometry.items(), key=lambda item: item[1][1], reverse=True)[
                    :512
                ]
            )
        self.geometry = geometry
        self.prime = False
        while len(self.seen) > 4096:
            self.seen.popitem(last=False)

    def tick(self):
        now = self.clock()
        if (
            not self.config.enabled
            or self.config.active_high is None
            or self.last_heartbeat is None
            or now - self.last_heartbeat > 0.5
        ):
            if self.active or self.pending or self.awaiting_confirmation:
                self.metrics["watchdog_or_disabled_inactive"] += 1
            reason = (
                "disabled"
                if not self.config.enabled
                else (
                    "active_level_unset"
                    if self.config.active_high is None
                    else (
                        "source_unavailable"
                        if self.last_heartbeat is None
                        else "source_stale"
                    )
                )
            )
            self.inactive(reason)
            return
        self._expire_confirmations(now)
        while self.pending and self.pending[0][0] <= now:
            due, end, (source, track) = heapq.heappop(self.pending)
            now = self.clock()
            covered = self.active and self.active_since <= due <= self.active_until
            if now > due + MAX_START_LATENESS_MS / 1000 and not covered:
                self.metrics["late_pulse"] += 1
                self._drop("start_deadline_at_tick", now, track, source, due)
                continue
            pulse = end - due

            if self.active and due <= self.active_until:
                started = max(due, self.active_since)
                self.active_until = max(self.active_until, started + pulse)
                self.metrics["merged_events"] += 1
            else:
                self._finish_low()
                self.line.set(True)
                self.active_since = self.clock()
                self.active = True
                started = self.active_since
                self.active_until = started + pulse
                self.metrics["active_edges"] += 1
            self.max_start_lateness_ms = max(
                self.max_start_lateness_ms, (started - due) * 1000
            )
            self.metrics["started_events"] += 1
            now = self.clock()
        if self.active and now >= self.active_until:
            self._finish_low()

    def snapshot(self):
        age = (
            (self.clock() - self.last_heartbeat) * 1000
            if self.last_heartbeat is not None
            else None
        )
        stale = age is None or age > 500
        off_reason = (
            (
                "disabled"
                if not self.config.enabled
                else (
                    "active_level_unset"
                    if self.config.active_high is None
                    else (
                        "source_stale"
                        if stale
                        else (
                            "model_mismatch"
                            if not self.model_matches
                            else (
                                "waiting_for_delay"
                                if self.pending
                                else (
                                    "waiting_for_confirmation"
                                    if self.awaiting_confirmation
                                    else "waiting_for_crossing"
                                )
                            )
                        )
                    )
                )
            )
            if not self.active
            else None
        )
        return dict(
            config=asdict(self.config),
            active=self.active,
            active_since_monotonic=self.active_since,
            active_until_monotonic=self.active_until if self.active else None,
            pulse_width_basis="actual_low_start",
            pending=len(self.pending),
            awaiting_confirmation=len(self.awaiting_confirmation),
            session=self.session,
            epoch=self.epoch,
            last_source_frame_id=self.last_source,
            source_age_ms=age,
            source_stale=stale,
            model_matches=self.model_matches,
            off_reason=off_reason,
            reason=off_reason or "active",
            max_start_lateness_ms=self.max_start_lateness_ms,
            last_event=self.last_event,
            last_drop=self.last_drop,
            last_clear=self.last_clear,
            last_completed_pulse=self.last_completed_pulse,
            maximum_allowed_start_lateness_ms=MAX_START_LATENESS_MS,
            metrics=dict(self.metrics),
            geometry_ids=len(self.geometry),
            consumed_ids=len(self.seen),
            fresh_source_watchdog_ms=500,
            pending_limit=128,
            dedup_limit=4096,
            geometry_limit=512,
            awaiting_confirmation_limit=512,
            logical_output=True,
            physical_pin="GPIO1_D2",
            voltage=1.8,
            center_line_x=self.center_x,
            center_crossing_direction="either",
            reference_clock="interpolated_real_source_capture_monotonic",
            display_predictions_used=False,
            config_changes_clear_pending_and_reprime_geometry=True,
        )
