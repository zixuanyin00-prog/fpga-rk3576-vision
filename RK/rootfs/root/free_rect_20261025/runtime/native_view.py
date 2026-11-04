import ctypes as C
import copy
import json
import os
import re
import subprocess
import time
import multiprocessing as mp
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import apple_glass
from PIL import Image, ImageDraw, ImageFont
from resistor_view import ScreenView as Widgets, BG
from pose_chart import ChartState, draw_chart, short_number

_ui_raw = None
_ui_capacity = 0
_ui_painter = None


def hdmi_mode(listing):

    match = re.search(r"^HDMI-1 connected[^\n]*(?:\n[ \t].*)*", listing, re.M)
    if match is None:
        raise RuntimeError("HDMI-1 is not connected")
    lines = match.group(0).splitlines()
    rates = next(
        (
            line.split()[1:]
            for line in lines[1:]
            if line.split() and line.split()[0] == "1920x1080"
        ),
        [],
    )
    supported = any(abs(float(rate.rstrip("*+")) - 60) < 0.005 for rate in rates)
    current = next((float(rate.rstrip("*+")) for rate in rates if "*" in rate), None)
    correct = (
        bool(re.match(r"^HDMI-1 connected primary 1920x1080\+0\+0(?:\s|$)", lines[0]))
        and current is not None
        and abs(current - 60) < 0.005
    )
    active_dsi = re.findall(
        r"^(DSI-\S+) connected(?: primary)? \d+x\d+[+-]\d+[+-]\d+", listing, re.M
    )
    return correct, supported, active_dsi


def init_ui(raw, capacity):
    global _ui_raw, _ui_capacity, _ui_painter
    _ui_raw = raw
    _ui_capacity = capacity
    os.sched_setaffinity(0, {0, 1, 2})
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    os.setpriority(os.PRIO_PROCESS, 0, 5)
    _ui_painter = ScreenView.__new__(ScreenView)
    _ui_painter.fonts = apple_glass.fonts()
    _ui_painter.rasterizations = 0
    _ui_painter.ui_error = ""


def build_ui(key, state, metrics, w, h, full, slot):
    built, pixels, regions, area = _ui_painter.build(key, state, metrics, w, h, full)
    np.frombuffer(_ui_raw, np.uint8, count=len(pixels), offset=slot * _ui_capacity)[
        :
    ] = np.frombuffer(pixels, np.uint8)
    return built, slot, regions, area


