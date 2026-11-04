from pathlib import Path
import json
import os
import time


def main():
    value = Path("/tmp/.X0-lock").read_text().strip()
    if not value.isdecimal() or int(value) <= 1:
        raise RuntimeError("Invalid Xorg :0 lock PID")
    pid = int(value)
    process = Path("/proc") / str(pid)
    if (process / "comm").read_text().strip() != "Xorg":
        raise RuntimeError("X0 lock does not identify Xorg")
    if b":0" not in (process / "cmdline").read_bytes().split(b"\0"):
        raise RuntimeError("Xorg lock display mismatch")
    target = {0, 1, 2}
    previous = os.sched_getaffinity(pid)
    if previous != target:
        os.sched_setaffinity(pid, target)
    actual = os.sched_getaffinity(pid)
    if actual != target:
        raise RuntimeError("Xorg main-thread affinity readback mismatch")
    print(
        json.dumps(
            dict(
                policy="xorg-main-little-v1",
                pid=pid,
                display=":0",
                previous_cpus=sorted(previous),
                actual_cpus=sorted(actual),
                changed=previous != actual,
                applies_to="main thread only",
                recorded_monotonic=time.monotonic(),
            )
        )
    )


if __name__ == "__main__":
    main()
