from dataclasses import dataclass, asdict
import os
import threading


@dataclass(frozen=True)
class CaptureScheduling:
    capture_cpu: int = 6

    application_cpus: tuple = (
        (0, 1, 2) if os.environ.get("VISION_MINIMAL60") == "1" else (7,)
    )
    owner_nice: int = -10
    native_nice: int = -20

    def validate(self, available, monitors):
        displays = {"HDMI-1": 4}
        if not monitors or len(set(monitors)) != len(monitors):
            raise ValueError("Display outputs must be nonempty and unique")
        if any(m not in displays for m in monitors):
            raise ValueError("Unsupported display CPU mapping")
        workers = set(self.application_cpus)
        reserved = {displays[m] for m in monitors} | {3, 5}
        if (
            not workers
            or self.capture_cpu in workers
            or workers & reserved
            or self.capture_cpu in reserved
        ):
            raise ValueError(
                "Capture, application and enabled displays must use disjoint CPUs"
            )
        if not ({self.capture_cpu} | workers | reserved).issubset(set(available)):
            raise ValueError(
                "CPU partition unavailable; refusing an unverified fallback"
            )
        if not -20 <= self.native_nice < self.owner_nice <= 0:
            raise ValueError(
                "Native DMA must have higher scheduling weight than the Python owner"
            )
        return self

    def apply_application(self):
        return apply_current_thread(self.application_cpus, 0, "application")

    def apply_owner(self):
        return apply_current_thread(
            (self.capture_cpu,), self.owner_nice, "python-dma-owner"
        )

    def describe(self):
        return dict(
            asdict(self),
            roi_reader_cpu=3,
            roi_reader_nice=0,
            policy="SCHED_OTHER",
            system_cpu_exclusivity=False,
            preview_decode_cpu=5,
            preview_decode_nice=-20,
            preview_decode_policy="SCHED_FIFO",
            preview_decode_priority=10,
            display_cpu_map={"HDMI-1": 4},
            board_verified=False,
        )


def apply_current_thread(cpus, nice, role):

    required = (
        "sched_setaffinity",
        "sched_getaffinity",
        "setpriority",
        "getpriority",
        "sched_setscheduler",
        "sched_getscheduler",
    )
    if any(not hasattr(os, name) for name in required):
        raise RuntimeError("Linux thread scheduling APIs are required")
    os.sched_setaffinity(0, set(cpus))
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    os.setpriority(os.PRIO_PROCESS, 0, nice)
    actual = dict(
        role=role,
        tid=threading.get_native_id(),
        cpus=sorted(os.sched_getaffinity(0)),
        nice=os.getpriority(os.PRIO_PROCESS, 0),
        policy=os.sched_getscheduler(0),
    )
    if (
        actual["cpus"] != sorted(cpus)
        or actual["nice"] != nice
        or actual["policy"] != os.SCHED_OTHER
    ):
        raise RuntimeError("Scheduling readback mismatch: " + repr(actual))
    return actual
