from resistor_image_panel import ImagePanel as ExistingPanel


class ImagePanel(ExistingPanel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.auto = dict.fromkeys(self.auto, False)

    def update(self, camera, control, frame=None):
        self.auto = dict.fromkeys(self.auto, False)
        super().update(camera, control, None)
        return self.status()

    def status(self):
        value = super().status()
        value.update(
            cpu_statistics_enabled=False,
            statistics_source="FPGA AutoISP metadata; legacy CPU statistics disabled",
        )
        return value
