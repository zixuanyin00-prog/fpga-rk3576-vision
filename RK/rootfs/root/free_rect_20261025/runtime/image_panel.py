from dataclasses import asdict, replace
from pathlib import Path
import json
import math
import time
from adaptive_isp import ISPConfig


class ImagePanel:
    def __init__(self, path="image_controls.json", clock=time.monotonic):
        self.path = Path(path)
        self.clock = clock
        self.config = ISPConfig()
        self.auto = dict(wb=False, brightness=False, contrast=False)
        self.message = "拖动对比度滑条切换为手动"
        self.error = ""
        self.last_stats = None
        self.last_sample = float("-inf")
        self.last_frame = None
        self.last_change = float("-inf")
        self.dirty = True
        self.generation = 0
        self.applied = None
        try:
            saved = json.loads(self.path.read_text(encoding="utf8"))
            self.config = ISPConfig(**saved["config"])
            self.auto.update({k: bool(saved["auto"][k]) for k in self.auto})
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError) as exc:
            self.message = "已忽略无效的参数文件"

    def command(self, command):
        key, value = command
        if key in self.auto:
            self.auto[key] = bool(value)
            self.last_sample = float("-inf")
        elif key == "contrast_value":
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                return
            self.config = replace(
                self.config, contrast=max(76, min(768, round(value * 256)))
            )
            self.auto["contrast"] = False
            self.dirty = True
        elif key == "reset":
            self.config = ISPConfig(revision=self.config.revision)
            self.auto = dict.fromkeys(self.auto, False)
            self.dirty = True
        else:
            return
        self.error = ""
        if not self.dirty:
            self.save()

    def save(self):
        try:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(dict(config=asdict(self.config), auto=self.auto), indent=2),
                encoding="utf8",
            )
            temp.replace(self.path)
        except OSError as exc:
            self.error = "参数保存失败: " + str(exc)

    @staticmethod
    def statistics(rgb):

        import cv2
        import numpy as np

        small = cv2.resize(rgb, (160, 90))
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        low, high = np.percentile(gray, [10, 90])
        means = small.mean((0, 1))
        return dict(
            mean_luma=float(gray.mean()),
            p10=float(low),
            p90=float(high),
            red=float(means[0]),
            green=float(means[1]),
            blue=float(means[2]),
        )

    def update(self, camera, control, frame=None):
        now = self.clock()
        if (
            frame is not None
            and any(self.auto.values())
            and now - self.last_sample >= 0.75
            and now - self.last_change >= 0.5
            and frame.source_frame_id != self.last_frame
        ):
            self.last_sample, self.last_frame = now, frame.source_frame_id
            stat = self.last_stats = self.statistics(frame.rgb)
            cfg = self.config
            changes = {}

            def bounded(current, target, step, lo, hi):
                return round(
                    max(lo, min(hi, current + max(-step, min(step, target - current))))
                )

            if self.auto["wb"] and 24 < stat["mean_luma"] < 230:
                for key, channel in (("rg", "red"), ("bg", "blue")):
                    ratio = stat["green"] / max(stat[channel], 1)

                    if abs(ratio - 1) > 0.015:
                        value = getattr(cfg, key)
                        changes[key] = bounded(value, value * ratio, 8, 128, 512)
            if self.auto["brightness"]:
                correction = max(-25, min(25, 110 - stat["mean_luma"]))
                if abs(correction) > 3:
                    changes["brightness"] = bounded(
                        cfg.brightness, cfg.brightness + correction, 2, -25, 25
                    )
            if self.auto["contrast"]:
                ratio = max(0.85, min(1.2, 125 / max(stat["p90"] - stat["p10"], 30)))
                if abs(ratio - 1) > 0.03:
                    changes["contrast"] = bounded(
                        cfg.contrast, cfg.contrast * ratio, 4, 218, 307
                    )
            candidate = replace(cfg, **changes)
            if candidate != cfg:
                self.config = candidate
                self.dirty = True
        if self.dirty:
            try:

                control.collect(wait=True)
                requested = replace(
                    self.config, revision=(self.config.revision + 1) & 65535
                )
                camera.set_isp(requested)
            except (OSError, RuntimeError) as exc:
                self.error = "图像调节失败: " + str(exc)
                self.auto = dict.fromkeys(self.auto, False)
                self.dirty = False
                if self.applied is not None:
                    self.config = self.applied
                return self.status()
            self.config = requested
            self.applied = requested
            self.error = ""
            self.dirty = False
            self.last_change = now
            self.generation += 1

            for track in control.scheduler.tracks.values():
                track.update(
                    cache=None,
                    fine_time=None,
                    pose_votes=[],
                    pose_pending=None,
                    pose_pending_count=0,
                    category_conflict=False,
                    trusted_coarse_category=None,
                    trusted_coarse_time=float("-inf"),
                    pose_confirmation="image_parameters_changed",
                )
                for key in ("coarse_class_hits", "coarse_class_candidate"):
                    track.pop(key, None)
            self.message = "参数已发送 FPGA，精判已刷新"
            self.save()
        return self.status()

    def status(self):
        return dict(
            config=asdict(self.config),
            auto=dict(self.auto),
            message=self.message,
            applied=None if self.applied is None else asdict(self.applied),
            error=self.error,
            generation=self.generation,
            statistics=self.last_stats,
            cpu_statistics_enabled=any(self.auto.values()),
            cpu_pixel_adjustment=False,
            sensor_exposure_control=False,
            effective_source_frame_unknown=True,
        )
