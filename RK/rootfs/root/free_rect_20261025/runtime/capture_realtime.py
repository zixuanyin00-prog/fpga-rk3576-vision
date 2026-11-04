from pathlib import Path
import os


def _reader_tid(name):
    matches = [
        p
        for p in Path("/proc/self/task").iterdir()
        if (p / "comm").read_text().strip() == name
    ]
    if len(matches) != 1:
        raise RuntimeError("Exactly one existing native reader is required: " + name)
    return int(matches[0].name)


def _preview_state(tid):
    result = dict(
        tid=tid,
        expected_cpu=5,
        expected_policy="SCHED_FIFO",
        expected_priority=10,
        actual_policy=os.sched_getscheduler(tid),
        actual_priority=os.sched_getparam(tid).sched_priority,
        actual_cpus=sorted(os.sched_getaffinity(tid)),
    )
    result["verified"] = (
        result["actual_policy"] == os.SCHED_FIFO
        and result["actual_priority"] == 10
        and result["actual_cpus"] == [5]
    )
    if not result["verified"]:
        raise RuntimeError(
            "Preview reader must retain CPU5/FIFO10 before an ROI affinity trial"
        )
    return result


def configure_roi_reader():
    requested = os.environ.get("VISION_ROI_READER_CPU", "3").strip()
    if requested not in ("3", "5"):
        raise ValueError("VISION_ROI_READER_CPU must be3 or5; no affinity fallback")
    target = int(requested)
    tid = _reader_tid("rgb-roi-reader")
    before = dict(
        policy=os.sched_getscheduler(tid),
        priority=os.sched_getparam(tid).sched_priority,
        cpus=sorted(os.sched_getaffinity(tid)),
    )
    if before["cpus"] != [3]:
        raise RuntimeError(
            "Native ROI reader must initially be on CPU3 before reconfiguration"
        )
    preview = _preview_state(_reader_tid("rgb-dma-reader"))
    os.sched_setaffinity(tid, {target})
    os.sched_setscheduler(tid, os.SCHED_FIFO, os.sched_param(5))
    return verify_roi_reader(
        dict(
            tid=tid,
            before=before,
            expected_cpu=target,
            expected_policy="SCHED_FIFO",
            expected_priority=5,
            requested_cpu_env=requested,
            preview_tid=preview["tid"],
            preview_before=preview,
            preview_priority=10,
            preview_expected_cpu=5,
            roi_below_preview_priority=True,
            shared_cpu_with_preview=target == 5,
            native_empty_sleep_us=100,
        )
    )


def verify_roi_reader(record):
    tid = record["tid"]
    expected_cpu = record["expected_cpu"]
    if expected_cpu not in (3, 5):
        raise RuntimeError("Unrecognized recorded ROI reader target CPU")
    result = dict(
        record,
        actual_policy=os.sched_getscheduler(tid),
        actual_priority=os.sched_getparam(tid).sched_priority,
        actual_cpus=sorted(os.sched_getaffinity(tid)),
    )
    result["preview"] = _preview_state(record["preview_tid"])
    result["verified"] = (
        result["actual_policy"] == os.SCHED_FIFO
        and result["actual_priority"] == 5
        and result["actual_cpus"] == [expected_cpu]
        and result["preview"]["verified"]
    )
    if not result["verified"]:
        raise RuntimeError("Native ROI scheduling contract changed")
    return result


def configure_cpu_performance():
    records = []
    for policy in sorted(Path("/sys/devices/system/cpu/cpufreq").glob("policy*")):
        if (
            "performance"
            not in (policy / "scaling_available_governors").read_text().split()
        ):
            raise RuntimeError("Standard CPU performance governor unavailable")
        before = (policy / "scaling_governor").read_text().strip()
        maximum = int((policy / "scaling_max_freq").read_text())
        (policy / "scaling_governor").write_text("performance")
        after = (policy / "scaling_governor").read_text().strip()
        if (
            after != "performance"
            or int((policy / "scaling_max_freq").read_text()) != maximum
        ):
            raise RuntimeError("CPU scheduling policy readback mismatch")
        records.append(
            dict(
                policy=policy.name,
                before=before,
                after=after,
                maximum_khz=maximum,
                current_khz=int((policy / "scaling_cur_freq").read_text()),
                overclocked=False,
            )
        )
    return records
