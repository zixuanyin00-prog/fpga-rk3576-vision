import argparse
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import time
from action_output import ActionConfig, PulseScheduler
from async_status_storage import Writer


class DeferredLine:

    def __init__(self):
        self.owner = None
        self.active_high = None

    def configure(self, active_high):
        if self.active_high == active_high:
            return
        self.close()
        if active_high is not None:
            from action_gpio import GPIOV2Line

            self.owner = GPIOV2Line(active_high)
        self.active_high = active_high

    def set(self, active):
        if self.owner is not None:
            self.owner.set(active)
        elif active:
            raise RuntimeError("Adapter active level has not been selected")

    def snapshot(self):
        return (
            self.owner.snapshot()
            if self.owner is not None
            else dict(
                configured=False,
                gpio="GPIO0_C1",
                chip_label="gpio0",
                line_offset=17,
                net="UART8_TX_M2",
                voltage=3.3,
                active=False,
                active_high=None,
                physical_high=None,
            )
        )

    def close(self):
        if self.owner is not None:
            self.owner.close()
            self.owner = None
        self.active_high = None


def main():
    parser = argparse.ArgumentParser(
        description="Independent TX/GPIO0_C1 owner: idle high, selected-pose pulses low."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, default=Path("/run/vision-action"))
    args = parser.parse_args()
    config = ActionConfig.load(args.config)
    if config.active_high is not False:
        raise ValueError("TX output requires idle high and active-low pulses")
    os.sched_setaffinity(0, {3})
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    os.setpriority(os.PRIO_PROCESS, 0, -10)
    args.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (args.runtime_dir / "owner.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    socket_path = args.runtime_dir / "events.sock"
    socket_path.unlink(missing_ok=True)
    line = DeferredLine()
    scheduler = PulseScheduler(config, line)
    sock = None
    stopping = False
    error = None
    config_error = None
    last_status = last_config = 0.0
    config_stamp = args.config.stat().st_mtime_ns
    status_path = args.runtime_dir / "status.json"
    status_writer = Writer()

    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(2))

    def stop(*_):
        nonlocal stopping
        stopping = True

    def write_status(stopped=False):
        pin = line.snapshot()
        row = dict(
            scheduler.snapshot(),
            pin=pin,
            physical_high=pin["physical_high"],
            physical_pin=pin["gpio"],
            voltage=pin["voltage"],
            configured=pin["configured"],
            pid=os.getpid(),
            recorded_monotonic=time.monotonic(),
            config_error=config_error,
            stopped=stopped,
            error=error,
            scheduling=dict(
                cpus=sorted(os.sched_getaffinity(0)),
                nice=os.getpriority(os.PRIO_PROCESS, 0),
                priority=os.sched_getparam(0).sched_priority,
                policy=os.sched_getscheduler(0),
            ),
            hard_realtime=False,
            status_storage=dict(status_writer.snapshot(), outside_gpio_timer=True),
        )

        status_writer.offer(status_path, row)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        line.configure(config.active_high)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        sock.setblocking(False)
        while not stopping:
            scheduler.tick()
            now = time.monotonic()
            if status_writer.error:
                raise RuntimeError(
                    "Action status storage failed: " + status_writer.error
                )
            due = min(
                [scheduler.active_until] if scheduler.active else [],
                default=now + 0.005,
            )
            if scheduler.pending:
                due = min(due, scheduler.pending[0][0])
            readable, _, _ = select.select(
                [sock], [], [], max(0.0, min(0.005, due - now))
            )
            if readable:
                for _ in range(64):
                    try:
                        data = sock.recv(32769)
                    except BlockingIOError:
                        break
                    try:
                        if len(data) > 32768:
                            raise ValueError("Oversized action datagram")
                        scheduler.receive(json.loads(data))
                    except (ValueError, TypeError, KeyError):
                        scheduler.metrics["invalid_datagram"] += 1
                    scheduler.tick()
            now = time.monotonic()
            scheduler.tick()
            if now - last_config >= 0.25:
                last_config = now
                try:
                    stamp = args.config.stat().st_mtime_ns
                    if stamp != config_stamp:
                        updated = ActionConfig.load(args.config)
                        if updated.active_high is not False:
                            raise ValueError("TX output requires active_high=false")
                        if updated == scheduler.config:
                            scheduler.metrics["ignored_same_config_saves"] += 1
                        else:
                            scheduler.inactive("configuration_changed")
                            line.configure(updated.active_high)
                            scheduler.reconfigure(updated)
                            config = updated
                        config_stamp = stamp
                    config_error = None
                except (OSError, ValueError, TypeError) as exc:
                    scheduler.reconfigure(replace(scheduler.config, enabled=False))
                    config_error = str(exc)
            scheduler.tick()
            now = time.monotonic()
            if now - last_status >= 1:
                write_status()
                last_status = now
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        try:
            scheduler.inactive()
            line.close()
        finally:
            if sock is not None:
                sock.close()
            socket_path.unlink(missing_ok=True)
            try:
                write_status(stopped=True)
            finally:
                status_writer.close()
                lock.close()


if __name__ == "__main__":
    main()
