from dataclasses import asdict, dataclass, replace
import time


@dataclass(frozen=True)
class ISPConfig:
    rg: int = 256
    bg: int = 256
    contrast: int = 256
    brightness: int = 0
    saturation: int = 256
    gaussian: int = 0
    he: int = 0
    revision: int = 0

    def __post_init__(self):
        if not all(type(value) is int for value in asdict(self).values()):
            raise ValueError("ISP values must be integers")
        if not (
            128 <= self.rg <= 512
            and 128 <= self.bg <= 512
            and 76 <= self.contrast <= 768
            and -100 <= self.brightness <= 100
            and self.saturation == 256
            and 0 <= self.gaussian <= 256
            and self.he == 0
            and 0 <= self.revision <= 65535
        ):
            raise ValueError("ISP config exceeds existing RGB driver limits")


@dataclass(frozen=True)
class FeedbackPolicy:
    low: float = 96
    high: float = 160
    step: int = 4
    max_offset: int = 24
    consecutive: int = 3
    interval_seconds: float = 1.0
    settle_samples: int = 3
    stale_seconds: float = 2.0

    def __post_init__(self):
        if not (
            0 < self.low < self.high < 255
            and type(self.step) is int
            and type(self.max_offset) is int
            and 1 <= self.step <= self.max_offset <= 100
            and type(self.consecutive) is int
            and self.consecutive >= 1
            and type(self.settle_samples) is int
            and self.settle_samples >= 0
            and self.interval_seconds > 0
            and self.stale_seconds > 0
        ):
            raise ValueError("Invalid feedback policy")


def summarize_snapshot(snapshot, width=1920, height=1080):

    if snapshot.get("fault"):
        raise ValueError("feature_fault")
    frame = snapshot.get("feature_frame")
    if type(frame) is not int or not 0 < frame <= 0xFFFFFFFF:
        raise ValueError("invalid_feature_frame")
    values = snapshot.get("tile_values")
    if not isinstance(values, list) or len(values) != 80:
        raise ValueError("expected_80_fields")
    counts, sums, means = [], [], []
    totals = [0, 0, 0]
    for index in range(16):
        count, luma, edge, foreground, highlight = values[index * 5 : index * 5 + 5]
        if not all(type(v) is int for v in (count, luma, edge, foreground, highlight)):
            raise ValueError("noninteger_feature")
        if (
            count != width * height // 16
            or not 0 <= luma <= 255 * count
            or not all(0 <= v <= count for v in (edge, foreground, highlight))
        ):
            raise ValueError("invalid_feature_range_or_geometry")
        counts.append(count)
        sums.append(luma)
        means.append(luma / count)
        for i, v in enumerate((edge, foreground, highlight)):
            totals[i] += v
    pixels = sum(counts)
    return dict(
        feature_frame=frame,
        pixels=pixels,
        mean_luma=sum(sums) / pixels,
        tile_mean_luma=means,
        dark_tiles=sum(v < 64 for v in means),
        bright_tiles=sum(v > 192 for v in means),
        edge_fraction=totals[0] / pixels,
        foreground_fraction=totals[1] / pixels,
        highlight_fraction=totals[2] / pixels,
        threshold_note="Mask thresholds not read back; masks are not feedback controls or noise/defect estimates",
    )


