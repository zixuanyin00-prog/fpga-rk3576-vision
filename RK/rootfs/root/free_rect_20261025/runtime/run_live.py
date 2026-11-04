from pathlib import Path
from types import SimpleNamespace
import argparse
import json
import time
import signal
import os
import threading
import sys
from continuous_transport import ContinuousTransport
from metadata_image_panel import ImagePanel
from shared_preview import SharedPreview
from capture_scheduling import CaptureScheduling

ROOT = Path("/root/free_rect_20261025/runtime")


def main():
    sys.setswitchinterval(0.0005)
    from deployment_guard import validate_deployment

    validate_deployment(ROOT)
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=0)
    p.add_argument("--inference", action="store_true")
    p.add_argument("--coarse", action="store_true")
    p.add_argument("--monitors", nargs="+", choices=["HDMI-1"], default=["HDMI-1"])
    args = p.parse_args()
    scheduling = CaptureScheduling().validate(os.sched_getaffinity(0), args.monitors)
    application_scheduling = scheduling.apply_application()
    application_scheduling["python_switch_interval_ms"] = sys.getswitchinterval() * 1000
    from selected_model_gate import require_model_selection, ModelSelectionRequired

    selection_error = None
    selection = None
    if args.inference:
        try:
            selection = require_model_selection()
        except ModelSelectionRequired as e:
            selection_error = str(e)
            args.inference = False
    stop = False

    def halt(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    output_config = Path("/root/gpio_output_20261023/output_config.json")
    if output_config.exists() and json.loads(output_config.read_text()).get("enabled"):
        raise RuntimeError(
            "Physical GPIO output is enabled: unique-ID mode requires a separately validated action adapter"
        )
    camera = view = inference = console = None
    console_stop = threading.Event()
    controller = SimpleNamespace(
        scheduler=SimpleNamespace(tracks={}), collect=lambda wait=True: None
    )
    panel = ImagePanel(ROOT / "image_controls.json")

    def image_status():
        status = panel.status()
        environment = getattr(inference, "environment", None)
        if environment:
            status.update(
                sensor_exposure_control=environment.capability_verified,
                sensor_control=environment.bridge.state(),
            )
        if console:
            status.update(
                automatic_fpga=console.auto.state(),
                statistics_source="FPGA native ROI metadata",
                exposure_mode="manual",
            )
        return status

    initial = dict(
        results=[],
        image_controls=image_status(),
        roi_rate=0,
        preview_only=not args.inference,
    )
    try:
        view = SharedPreview(initial, monitors=tuple(args.monitors))
        from transport import RGBTransport
        from sensor_device import install_device_extension

        install_device_extension(RGBTransport)
        camera = ContinuousTransport(
            str(Path(__file__).resolve().with_name("librgb_device.so")),
            on_preview=view.preview,
            scheduling=scheduling,
            preview_sink=view.native_preview_sink(),
        )
        from capture_realtime import configure_roi_reader, verify_roi_reader

        roi_realtime = configure_roi_reader()
        from capture_realtime import configure_cpu_performance

        cpu_performance = configure_cpu_performance()
        restore = ROOT / "startup_sensor.json"
        if restore.exists():
            from sensor_device import SensorSession

            spec = json.loads(restore.read_text())
            if (
                spec["boot_id"]
                != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            ):
                raise RuntimeError("Exposure restore belongs to another boot")
            sensor = SensorSession(camera)
            state = sensor.wait(sensor.request(spec["shr0"], spec["gain_code"]))
            if state["shr0"] != spec["shr0"] or state["gain_code"] != spec["gain_code"]:
                raise RuntimeError("Exposure restore readback mismatch")
            (ROOT / "startup_sensor_restored.json").write_text(
                json.dumps(state, indent=2)
            )
            restore.rename(
                ROOT / ("startup_sensor.applied." + str(os.getpid()) + ".json")
            )
        restore_process = ROOT / "startup_process.json"
        if restore_process.exists():
            spec = json.loads(restore_process.read_text())
            if (
                spec["boot_id"]
                != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            ):
                raise RuntimeError("Processing restore belongs to another boot")
            revision = camera._call("process_submit", spec["words"]).result(timeout=2)
            deadline = time.monotonic() + 3
            while True:
                actual = camera._call("process_status").result(timeout=2)
                if actual["fault"]:
                    raise RuntimeError("Processing restore reported an FPGA fault")
                if (
                    actual["effective_revision"] == revision
                    and not actual["pending"]
                    and actual["source_valid"]
                ):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Processing restore timeout")
                time.sleep(0.01)
            if actual["effective"]["words"] != spec["words"]:
                raise RuntimeError("Processing restore readback mismatch")
            restore_process.rename(
                ROOT / ("startup_process.applied." + str(os.getpid()) + ".json")
            )
        restore_enhancement = ROOT / "startup_enhancement.json"
        if restore_enhancement.exists():
            from auto_isp import enhance_submit, enhance_status

            RGBTransport.enhance_submit = enhance_submit
            RGBTransport.enhance_status = enhance_status
            spec = json.loads(restore_enhancement.read_text())
            if (
                spec["boot_id"]
                != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            ):
                raise RuntimeError("Enhancement restore belongs to another boot")
            current = camera._call("enhance_status").result(timeout=2)
            revision = (
                max(current["revision"], spec["revision"]) + 1
            ) & 0xFFFFFFFF or 1
            camera._call(
                "enhance_submit",
                [spec["gain_q8"], spec["strength_q8"], revision, *spec["knots"]],
            ).result(timeout=2)
            deadline = time.monotonic() + 3
            while True:
                actual = camera._call("enhance_status").result(timeout=2)
                processing = camera._call("process_status").result(timeout=2)
                if (
                    actual["effective_revision"] == revision
                    and not processing["pending"]
                    and processing["source_valid"]
                ):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Enhancement restore timeout")
                time.sleep(0.01)
            if any(actual[k] != spec[k] for k in ("gain_q8", "strength_q8", "knots")):
                raise RuntimeError("Enhancement restore mismatch")
            restore_enhancement.rename(
                ROOT / ("startup_enhancement.applied." + str(os.getpid()) + ".json")
            )
        panel.update(camera, controller)
        from rect_pipeline import RectPipeline

        inference = RectPipeline(
            camera, ROOT, selection=selection, autostart=False, result_sink=view
        )

        application_scheduling["after_model_initialization"] = (
            scheduling.apply_application()
        )
        application_scheduling["python_switch_interval_ms"] = (
            sys.getswitchinterval() * 1000
        )
        controller = inference.control
        if inference.environment:
            from camera_console_bridge import CameraConsoleBridge

            console = CameraConsoleBridge(
                camera,
                ROOT,
                panel,
                controller,
                inference.environment.bridge,
                lambda: getattr(inference, "latest_native", None),
                console_stop,
                inference_enabled=bool(selection),
                control_lock=inference.lock,
                on_isp_generation=lambda value: setattr(inference, "generation", value),
            )
        if console:
            console.pipeline_state_lock = inference.lock
            console.control_epoch_provider = lambda: inference.environment.epoch
            console.pipeline_control_io = inference.control_io_transaction
            console.control_lock = inference.control_io_lock
        panel._vision_control_io_lock = inference.control_io_lock

        inference.generation = panel.generation
        inference.start()
        started = time.monotonic()
        last_report = 0
        next_statistics = 0.0
        statistics_period = 1.0 if os.environ.get("VISION_MINIMAL60") == "1" else 0.0
        next_ui = 0.0
        ui_period = 1.0 if os.environ.get("VISION_MINIMAL60") == "1" else 0.0
        results = []
        infer_stats = {}
        while not stop and (
            not args.seconds or time.monotonic() - started < args.seconds
        ):
            cycle_started = time.monotonic()
            if console:
                console.poll()
            for command in view.drain_commands():
                if command[0] == "count_reset":
                    if inference:
                        inference.counter.reset()
                        inference.counter.save(asynchronous=True)
                        next_statistics = 0.0
                else:
                    if inference:
                        with inference.control_io_lock:
                            panel.command(command)
                    else:
                        panel.command(command)
            frame = camera.latest
            if inference:
                if panel.dirty:
                    with inference.control_io_lock:
                        if panel.dirty:
                            with inference.control_io_transaction():
                                panel.update(camera, controller, frame)
                                inference.generation = panel.generation
                elif inference.generation != panel.generation:
                    inference.generation = panel.generation
                statistics_started = time.monotonic()
                if statistics_started >= next_statistics:
                    results, infer_stats = inference.snapshot()
                    next_statistics = statistics_started + statistics_period
            else:
                panel.update(camera, controller, frame)
                results = []
                infer_stats = {}

            if time.monotonic() >= next_ui:
                ui_counting = {
                    key: value
                    for key, value in infer_stats.get("counting", {}).items()
                    if key
                    not in (
                        "association_diagnostics",
                        "object_outcomes",
                        "global_motion",
                        "last_image_rejections",
                    )
                }
                row = dict(
                    display_revision=infer_stats.get("display_revision"),
                    model_id=infer_stats.get("model_id"),
                    counting=ui_counting,
                    fine_state=infer_stats.get("fine_state", "disabled"),
                    image_controls=image_status(),
                    roi_rate=infer_stats.get("roi_calls_per_second", 0),
                    coarse_rate=infer_stats.get("coarse_fps", 0),
                    coarse_coverage=infer_stats.get("coarse_source_coverage", 0),
                    preview_only=not args.inference,
                )
                row["inference_rectangle"] = (
                    infer_stats.get("settings", {}).get("rectangle")
                    if args.inference
                    else None
                )
                row["fine_rate"] = infer_stats.get(
                    "effective_result_fps",
                    infer_stats.get("fine_model_calls_per_second", 0),
                )
                view.show(row)
                next_ui = time.monotonic() + ui_period
            now = time.monotonic()
            if now - last_report >= 1:
                if inference:
                    inference.counter.save(asynchronous=True)

                preview_stats = camera.stats.copy()
                preview_stats["capture_roi"] = preview_stats["capture_roi"].copy()
                report = dict(
                    pid=os.getpid(),
                    started_monotonic=started,
                    display_outputs=list(args.monitors),
                    preview=preview_stats,
                    ui_dropped=view.dropped,
                    image_controls=image_status(),
                    inference_enabled=args.inference,
                    inference=infer_stats,
                    results=results,
                    recorded_monotonic=now,
                    application_scheduling=application_scheduling,
                    model_selection_error=selection_error,
                    ui_state_poll_hz=(
                        30 if os.environ.get("VISION_MINIMAL60") == "1" else 60
                    ),
                    ui_statistics_target_hz=1 if statistics_period else 60,
                    ui_state_target_hz=1 if ui_period else 60,
                    display_native_target_hz=60,
                    application_version="native1080-prc2-switches-20261029",
                    selected_model=(
                        {
                            k: selection.get(k)
                            for k in (
                                "project_name",
                                "run",
                                "training_started_beijing",
                                "training_completed_beijing",
                                "batch",
                                "fine_sha256",
                                "config_sha256",
                            )
                        }
                        if selection
                        else None
                    ),
                )
                if inference and inference.environment:
                    report["environment_identity"] = inference.environment.identity()
                report["roi_realtime"] = verify_roi_reader(roi_realtime)
                report["cpu_performance"] = cpu_performance
                inference.status_writer.offer(ROOT / "runtime_status.json", report)
                last_report = now
            if inference:
                time.sleep(
                    max(
                        0.0,
                        1 / (30 if os.environ.get("VISION_MINIMAL60") == "1" else 60)
                        - (time.monotonic() - cycle_started),
                    )
                )
            else:
                time.sleep(0.03)
    finally:
        console_stop.set()
        try:
            try:
                if console:
                    console.close()
            finally:
                if inference:
                    try:
                        inference.close()
                    finally:
                        inference.counter.save()
        finally:
            try:
                if camera:
                    camera.close()
            finally:
                if view:
                    view.close()


if __name__ == "__main__":
    main()
