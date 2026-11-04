import ctypes as C
import json
import time
import subprocess
import struct
import queue
import os
from PIL import Image, ImageDraw, ImageFont


class Rect(C.Structure):
    _fields_ = [("x", C.c_int), ("y", C.c_int), ("w", C.c_int), ("h", C.c_int)]


class Info(C.Structure):
    _fields_ = [
        ("name", C.c_char_p),
        ("flags", C.c_uint),
        ("num", C.c_uint),
        ("formats", C.c_uint * 16),
        ("w", C.c_int),
        ("h", C.c_int),
    ]


class ScreenView:
    def __init__(self, commands=None):
        self.draw_text = os.environ.get("VISION_DRAW_TEXT", "1") != "0"
        self.main_only = os.environ.get("VISION_MAIN_ONLY", "0") == "1"
        self._perf = {
            "start": time.monotonic(),
            "frames": 0,
            "render_s": 0.0,
            "overviews": 0,
            "last_overview": None,
        }
        self.commands = commands
        self.regions = {}
        self.drag = None
        self.panel_state = {}
        self.pending_commands = {}
        self.s = C.CDLL("libSDL2-2.0.so.0")
        self.windows = []
        self.last = time.monotonic()
        self.fps = 0
        signatures = {
            "SDL_Init": ([C.c_uint], C.c_int),
            "SDL_SetHint": ([C.c_char_p, C.c_char_p], C.c_int),
            "SDL_GetError": ([], C.c_char_p),
            "SDL_CreateWindow": ([C.c_char_p] + [C.c_int] * 4 + [C.c_uint], C.c_void_p),
            "SDL_CreateRenderer": ([C.c_void_p, C.c_int, C.c_uint], C.c_void_p),
            "SDL_GetRendererInfo": ([C.c_void_p, C.POINTER(Info)], C.c_int),
            "SDL_CreateTexture": (
                [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int],
                C.c_void_p,
            ),
            "SDL_UpdateTexture": (
                [C.c_void_p, C.c_void_p, C.c_void_p, C.c_int],
                C.c_int,
            ),
            "SDL_SetTextureBlendMode": ([C.c_void_p, C.c_int], C.c_int),
            "SDL_SetRenderDrawColor": ([C.c_void_p] + [C.c_ubyte] * 4, C.c_int),
            "SDL_RenderClear": ([C.c_void_p], C.c_int),
            "SDL_RenderCopy": (
                [C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(Rect)],
                C.c_int,
            ),
            "SDL_RenderDrawRect": ([C.c_void_p, C.POINTER(Rect)], C.c_int),
            "SDL_RenderFillRect": ([C.c_void_p, C.POINTER(Rect)], C.c_int),
            "SDL_GetWindowID": ([C.c_void_p], C.c_uint),
            "SDL_RenderPresent": ([C.c_void_p], None),
            "SDL_PollEvent": ([C.c_void_p], C.c_int),
            "SDL_DestroyTexture": ([C.c_void_p], None),
            "SDL_DestroyRenderer": ([C.c_void_p], None),
            "SDL_DestroyWindow": ([C.c_void_p], None),
            "SDL_Quit": ([], None),
            "SDL_GL_GetProcAddress": ([C.c_char_p], C.c_void_p),
        }
        for name, (args, result) in signatures.items():
            f = getattr(self.s, name)
            f.argtypes = args
            f.restype = result
        self.s.SDL_SetHint(b"SDL_RENDER_DRIVER", b"opengles2")
        self.s.SDL_SetHint(b"SDL_RENDER_VSYNC", b"0")
        self.s.SDL_SetHint(b"SDL_RENDER_SCALE_QUALITY", b"nearest")
        self.s.SDL_SetHint(b"SDL_MOUSE_FOCUS_CLICKTHROUGH", b"1")
        self.s.SDL_SetHint(b"SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", b"0")
        if self.s.SDL_Init(0x20):
            raise RuntimeError(self.s.SDL_GetError())

        if not os.environ.get("VISION_MONITOR"):
            subprocess.run(
                [
                    "xrandr",
                    "--output",
                    "HDMI-1",
                    "--primary",
                    "--pos",
                    "0x0",
                    "--output",
                    "DSI-1",
                    "--right-of",
                    "HDMI-1",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        import re

        listing = subprocess.check_output(["xrandr", "--current"], text=True)
        monitors = re.findall(
            r"^(\S+) connected(?: primary)? (\d+)x(\d+)\+(\d+)\+(\d+)", listing, re.M
        )
        if os.environ.get("VISION_MONITOR"):
            monitors = [m for m in monitors if m[0] == os.environ["VISION_MONITOR"]]
        self.font = (
            ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 24)
            if self.draw_text
            else None
        )
        details = []
        for name, w, h, x, y in monitors:
            w, h, x, y = map(int, (w, h, x, y))
            window = self.s.SDL_CreateWindow(
                b"MobileNet - resistor 005638-box1", x, y, w, h, 0x1015
            )
            renderer = self.s.SDL_CreateRenderer(window, -1, 0x2)
            if not renderer:
                raise RuntimeError(self.s.SDL_GetError())
            info = Info()
            self.s.SDL_GetRendererInfo(renderer, C.byref(info))
            getter = self.s.SDL_GL_GetProcAddress(b"glGetString")
            gpu = (
                C.CFUNCTYPE(C.c_char_p, C.c_uint)(getter)(0x1F01).decode()
                if getter
                else "unknown"
            )
            if any(t in gpu.lower() for t in ("llvmpipe", "softpipe", "software")):
                raise RuntimeError("GPU presentation unavailable: " + gpu)
            texture = self.s.SDL_CreateTexture(renderer, 0x17101803, 1, 512, 288)
            roi = self.s.SDL_CreateTexture(renderer, 0x17101803, 1, 512, 288)
            if not texture or not roi:
                raise RuntimeError(self.s.SDL_GetError())
            self.windows.append((window, renderer, texture, roi, w, h))
            details.append(
                dict(
                    screen=name, width=w, height=h, renderer=info.name.decode(), gpu=gpu
                )
            )
        if not self.windows:
            raise RuntimeError("No active display")
        from pathlib import Path

        Path(
            "screen_status_" + os.environ.get("VISION_MONITOR", "all") + ".json"
        ).write_text(
            json.dumps(dict(displays=details, camera_cpu_transforms=False)),
            encoding="utf8",
        )
        print("SCREEN_READY " + json.dumps(details), flush=True)

    def text(
        self, r, text, x, y, width, color=(220, 230, 240, 255), background=(0, 0, 0, 0)
    ):
        if not self.draw_text:
            return

        im = Image.new("RGBA", (width, 36), background)
        ImageDraw.Draw(im).text((0, 0), text, font=self.font, fill=color)
        raw = im.tobytes()
        t = self.s.SDL_CreateTexture(r, 0x16762004, 1, width, 36)
        self.s.SDL_SetTextureBlendMode(t, 1)
        self.s.SDL_UpdateTexture(t, None, C.c_char_p(raw), width * 4)
        rect = Rect(x, y, width, 36)
        self.s.SDL_RenderCopy(r, t, None, C.byref(rect))
        self.s.SDL_DestroyTexture(t)

    def send_control(self, key, value):
        if self.commands is None:
            return
        self.pending_commands.pop(key, None)
        self.pending_commands[key] = value
        self.flush_commands()

    def flush_commands(self):
        if self.commands is None:
            return
        for key, value in list(self.pending_commands.items()):
            try:
                self.commands.put_nowait((key, value))
            except queue.Full:
                break
            del self.pending_commands[key]

    def handle_event(self, data):
        kind = struct.unpack_from("<I", data)[0]
        if kind not in (0x400, 0x401, 0x402):
            return
        window = struct.unpack_from("<I", data, 8)[0]
        x, y = struct.unpack_from("<ii", data, 20)
        if kind == 0x402:
            self.drag = None
            return
        if kind == 0x401 and data[16] != 1:
            return
        if kind == 0x400:
            if not self.drag or not struct.unpack_from("<I", data, 16)[0] & 1:
                return
            window, box = self.drag
            self.send_control(
                "contrast_value",
                76 / 256 + max(0, min(1, (x - box[0]) / box[2])) * ((768 - 76) / 256),
            )
            return
        for key, box in self.regions.get(window, []):
            bx, by, bw, bh = box
            if bx <= x < bx + bw and by <= y < by + bh:
                if key == "contrast_value":
                    self.drag = (window, box)
                    self.send_control(
                        key,
                        76 / 256 + max(0, min(1, (x - bx) / bw)) * ((768 - 76) / 256),
                    )
                elif key == "reset":
                    self.send_control(key, True)
                else:
                    value = not self.panel_state.get("auto", {}).get(key, False)
                    self.panel_state.setdefault("auto", {})[key] = value
                    self.send_control(key, value)
                break

    def draw_controls(self, window, r, x, y, width, state):
        if not state:
            return
        self.panel_state = state
        regions = []
        cfg = state["config"]
        auto = state["auto"]

        def fill(box, color):
            self.s.SDL_SetRenderDrawColor(r, *color, 255)
            self.s.SDL_RenderFillRect(r, C.byref(Rect(*box)))

        def button(key, label, bx, by, bw, on=False):
            box = (bx, by, bw, 42)
            fill(box, (0, 105, 112) if on else (38, 57, 78))
            self.text(r, label, bx + 8, by + 3, bw - 12)
            regions.append((key, box))

        half = (width - 12) // 2
        button(
            "wb", "自动白平衡 " + ("开" if auto["wb"] else "关"), x, y, half, auto["wb"]
        )
        button(
            "brightness",
            "自动亮度 " + ("开" if auto["brightness"] else "关"),
            x + half + 12,
            y,
            half,
            auto["brightness"],
        )
        self.text(r, f'对比度 {cfg["contrast"]/256:.2f}x', x, y + 52, width - 140)
        button(
            "contrast",
            "自动 " + ("开" if auto["contrast"] else "关"),
            x + width - 130,
            y + 48,
            130,
            auto["contrast"],
        )
        bx, by, bw = x + 12, y + 104, width - 24
        fill((bx, by + 15, bw, 6), (73, 94, 117))
        pos = bx + round(bw * (cfg["contrast"] - 76) / (768 - 76))
        fill((bx, by + 15, max(1, pos - bx), 6), (0, 235, 210))
        fill((pos - 7, by + 3, 14, 30), (0, 235, 210))
        regions.append(("contrast_value", (bx, by - 3, bw, 42)))
        self.text(r, "0.30x", x, y + 140, 90)
        self.text(r, "1.0x", bx + round(bw * (256 - 76) / (768 - 76)) - 24, y + 140, 90)
        self.text(r, "3.0x", x + width - 65, y + 140, 65)
        button("reset", "恢复默认", x + width - 130, y + 180, 130)
        self.text(
            r,
            f'红 {cfg["rg"]/256:.2f} 蓝 {cfg["bg"]/256:.2f} 亮度 {cfg["brightness"]:+d}',
            x,
            y + 181,
            width - 138,
        )
        self.text(
            r,
            state["error"] or state["message"],
            x,
            y + 228,
            width,
            (255, 170, 100, 255) if state["error"] else (120, 220, 210, 255),
        )
        self.text(
            r, "亮度为 ISP 补偿，非传感器曝光", x, y + 264, width, (160, 175, 190, 255)
        )
        self.regions[self.s.SDL_GetWindowID(window)] = regions

    def show(self, control, row):
        show_started = time.perf_counter()
        event = C.create_string_buffer(64)
        while self.s.SDL_PollEvent(event):
            self.handle_event(event.raw)
        self.flush_commands()
        now = time.monotonic()
        rate = 1 / max(now - self.last, 0.001)
        self.last = now
        self.fps = rate if not self.fps else self.fps * 0.8 + rate * 0.2
        frame = control.last_overview
        candidates = [
            c for result in row["results"] for c in result.get("fine_candidates", [])
        ]
        aligned = [
            c
            for result in row["results"]
            if result.get("roi_frame_id") == frame.source_frame_id
            for c in result.get("fine_candidates", [])
        ]
        cached = [r for r in row["results"] if "cached_fine" in r]
        for window, r, texture, roi, w, h in self.windows:
            self.s.SDL_SetRenderDrawColor(r, 13, 20, 30, 255)
            self.s.SDL_RenderClear(r)
            self.text(
                r,
                f'MobileNet 电阻 005638-box1 | ROI {row.get("roi_rate",0):.1f}次/秒 | 显示 {self.fps:.1f}次/秒',
                20,
                30,
                w - 40,
            )
            if self.main_only:
                vw = min(w, h * 512 // 288)
                vh = vw * 288 // 512
                area = Rect((w - vw) // 2, (h - vh) // 2, vw, vh)
            else:
                vw = w if w < 1100 else w - 480
                vh = vw * 288 // 512
                area = Rect(0, 60, vw, vh)
            self.s.SDL_UpdateTexture(
                texture, None, C.c_void_p(frame.rgb.ctypes.data), 512 * 3
            )
            self.s.SDL_RenderCopy(r, texture, None, C.byref(area))

            def box(coords, sw, sh, color):
                x1, y1, x2, y2 = coords
                rect = Rect(
                    area.x + int(x1 * vw / sw),
                    area.y + int(y1 * vh / sh),
                    max(1, int((x2 - x1) * vw / sw)),
                    max(1, int((y2 - y1) * vh / sh)),
                )

                for first, last, ink in ((0, 9, (0, 0, 0)), (2, 7, color)):
                    self.s.SDL_SetRenderDrawColor(r, *ink, 255)
                    for inset in range(first, last):
                        if rect.w <= inset * 2 or rect.h <= inset * 2:
                            break
                        edge = Rect(
                            rect.x + inset,
                            rect.y + inset,
                            rect.w - inset * 2,
                            rect.h - inset * 2,
                        )
                        self.s.SDL_RenderDrawRect(r, C.byref(edge))

            for target in row["results"]:
                b = target["box"]
                held = target.get("display_state") == "held_prediction"
                color = (
                    (0, 255, 120)
                    if "cached_fine" in target
                    and target["cached_fine"].get("label") != "uncertain"
                    else (255, 210, 0)
                )
                box(b, 1920, 1080, color)
                tx = max(0, min(w - 150, int(b[0] * vw / 1920)))
                ty = max(60, min(h - 40, 60 + int(b[1] * vh / 1080) - 28))
                semantic = target.get("cached_fine") or target.get("held_semantics")
                poses = {
                    "stem_up": "柄朝上",
                    "stem_down": "柄朝下",
                    "opening_up": "开口朝上",
                    "opening_down": "开口朝下",
                    "lying": "平放",
                    "upright": "直立",
                    "side": "侧放",
                    "uncertain": "未确认",
                }
                if semantic:
                    pose = poses.get(semantic.get("pose"), semantic.get("pose"))
                else:
                    count = target.get("pose_pending_count", 0)
                    pose = (
                        "类别冲突，待复核"
                        if target.get("category_conflict")
                        else "等待有效精判"
                    )
                label = f'ID {target.get("track_id", "?")} {pose}' + (
                    " 保持" if held else ""
                )
                tx = max(0, min(w - 380, tx))
                self.text(
                    r,
                    label,
                    tx,
                    ty,
                    min(380, w),
                    (*color, 255),
                    background=(0, 0, 0, 225),
                )
            if self.main_only:
                self.s.SDL_RenderPresent(r)
                continue
            px, py = (20, 90 + vh) if w < 1100 else (vw + 20, 70)
            tw = w - 40 if w < 1100 else 440
            self.text(r, "亮绿：识别有效  亮黄：待确认", px, py, tw)
            self.text(
                r,
                f'已确认 {len(cached)} / 目标 {len(row["results"])}',
                px,
                py + 38,
                tw,
                (255, 205, 110, 255),
            )
            if not candidates and not cached:
                self.text(r, "正在原生像素 ROI 扫描 / 精判", px, py + 80, tw)
            labels = candidates + [x["cached_fine"] for x in cached]
            for i, c in enumerate(labels[: 2 if w < 1100 else 3]):
                age = f' [复用 {c["age_ms"]:.0f}ms]' if c.get("reused") else ""
                self.text(
                    r,
                    f'{i+1}. {c.get("category","unknown")} / {c.get("pose","")}{age}',
                    px,
                    py + 80 + i * 66,
                    min(tw, 380),
                )
                self.text(
                    r,
                    f'置信度 {c.get("score",0):.1%}',
                    px,
                    py + 110 + i * 66,
                    tw,
                    (70, 255, 140, 255),
                )
            crop = getattr(control, "last_roi", None)
            rx, ry = (px + 400, py + 80) if w < 1100 else (px, py + 290)
            if crop is not None:
                self.text(
                    r, f"最近 ROI 源帧 {crop.source_frame_id}", rx, ry, min(tw, 340)
                )
                self.s.SDL_UpdateTexture(
                    roi, None, C.c_void_p(crop.rgb.ctypes.data), 512 * 3
                )
                dest = Rect(rx, ry + 36, min(tw, 320), min(tw, 320) * 288 // 512)
                self.s.SDL_RenderCopy(r, roi, None, C.byref(dest))
            self.draw_controls(
                window,
                r,
                px,
                py + (315 if w < 1100 else 535),
                tw,
                row.get("image_controls"),
            )
            industrial = row.get("industrial")
            if industrial:
                self.text(
                    r,
                    f'活动分析 {industrial.get("status")}  前景 {industrial.get("foreground_pixels","--")} / 129600  独立帧 {industrial.get("analysis_frame","--")}',
                    20,
                    h - 76,
                    w - 40,
                )
            self.text(
                r,
                "1000 区分上下；1001 仅分字面 / 背面 | 原生 ROI 识别，缩略图显示",
                20,
                h - 40,
                w - 40,
            )
            self.s.SDL_RenderPresent(r)

        p = self._perf
        p["frames"] += 1
        p["render_s"] += time.perf_counter() - show_started
        if p["last_overview"] != frame.source_frame_id:
            p["overviews"] += 1
            p["last_overview"] = frame.source_frame_id
        elapsed = time.monotonic() - p["start"]
        if elapsed >= 2:
            from pathlib import Path

            report = dict(
                text_enabled=self.draw_text,
                main_only=self.main_only,
                preview_only=row.get("preview_only", False),
                screens=len(self.windows),
                measured_seconds=elapsed,
                window_redraws_per_second=p["frames"] / elapsed,
                render_ms_mean=p["render_s"] * 1000 / p["frames"],
                fresh_overview_updates_per_second=p["overviews"] / elapsed,
                roi_inferences_per_second=row.get("roi_rate"),
                recorded_monotonic=time.monotonic(),
            )
            temp = Path("screen_performance.tmp")
            temp.write_text(json.dumps(report, indent=2))
            temp.replace("screen_performance.json")
            p.update(start=time.monotonic(), frames=0, render_s=0.0, overviews=0)

    def close(self):
        for window, r, texture, roi, w, h in self.windows:
            self.s.SDL_DestroyTexture(texture)
            self.s.SDL_DestroyTexture(roi)
            self.s.SDL_DestroyRenderer(r)
            self.s.SDL_DestroyWindow(window)
        self.s.SDL_Quit()


if __name__ == "__main__":
    view = ScreenView()
    view.close()