class AdaptiveISP:
    def __init__(self, baseline, policy=None, clock=time.monotonic):
        self.baseline = self.current = baseline
        self.policy = policy or FeedbackPolicy()
        self.clock = clock
        self.last_frame = None
        self.last_new = None
        self.last_change = float("-inf")
        self.direction = 0
        self.streak = 0
        self.settling = 0

    def evaluate(self, snapshot):
        now = self.clock()
        p = self.policy
        base = dict(
            mode="bounded_brightness",
            configured=asdict(self.current),
            effective_source_frame_unknown=True,
        )
        if snapshot is None:
            self.streak = 0
            return dict(base, status="missing_snapshot")
        try:
            info = summarize_snapshot(snapshot)
        except (ValueError, TypeError, KeyError) as error:
            self.streak = 0
            return dict(base, status="invalid_snapshot", reason=str(error))
        frame = info["feature_frame"]
        if self.last_frame is not None:
            delta = (frame - self.last_frame) & 0xFFFFFFFF
            if delta == 0:
                if now - self.last_new >= p.stale_seconds:
                    self.streak = 0
                return dict(
                    base,
                    status=(
                        "stale_snapshot"
                        if now - self.last_new >= p.stale_seconds
                        else "duplicate_snapshot"
                    ),
                )
            if delta >= 0x80000000:
                self.streak = 0
                return dict(base, status="out_of_order_snapshot")
            if now - self.last_new >= p.stale_seconds:
                self.streak = 0
        self.last_frame, self.last_new = frame, now
        base["features"] = info
        if self.settling:
            self.settling -= 1
            self.streak = 0
            return dict(base, status="settling", remaining_samples=self.settling)
        if info["dark_tiles"] >= 4 and info["bright_tiles"] >= 4:
            self.streak = 0
            return dict(base, status="mixed_lighting_hold")
        direction = (
            1 if info["mean_luma"] < p.low else -1 if info["mean_luma"] > p.high else 0
        )
        if not direction:
            self.streak = self.direction = 0
            return dict(base, status="within_band")
        self.streak = self.streak + 1 if direction == self.direction else 1
        self.direction = direction
        if self.streak < p.consecutive:
            return dict(base, status="collecting_evidence", consecutive=self.streak)
        if now - self.last_change < p.interval_seconds:
            return dict(base, status="cooldown")
        lower = max(-100, self.baseline.brightness - p.max_offset)
        upper = min(100, self.baseline.brightness + p.max_offset)
        target = max(lower, min(upper, self.current.brightness + direction * p.step))
        if target == self.current.brightness:
            return dict(base, status="brightness_limit")
        desired = replace(
            self.current,
            brightness=target,
            revision=(self.current.revision + 1) & 65535,
        )
        return dict(
            base,
            status="proposal",
            reason="dark" if direction > 0 else "bright",
            desired=asdict(desired),
        )

    def committed(self, config, applied):
        if applied:
            self.current = config
        self.last_change = self.clock()
        self.streak = 0
        self.settling = self.policy.settle_samples


class ISPSession:

    def __init__(
        self, camera, baseline, mode="observe", policy=None, clock=time.monotonic
    ):
        if mode not in ("observe", "apply"):
            raise ValueError("mode must be observe or apply")
        self.camera, self.mode = camera, mode
        self.controller = AdaptiveISP(baseline, policy, clock)
        self.needs_restore = False
        self.last_sent = None

    def start(self):

        self.camera.feature_snapshot()
        if self.mode == "apply":
            self.needs_restore = True
            self.camera.set_isp(self.controller.baseline)
            self.last_sent = self.controller.baseline
            self.controller.settling = self.controller.policy.settle_samples
            self.controller.last_change = self.controller.clock()

    def update(self, snapshot, scheduler=None):
        decision = self.controller.evaluate(snapshot)
        decision["execution_mode"] = self.mode
        decision["command_sent"] = False
        if decision["status"] == "proposal":
            config = ISPConfig(**decision["desired"])
            if self.mode == "apply":
                self.camera.set_isp(config)
                self.last_sent = config
                decision["command_sent"] = True

                if scheduler is not None:
                    for track in scheduler.tracks.values():
                        track["cache"] = None
                        track["fine_time"] = None
            self.controller.committed(config, applied=self.mode == "apply")
        return decision

    def restore(self):
        if self.needs_restore:
            config = replace(
                self.controller.baseline,
                revision=(
                    (self.last_sent.revision + 1) & 65535
                    if self.last_sent is not None
                    else self.controller.baseline.revision
                ),
            )
            self.camera.set_isp(config)
            self.needs_restore = False
            return dict(
                restored_baseline_parameters=True,
                config=asdict(config),
                effective_source_frame_unknown=True,
            )
        return dict(restored_baseline_parameters=False, reason="observe_only")