class ScreenView(Widgets):
    def __init__(self, commands=None):
        self.commands = commands
        self.pending_commands = {}
        self.drag = None
        self.regions = {}
        self.panel_state = {}
        self.current_state = {}
        self.ui_error = ""
        self.metrics = {}
        self.fonts = apple_glass.fonts()
        monitor = os.environ["VISION_MONITOR"]
        listing = subprocess.check_output(["xrandr", "--current"], text=True)
        if monitor == "HDMI-1":
            correct, supported, active_dsi = hdmi_mode(listing)
            if not supported:
                raise RuntimeError("HDMI-1 must support 1920x1080 at60Hz")
            if not correct or active_dsi:
                command = [
                    "xrandr",
                    "--output",
                    monitor,
                    "--mode",
                    "1920x1080",
                    "--rate",
                    "60",
                    "--pos",
                    "0x0",
                    "--primary",
                ]
                for output in active_dsi:
                    command.extend(["--output", output, "--off"])
                subprocess.check_call(command)
                listing = subprocess.check_output(["xrandr", "--current"], text=True)
                correct, _, active_dsi = hdmi_mode(listing)
                if not correct or active_dsi:
                    raise RuntimeError("HDMI1080p60 mode readback failed")
        record = next(
            m
            for m in re.findall(
                r"^(\S+) connected(?: primary)? (\d+)x(\d+)\+(\d+)\+(\d+)",
                listing,
                re.M,
            )
            if m[0] == monitor
        )
        _, w, h, x, y = record
        self.w, self.h, self.x, self.y = map(int, (w, h, x, y))
        self.origin = (self.x, self.y, self.w, self.h)
        self.full = True
        self.monitor = monitor
        self.window_geometry = {}
        self.cache = {}
        self.rasterizations = 0
        self.lib = C.CDLL(str(Path(__file__).with_name("librga_x11.so")))
        self.lib.rv_overlay_api_version.argtypes = []
        self.lib.rv_overlay_api_version.restype = C.c_uint
        if self.lib.rv_overlay_api_version() != 1:
            raise RuntimeError("Native display requires overlay API1")
        sig = {
            "rv_open": ([C.c_int] * 4, C.c_void_p),
            "rv_close": ([C.c_void_p], None),
            "rv_resize": ([C.c_void_p] + [C.c_int] * 4, C.c_int),
            "rv_event": ([C.c_void_p, C.c_void_p], C.c_int),
            "rv_cursor": ([C.c_void_p, C.c_int], None),
            "rv_error": ([C.c_void_p], C.c_char_p),
            "rv_stats": ([C.c_void_p, C.c_void_p], None),
            "rv_show": (
                [C.c_void_p] * 3
                + [C.c_ulonglong, C.c_uint]
                + [C.c_int] * 7
                + [C.c_void_p] * 2,
                C.c_int,
            ),
            "rv_show_overlay_v1": (
                [C.c_void_p] * 3
                + [C.c_ulonglong, C.c_uint]
                + [C.c_int] * 7
                + [C.c_void_p] * 4,
                C.c_int,
            ),
        }
        for name, (args, result) in sig.items():
            fn = getattr(self.lib, name)
            fn.argtypes = args
            fn.restype = result
        self.handle = self.lib.rv_open(self.x, self.y, self.w, self.h)
        if not self.handle:
            raise RuntimeError("RGA/X11 display initialization failed")
        context = mp.get_context("spawn")
        self.ui_capacity = max(self.w * self.h, 1280 * 800) * 4
        self.ui_raw = context.RawArray(C.c_ubyte, self.ui_capacity * 2)
        self.pool = ProcessPoolExecutor(
            max_workers=1,
            mp_context=context,
            initializer=init_ui,
            initargs=(self.ui_raw, self.ui_capacity),
        )
        self.ui_slot = 0
        self.ui_pointer = C.addressof(self.ui_raw)
        np.frombuffer(self.ui_raw, np.uint8, count=self.w * self.h * 4).reshape(-1, 4)[
            :
        ] = [31, 20, 12, 255]
        self.future = None
        self.built_key = None
        self.ui_serial = 1
        self.ui_render_due = 0.0
        self.area = self.layout(self.w, self.h)[0]
        self.measured = dict(main=0.0, redraw=0.0, roi=0.0)
        self.perf = dict(
            start=time.monotonic(),
            frames=0,
            render=0.0,
            first=None,
            last=None,
            previous=None,
        )
        self.native_stats = {}
        self.chart = ChartState()
        self.ui_builds = 0
        self.ui_metrics = {}
        self.ui_started = time.monotonic()
        self.glass_backdrop = bytes((12, 20, 31))
        self.glass_backdrop_size = (1, 1)

    def text(
        self,
        r,
        text,
        x,
        y,
        width,
        color=(225, 235, 244, 255),
        background=(0, 0, 0, 0),
        size=20,
    ):
        return apple_glass.text(self, text, x, y, width, color, background, size)

    def fill(self, r, box, color):
        return apple_glass.surface(self, box, color)

    def button(self, r, regions, key, text, x, y, width, active=False):
        return apple_glass.button(self, regions, key, text, x, y, width, active)

    @staticmethod
    def layout(w, h):
        if w >= 1100:
            pw = 416
            cw = w - pw - 60
            ch = max(100, h - 280)
            vw = min(cw, ch * 512 // 288) & ~3
            vh = (vw * 288 // 512) & ~1
            area = ((20 + (cw - vw) // 2) & ~1, (250 + (ch - vh) // 2) & ~1, vw, vh)
            panel = (w - pw - 20, 166, pw)
        else:
            vh = min((w - 40) * 288 // 512, max(72, h - 850)) & ~1
            vw = (vh * 512 // 288) & ~3
            area = ((w - vw) // 2, 250, vw, vh)
            panel = (20, 270 + vh, w - 40)
        return area, panel

    def build(self, key, state, metrics, w, h, full):
        apple_glass.begin(self, w, h, state)
        self.metrics = metrics
        regions = []
        self.text(
            None,
            "工业视觉 · " + str(metrics.get("model_id") or "相机预览"),
            20,
            28,
            w - 210,
            size=22,
        )
        self.button(
            None,
            regions,
            "fullscreen",
            "退出全屏" if full else "进入全屏",
            w - 166,
            24,
            146,
            full,
        )
        cw = min(350, (w - 76) // 4)
        chart = metrics["chart"]
        cards = [
            ("相机新画面", f'{metrics["main"]:.1f} FPS'),
            ("模型推理", f'{metrics["roi"]:.1f} 次/秒'),
            ("统计更新", f'{chart["interval"]:g} 秒/次'),
            ("当前在场", short_number(chart["latest"].get("stat:current_present"))),
        ]
        for i, (label, value) in enumerate(cards):
            x = 20 + i * (cw + 12)
            self.fill(None, (x, 76, cw, 70), (22, 34, 49))
            self.text(
                None, label, x + 12, 79, cw - 24, color=(145, 168, 188, 255), size=18
            )
            self.text(
                None, value, x + 12, 104, cw - 24, color=(65, 243, 153, 255), size=28
            )
        area, (px, py, pw) = self.layout(w, h)
        draw_chart(
            self,
            regions,
            chart,
            px,
            py,
            pw,
            h - 270 if w >= 1100 and h >= 1000 else h - 20,
        )
        cw = w - 476 if w >= 1100 else w - 40
        self.fill(None, (20, 158, cw, 78), (22, 34, 49))

        fields = chart["fields"][
            chart["page"]
            * chart.get("page_size", 8) : (chart["page"] + 1)
            * chart.get("page_size", 8)
        ]
        cell = (cw - 20) // max(1, min(4, len(fields)))
        for i, field in enumerate(fields[:4]):
            label = (
                field["title"] + " " + short_number(chart["latest"].get(field["key"]))
            )
            self.text(None, label, 30 + i * cell, 164, cell - 8, size=18)
        self.text(
            None,
            "绿：同帧确认 · 黄：待确认 · 紫：图像跟踪",
            30,
            199,
            cw - 160,
            size=18,
            color=(145, 168, 188, 255),
        )
        self.button(None, regions, "count_reset", "计数清零", 20 + cw - 126, 193, 116)
        return key, self.canvas.tobytes("raw", "BGRA"), regions, area

    def fullscreen(self, wid=1):
        if self.full:
            ox, oy, ow, oh = self.origin
            x, y = ox + 32, oy + 48
            w, h = (1280, 800) if ow >= 1100 else (720, 1000)
        else:
            x, y, w, h = self.origin
        if self.lib.rv_resize(self.handle, x, y, w, h):
            raise RuntimeError("Native window resize failed")
        self.x, self.y, self.w, self.h = x, y, w, h
        self.full = not self.full
        self.area = self.layout(w, h)[0]
        np.frombuffer(
            self.ui_raw,
            np.uint8,
            count=w * h * 4,
            offset=self.ui_slot * self.ui_capacity,
        ).reshape(-1, 4)[:] = [31, 20, 12, 255]
        self.ui_serial += 1
        self.built_key = None
        self.regions = {}

    def input(self, event):
        kind, x, y, key = event
        if kind == 4:
            if self.chart.keyboard(key, time.monotonic()):
                return
            if key == 0xFFC8:
                self.fullscreen()
            elif key == 0xFF1B and self.full:
                self.fullscreen()
            return
        if kind == 3:
            self.drag = None
            return
        if kind in (2, 5):
            hover = next(
                (
                    r["key"]
                    for r in self.regions.get(1, [])
                    if r["box"][0] <= x < r["box"][0] + r["box"][2]
                    and r["box"][1] <= y < r["box"][1] + r["box"][3]
                ),
                None,
            )
            if hover != getattr(self, "hover_key", None):
                self.hover_key = hover
                self.hover_serial = getattr(self, "hover_serial", 0) + 1
                self.lib.rv_cursor(self.handle, int(hover is not None))
            if self.drag:
                self.set_slider(self.drag, x)
            return
        if kind != 1:
            return
        for region in self.regions.get(1, []):
            bx, by, bw, bh = region["box"]
            if not bx <= x < bx + bw or not by <= y < by + bh:
                continue
            name = region["key"]
            if name == "fullscreen":
                self.fullscreen()
            elif name.startswith("chart:"):
                action = self.chart.action(name, time.monotonic())
                if action and action.get("reset_counts"):
                    self.send_control("count_reset", True)
            elif name == "count_reset":
                self.send_control(name, True)
            break

    def prepare(self, control, row):

        began = time.monotonic()
        event = (C.c_int * 4)()
        while self.lib.rv_event(self.handle, event):
            self.input(list(event))
        self.flush_commands()
        self.current_state = row["image_controls"]
        sampled = self.chart.update(row.get("counting", {}), began)
        if (
            sampled
            or not self.ui_metrics
            or began >= getattr(self, "console_ui_due", 0)
        ):
            self.console_ui_due = began + 1.0
            self.console_ui_serial = getattr(self, "console_ui_serial", 0) + 1
            self.ui_metrics = dict(
                self.measured,
                roi=row.get("fine_rate", 0),
                model_id=row.get("model_id"),
                counting=row.get("counting", {}),
            )
        key = (
            self.w,
            self.h,
            self.full,
            self.chart.serial,
            getattr(self, "console_ui_serial", 0),
            getattr(self, "hover_serial", 0),
        )
        if self.future and self.future.done():
            built, slot, regions, area = self.future.result()
            self.future = None
            if built[:3] == key[:3]:
                self.ui_slot = slot
                self.ui_pointer = C.addressof(self.ui_raw) + slot * self.ui_capacity
                self.ui_serial += 1
                self.regions[1] = regions
                self.area = area
                self.built_key = built
        if (
            self.future is None
            and key != self.built_key
            and began >= self.ui_render_due
        ):
            metrics = dict(self.ui_metrics, chart=self.chart.snapshot())
            state = dict(
                hover=getattr(self, "hover_key", None),
                backdrop=self.glass_backdrop,
                backdrop_size=self.glass_backdrop_size,
            )
            self.ui_render_due = time.monotonic() + 1.0
            self.future = self.pool.submit(
                build_ui,
                key,
                state,
                metrics,
                self.w,
                self.h,
                self.full,
                1 - self.ui_slot,
            )
            self.ui_builds += 1

    def show(self, control, row, prepared=False):
        began = time.monotonic()
        if not prepared:
            self.prepare(control, row)
        frame = control.last_overview

        if began >= getattr(self, "glass_backdrop_due", 0):
            apple_glass.frame_state(self, control, began)
        displayed = row["results"]
        boxes = np.array([r["box"] for r in displayed], np.float32).reshape(-1, 4)
        confirmed = np.array(
            [
                (
                    int(
                        bool(
                            r.get("tracking", {}).get("status") == "confirmed"
                            and not r.get("display_history")
                            and r.get("cached_fine")
                            and r["cached_fine"].get("label")
                            not in ("uncertain", "unreadable")
                        )
                    )
                    | (2 if r.get("display_pixel_tracked") else 0)
                )
                for r in displayed
            ],
            np.int32,
        )
        roi = row.get("inference_rectangle")
        roi = (
            np.array(roi, np.int32)
            if roi is not None and not row.get("preview_only", False)
            else None
        )
        rc = self.lib.rv_show_overlay_v1(
            self.handle,
            frame.rgb.ctypes.data,
            self.ui_pointer,
            self.ui_serial,
            frame.source_frame_id,
            frame.rgb.shape[1],
            frame.rgb.shape[0],
            *self.area,
            len(boxes),
            boxes.ctypes.data,
            confirmed.ctypes.data,
            roi.ctypes.data if roi is not None else None,
            None,
        )
        if rc < 0:
            raise RuntimeError(self.lib.rv_error(self.handle).decode())
        if not rc:
            return False
        stats = (C.c_double * 4)()
        self.lib.rv_stats(self.handle, stats)
        self.native_stats = dict(
            submitted=stats[0],
            completed=stats[1],
            last_completed_source=int(stats[2]),
            mean_rga_ms=stats[3],
        )
        now = time.monotonic()
        p = self.perf
        p["frames"] += 1
        p["render"] += now - began
        if p["first"] is None:
            p["first"] = now
        p["last"] = now
        elapsed = now - p["start"]
        if elapsed >= 1:
            self.measured = dict(
                main=p["frames"] / elapsed,
                redraw=p["frames"] / elapsed,
                roi=row.get("fine_rate", 0),
            )
            wnd = dict(
                window_id=1,
                x=self.x,
                y=self.y,
                w=self.w,
                h=self.h,
                fullscreen=self.full,
                regions=self.regions.get(1, []),
                camera_area=list(self.area),
            )
            report = dict(
                backend="RGA display scaling + XShm completion slots",
                text_cached=True,
                ui_async=True,
                main_only=True,
                preview_only=row.get("preview_only", False),
                screens=1,
                measured_seconds=elapsed,
                window_redraws_per_second=p["frames"] / elapsed,
                fresh_overview_updates_per_second=p["frames"] / elapsed,
                render_ms_mean=p["render"] * 1000 / p["frames"],
                roi_inferences_per_second=row.get("fine_rate", 0),
                native_roi_acquisitions_per_second=row.get("roi_rate", 0),
                display_max_boxes=None,
                display_boxes=len(displayed),
                inference_rectangle=roi.tolist() if roi is not None else None,
                box_outline_pixels=2,
                box_labels=False,
                recorded_monotonic=now,
                windows=[wnd],
                image_controls=self.current_state,
                native=self.native_stats,
                counting=row.get("counting", {}),
                camera_cpu_transforms=False,
                chart_interval_seconds=self.chart.interval,
                chart_sample_count=len(self.chart.history),
                chart_session_id=self.chart.session,
                chart_latest=self.chart.latest,
                chart_snapshot=self.chart.snapshot(),
                chart_enabled_fields=sorted(self.chart.enabled),
                ui_builds=self.ui_builds,
                ui_builds_per_second=self.ui_builds / max(0.001, now - self.ui_started),
            )
            target = Path(os.environ["VISION_PERF_FILE"])
            if hasattr(self, "write_report"):
                self.write_report(target, report)
            else:
                temp = target.with_suffix(".tmp")
                temp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                temp.replace(target)
            p.update(start=time.monotonic(), frames=0, render=0.0)
        return True

    def close(self):
        self.pool.shutdown(wait=True)
        self.lib.rv_close(self.handle)
