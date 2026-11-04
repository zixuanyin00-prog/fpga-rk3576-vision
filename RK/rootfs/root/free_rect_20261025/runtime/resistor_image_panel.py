from dataclasses import replace
import math
from image_panel import ImagePanel as ExistingImagePanel


class ImagePanel(ExistingImagePanel):
    def command(self, command):
        key, value = command
        fields = {
            "rg_value": ("rg", 128, 512, 256, "wb"),
            "bg_value": ("bg", 128, 512, 256, "wb"),
            "brightness_value": ("brightness", -100, 100, 1, "brightness"),
            "gaussian_value": ("gaussian", 0, 256, 256, None),
        }
        if key not in fields:
            return super().command(command)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        field, lo, hi, scale, auto = fields[key]
        self.config = replace(
            self.config, **{field: max(lo, min(hi, round(value * scale)))}
        )
        if auto:
            self.auto[auto] = False
        self.dirty = True
        self.error = ""
