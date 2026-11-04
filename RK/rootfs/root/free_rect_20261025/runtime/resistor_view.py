import ctypes as C
import json
import math
import struct
import time
import os
from pathlib import Path
from collections import OrderedDict
from PIL import Image, ImageDraw, ImageFont
from screen_view import ScreenView as BaseView, Rect

BG = (12, 20, 31)
PANEL = (22, 34, 49)
INK = (225, 235, 244, 255)
MUTED = (145, 168, 188, 255)
ACCENT = (0, 221, 194)
GREEN = (65, 243, 153)


class ScreenView(BaseView):
    def __init__(self, commands=None):
        super().__init__(commands)
        self.cache = OrderedDict()
        self.rasterizations = 0
        self.layers = {}
        self.fonts = {
            n: ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", n)
            for n in (18, 20, 22, 28)
        }
        self.drag = None
        self.regions = {}
        self.actions = {}
        self.window_geometry = {}
        self.metrics = dict(main=0.0, redraw=0.0, roi=0.0, render_ms=0.0)
        self.counts = dict(
            start=time.monotonic(), frames=0, overviews=0, last=None, render=0.0
        )
        self.ui_error = ""
        signatures = {
            "SDL_GetRendererOutputSize": (
                [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)],
                C.c_int,
            ),
            "SDL_GetWindowPosition": (
                [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)],
                None,
            ),
            "SDL_GetWindowFlags": ([C.c_void_p], C.c_uint),
            "SDL_SetRenderTarget": ([C.c_void_p, C.c_void_p], C.c_int),
            "SDL_SetWindowFullscreen": ([C.c_void_p, C.c_uint], C.c_int),
            "SDL_SetWindowSize": ([C.c_void_p, C.c_int, C.c_int], None),
            "SDL_SetWindowPosition": ([C.c_void_p, C.c_int, C.c_int], None),
            "SDL_SetWindowBordered": ([C.c_void_p, C.c_int], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.s, name)
            fn.argtypes = args
            fn.restype = result
        if hasattr(self.s, "SDL_RenderSetVSync"):
            self.s.SDL_RenderSetVSync.argtypes = [C.c_void_p, C.c_int]
            self.s.SDL_RenderSetVSync.restype = C.c_int
            for window, renderer, *_ in self.windows:
                print(
                    "RENDER_VSYNC_OFF",
                    self.s.SDL_RenderSetVSync(renderer, 0),
                    flush=True,
                )
        self.origins = {}
        for window, *_ in self.windows:
            x = C.c_int()
            y = C.c_int()
            self.s.SDL_GetWindowPosition(window, C.byref(x), C.byref(y))
            self.origins[self.s.SDL_GetWindowID(window)] = (x.value, y.value)

    def text(self, r, text, x, y, width, color=INK, background=(0, 0, 0, 0), size=20):
        width = max(1, int(width))
        height = size + 12
        key = (r, text, width, color, background, size)
        cached = self.cache.get(key)
        if cached is None:
            im = Image.new("RGBA", (width, height), background)
            ImageDraw.Draw(im).text(
                (0, 0), str(text), font=self.fonts[size], fill=color
            )
            raw = im.tobytes()
            texture = self.s.SDL_CreateTexture(r, 0x16762004, 1, width, height)
            if not texture:
                raise RuntimeError(self.s.SDL_GetError())
            self.s.SDL_SetTextureBlendMode(texture, 1)
            self.s.SDL_UpdateTexture(texture, None, C.c_char_p(raw), width * 4)
            self.cache[key] = texture
            self.rasterizations += 1
            if len(self.cache) > 256:
                _, old = self.cache.popitem(last=False)
                self.s.SDL_DestroyTexture(old)
            cached = texture
        else:
            self.cache.move_to_end(key)
        self.s.SDL_RenderCopy(
            r, cached, None, C.byref(Rect(int(x), int(y), width, height))
        )

    def fill(self, r, box, color):
        self.s.SDL_SetRenderDrawColor(r, *color, 255)
        self.s.SDL_RenderFillRect(r, C.byref(Rect(*map(int, box))))

    def layer(self, r, key, signature, width, height, draw):
        old = self.layers.get((r, key))
        if old is None or old["size"] != (width, height):
            if old:
                self.s.SDL_DestroyTexture(old["texture"])
            texture = self.s.SDL_CreateTexture(r, 0x16762004, 2, width, height)
            if not texture:
                raise RuntimeError(self.s.SDL_GetError())
            old = dict(
                texture=texture, size=(width, height), signature=None, regions=[]
            )
            self.layers[r, key] = old
        if old["signature"] != signature:
            if self.s.SDL_SetRenderTarget(r, old["texture"]):
                raise RuntimeError(self.s.SDL_GetError())
            try:
                self.s.SDL_SetRenderDrawColor(r, *BG, 255)
                self.s.SDL_RenderClear(r)
                old["regions"] = draw()
            finally:
                self.s.SDL_SetRenderTarget(r, None)
            old["signature"] = signature
        return old["texture"], old["regions"]

    def header(self, r, w, full):
        regions = []
        model = self.metrics.get("model_id")
        title = f"工件视觉 · 多姿态 · {model}" if model else "工件视觉 · 预览与采集"
        self.text(r, title, 20, 28, w - 210, size=22)
        self.button(
            r,
            regions,
            "fullscreen",
            "退出全屏" if full else "进入全屏",
            w - 166,
            24,
            146,
            full,
        )
        cw = min(350, (w - 76) // 4)
        cards = [
            ("主画面新帧", f'{self.metrics["main"]:.1f} FPS', GREEN),
            (
                "按需精判",
                (
                    "等待目标"
                    if self.metrics.get("fine_state") == "waiting_for_targets"
                    else f'{self.metrics["roi"]:.1f} 次/秒'
                ),
                ACCENT,
            ),
            ("全景定位", f'{self.metrics.get("coarse",0):.1f} FPS', (117, 179, 255)),
            ("源帧覆盖", f'{self.metrics.get("coverage",0)*100:.1f}%', GREEN),
        ]
        if self.metrics.get("fine_state") == "central_roi_only":
            cards[1] = ("中央 ROI 精判", f'{self.metrics["roi"]:.1f} 次/秒', ACCENT)
            cards[2] = ("全景 NPU", "已关闭", (117, 179, 255))
            cards[3] = ("推理范围", "中央 512×288", GREEN)
        if self.metrics.get("fine_state") == "manual_rectangle_fine_tracking":
            cards[2] = ("定位方式", "人工矩形", (117, 179, 255))
            cards[3] = ("ID 确认", "独立源帧", GREEN)
        for i, (label, value, color) in enumerate(cards):
            x = 20 + i * (cw + 12)
            self.fill(r, (x, 76, cw, 70), PANEL)
            self.text(r, label, x + 12, 79, cw - 24, color=MUTED, size=18)
            self.text(r, value, x + 12, 104, cw - 24, color=(*color, 255), size=28)
        return regions

    def panel_layer(self, r, state, w):
        regions = []
        self.isp_panel(r, regions, state, 0, 0, w)
        return regions

    def button(self, r, regions, key, label, x, y, w, on=False):
        box = (int(x), int(y), int(w), 40)
        self.fill(r, box, (0, 100, 103) if on else (44, 63, 85))
        self.text(r, label, x + 10, y + 4, w - 18, size=18)
        regions.append(dict(key=key, box=box))

    def slider(self, r, regions, key, label, value, lo, hi, x, y, w, unit=""):
        shown = f"{value:+.0f}" if key == "brightness_value" else f"{value:.2f}{unit}"
        self.text(r, label + "  " + shown, x, y, w, size=18)
        rail = (x + 6, y + 39, w - 12, 6)
        self.fill(r, rail, (66, 85, 105))
        pos = rail[0] + round((rail[2] - 1) * max(0, min(1, (value - lo) / (hi - lo))))
        self.fill(r, (rail[0], rail[1], max(1, pos - rail[0]), 6), ACCENT)
        self.fill(r, (pos - 5, y + 30, 10, 24), ACCENT)
        regions.append(dict(key=key, box=(x, y + 26, w, 34), rail=rail, lo=lo, hi=hi))

    def set_slider(self, region, x):
        rail = region["rail"]
        ratio = max(0, min(1, (x - rail[0]) / max(1, rail[2] - 1)))
        self.send_control(
            region["key"], region["lo"] + ratio * (region["hi"] - region["lo"])
        )

    def fullscreen(self, wid):
        window = next(w[0] for w in self.windows if self.s.SDL_GetWindowID(w[0]) == wid)
        full = bool(self.s.SDL_GetWindowFlags(window) & 1)
        if self.s.SDL_SetWindowFullscreen(window, 0 if full else 0x1001):
            self.ui_error = self.s.SDL_GetError().decode()
            return
        self.s.SDL_SetWindowBordered(window, 1 if full else 0)
        if full:
            native = next(w for w in self.windows if w[0] == window)
            ww, hh = (1280, 800) if native[4] > 1100 else (720, 1000)
            self.s.SDL_SetWindowSize(window, ww, hh)
            ox, oy = self.origins[wid]
            self.s.SDL_SetWindowPosition(window, ox + 32, oy + 48)

    def handle_event(self, data):
        kind = struct.unpack_from("<I", data)[0]
        if kind not in (0x300, 0x400, 0x401, 0x402):
            return
        wid = struct.unpack_from("<I", data, 8)[0]
        if kind == 0x300:
            sym = struct.unpack_from("<i", data, 20)[0]
            if sym == 1073741892:
                self.fullscreen(wid)
            elif sym == 27:
                window = next(
                    (w[0] for w in self.windows if self.s.SDL_GetWindowID(w[0]) == wid),
                    None,
                )
                if window and self.s.SDL_GetWindowFlags(window) & 1:
                    self.fullscreen(wid)
            return
        x, y = struct.unpack_from("<ii", data, 20)
        if kind == 0x402:
            self.drag = None
            return
        if kind == 0x400:
            if self.drag and struct.unpack_from("<I", data, 16)[0] & 1:
                self.set_slider(self.drag, x)
            return
        if data[16] != 1:
            return
        for region in self.regions.get(wid, []):
            bx, by, bw, bh = region["box"]
            if not (bx <= x < bx + bw and by <= y < by + bh):
                continue
            key = region["key"]
            if key == "fullscreen":
                self.fullscreen(wid)
            elif "rail" in region:
                self.drag = region
                self.set_slider(region, x)
            elif key == "reset":
                self.send_control(key, True)
            elif key in ("wb", "brightness", "contrast"):
                value = not self.panel_state.get("auto", {}).get(key, False)
                self.panel_state.setdefault("auto", {})[key] = value
                self.send_control(key, value)
            break

    def isp_panel(self, r, regions, state, x, y, w):
        self.panel_state = state
        cfg = state["config"]
        auto = state["auto"]
        columns = 2 if w >= 600 else 1
        height = 434 if columns == 2 else 646
        self.fill(r, (x, y, w, height), PANEL)
        self.text(r, "FPGA ISP 调节", x + 16, y + 12, w - 32, size=22)
        gap = 8
        bw = (w - 32 - 2 * gap) // 3
        for i, (key, label) in enumerate(
            [("wb", "白平衡"), ("brightness", "亮度"), ("contrast", "对比度")]
        ):
            self.button(
                r,
                regions,
                key,
                label + (" 自动" if auto[key] else " 手动"),
                x + 16 + i * (bw + gap),
                y + 55,
                bw,
                auto[key],
            )
        controls = [
            ("rg_value", "红增益", cfg["rg"] / 256, 0.5, 2.0, "×"),
            ("bg_value", "蓝增益", cfg["bg"] / 256, 0.5, 2.0, "×"),
            ("brightness_value", "亮度补偿", cfg["brightness"], -100.0, 100.0, ""),
            ("contrast_value", "对比度", cfg["contrast"] / 256, 76 / 256, 3.0, "×"),
            ("gaussian_value", "高斯滤波", cfg["gaussian"] / 256, 0.0, 1.0, ""),
        ]
        sw = (w - 48) // 2 if columns == 2 else w - 32
        for i, (key, label, value, lo, hi, unit) in enumerate(controls):
            col = i % columns
            rr = i // columns
            self.slider(
                r,
                regions,
                key,
                label,
                value,
                lo,
                hi,
                x + 16 + col * (sw + 16),
                y + 112 + rr * 76,
                sw,
                unit,
            )
        foot = y + (352 if columns == 2 else 514)
        self.button(r, regions, "reset", "恢复默认", x + w - 142, foot, 126)
        self.text(r, "亮度为 ISP 补偿", x + 16, foot + 4, w - 172, color=MUTED, size=18)
        message = state.get("error") or self.ui_error or "参数已发送 FPGA"
        self.text(
            r,
            message,
            x + 16,
            foot + 54,
            w - 32,
            color=(
                (255, 165, 110, 255) if state.get("error") or self.ui_error else MUTED
            ),
            size=18,
        )

    def body_box(self, r, box, area, confirmed):
        x0, y0, x1, y1 = box
        rect = Rect(
            area.x + round(x0 * area.w / 1920),
            area.y + round(y0 * area.h / 1080),
            max(1, round((x1 - x0) * area.w / 1920)),
            max(1, round((y1 - y0) * area.h / 1080)),
        )
        for a, b, color in [
            (0, 8, (0, 0, 0)),
            (2, 6, GREEN if confirmed else (255, 209, 62)),
        ]:
            self.s.SDL_SetRenderDrawColor(r, *color, 255)
            for inset in range(a, b):
                if rect.w <= 2 * inset or rect.h <= 2 * inset:
                    break
                self.s.SDL_RenderDrawRect(
                    r,
                    C.byref(
                        Rect(
                            rect.x + inset,
                            rect.y + inset,
                            rect.w - 2 * inset,
                            rect.h - 2 * inset,
                        )
                    ),
                )

    def show(self, control, row):
        began = time.perf_counter()
        event = C.create_string_buffer(64)
        while self.s.SDL_PollEvent(event):
            self.handle_event(event.raw)
        self.flush_commands()
        frame = control.last_overview
        for window, r, texture, _, nativew, nativeh in self.windows:
            ww = C.c_int()
            hh = C.c_int()
            self.s.SDL_GetRendererOutputSize(r, C.byref(ww), C.byref(hh))
            w, h = ww.value or nativew, hh.value or nativeh
            wid = self.s.SDL_GetWindowID(window)
            regions = []
            full = bool(self.s.SDL_GetWindowFlags(window) & 1)
            self.s.SDL_SetRenderDrawColor(r, *BG, 255)
            self.s.SDL_RenderClear(r)
            signature = (
                w,
                full,
                round(self.metrics["main"], 1),
                round(self.metrics["roi"], 1),
                round(self.metrics["redraw"], 1),
            )
            header, header_regions = self.layer(
                r, "header", signature, w, 150, lambda: self.header(r, w, full)
            )
            self.s.SDL_RenderCopy(r, header, None, C.byref(Rect(0, 0, w, 150)))
            regions.extend(header_regions)
            landscape = w >= 1100
            if landscape:
                panelw = 416
                canvasw = w - panelw - 60
                canvash = max(100, h - 190)
                vw = min(canvasw, canvash * 512 // 288)
                vh = vw * 288 // 512
                area = Rect(20 + (canvasw - vw) // 2, 166 + (canvash - vh) // 2, vw, vh)
                px, py, pw = w - panelw - 20, 166, panelw
            else:
                vw = w - 40
                vh = vw * 288 // 512
                area = Rect(20, 166, vw, vh)
                px, py, pw = 20, 186 + vh, w - 40
            self.s.SDL_UpdateTexture(
                texture, None, C.c_void_p(frame.rgb.ctypes.data), 512 * 3
            )
            self.s.SDL_RenderCopy(r, texture, None, C.byref(area))
            for target in row["results"]:
                semantic = target.get("cached_fine")
                self.body_box(
                    r,
                    target["box"],
                    area,
                    bool(semantic and semantic.get("label") != "uncertain"),
                )
            state = row["image_controls"]
            signature = json.dumps(
                dict(
                    config=state["config"],
                    auto=state["auto"],
                    error=state["error"],
                    ui_error=self.ui_error,
                ),
                sort_keys=True,
            )
            ph = 434 if pw >= 600 else 646
            panel, panel_regions = self.layer(
                r, "isp", signature, pw, ph, lambda: self.panel_layer(r, state, pw)
            )
            self.s.SDL_RenderCopy(r, panel, None, C.byref(Rect(px, py, pw, ph)))
            for item in panel_regions:
                item = dict(item)
                box = item["box"]
                item["box"] = (box[0] + px, box[1] + py, box[2], box[3])
                if "rail" in item:
                    rail = item["rail"]
                    item["rail"] = (rail[0] + px, rail[1] + py, rail[2], rail[3])
                regions.append(item)
            self.regions[wid] = regions
            xx = C.c_int()
            yy = C.c_int()
            self.s.SDL_GetWindowPosition(window, C.byref(xx), C.byref(yy))
            self.window_geometry[wid] = dict(
                window_id=wid,
                x=xx.value,
                y=yy.value,
                w=w,
                h=h,
                fullscreen=full,
                regions=regions,
                camera_area=[area.x, area.y, area.w, area.h],
            )
            self.s.SDL_RenderPresent(r)
        p = self.counts
        p["frames"] += 1
        p["render"] += time.perf_counter() - began
        if p["last"] != frame.source_frame_id:
            p["overviews"] += 1
            p["last"] = frame.source_frame_id
        elapsed = time.monotonic() - p["start"]
        if elapsed >= 1:
            self.metrics = dict(
                main=p["overviews"] / elapsed,
                redraw=p["frames"] / elapsed,
                roi=row.get("roi_rate", 0),
                render_ms=p["render"] * 1000 / p["frames"],
            )
            report = dict(
                text_enabled=True,
                text_cached=True,
                main_only=True,
                preview_only=row.get("preview_only", False),
                screens=len(self.windows),
                measured_seconds=elapsed,
                window_redraws_per_second=self.metrics["redraw"],
                render_ms_mean=self.metrics["render_ms"],
                fresh_overview_updates_per_second=self.metrics["main"],
                roi_inferences_per_second=self.metrics["roi"],
                recorded_monotonic=time.monotonic(),
                cache_entries=len(self.cache),
                text_rasterizations=self.rasterizations,
                windows=list(self.window_geometry.values()),
                image_controls=row["image_controls"],
            )
            target = Path(os.environ.get("VISION_PERF_FILE", "screen_performance.json"))
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            temp.replace(target)
            p.update(start=time.monotonic(), frames=0, overviews=0, render=0.0)

    def close(self):
        for item in self.layers.values():
            self.s.SDL_DestroyTexture(item["texture"])
        self.layers.clear()
        for texture in self.cache.values():
            self.s.SDL_DestroyTexture(texture)
        self.cache.clear()
        super().close()
