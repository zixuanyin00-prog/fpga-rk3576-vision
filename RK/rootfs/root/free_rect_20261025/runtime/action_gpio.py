import os
from pathlib import Path
import struct

GPIO_GET_CHIPINFO_IOCTL = 0x8044B401
GPIO_V2_GET_LINEINFO_IOCTL = 0xC100B405
GPIO_V2_GET_LINE_IOCTL = 0xC250B407
GPIO_V2_LINE_SET_VALUES_IOCTL = 0xC010B40F
UART_DEVICE = Path("/sys/bus/platform/devices/2adb0000.serial")
UART_DRIVER = Path("/sys/bus/platform/drivers/dw-apb-uart")


def _ioctl(fd, code, buffer):
    import fcntl

    fcntl.ioctl(fd, code, buffer, True)
    return buffer


def probe():

    if b"rockchip,rk3576" not in Path("/proc/device-tree/compatible").read_bytes():
        raise RuntimeError("TX GPIO output requires RK3576")
    candidates = []
    for path in Path("/dev").glob("gpiochip*"):
        fd = os.open(str(path), os.O_RDONLY | os.O_CLOEXEC)
        try:
            _, label, lines = struct.unpack(
                "=32s32sI", _ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, bytearray(68))
            )
            if label.rstrip(b"\0") != b"gpio0":
                continue
            if lines <= 17:
                raise RuntimeError("gpio0 does not expose offset17")
            line = bytearray(256)
            struct.pack_into("=I", line, 64, 17)
            _ioctl(fd, GPIO_V2_GET_LINEINFO_IOCTL, line)
            name, consumer, _, _, flags = struct.unpack_from("=32s32sIIQ", line)
            candidates.append(
                dict(
                    device=str(path),
                    chip_label="gpio0",
                    line_offset=17,
                    used=bool(flags & 1),
                    consumer=consumer.rstrip(b"\0").decode(errors="replace"),
                    name=name.rstrip(b"\0").decode(errors="replace"),
                )
            )
        finally:
            os.close(fd)
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one gpio0 chip")
    info = candidates[0]
    rows = (
        Path("/sys/kernel/debug/pinctrl/pinctrl-rockchip-pinctrl/pinmux-pins")
        .read_text()
        .splitlines()
    )
    mux = next((row for row in rows if row.startswith("pin 17 (gpio0-17):")), "")
    uart8 = "2adb0000.serial" in mux and "function uart8 group uart8m2-xfer" in mux
    free = "(MUX UNCLAIMED)" in mux and "(GPIO UNCLAIMED)" in mux
    if info["used"] or "(GPIO UNCLAIMED)" not in mux or not (uart8 or free):
        raise RuntimeError("UART8 TX is claimed by an unsupported owner: " + mux)
    return dict(
        info,
        pinmux=mux,
        uart8_mux_owned=uart8,
        net="UART8_TX_M2",
        gpio="GPIO0_C1",
        voltage=3.3,
    )


def _check_uart8_available():

    dt = Path("/proc/device-tree")
    if any(
        row.split()[0] == "ttyS8"
        for row in Path("/proc/consoles").read_text().splitlines()
        if row.split()
    ):
        raise RuntimeError("UART8 is an active console")
    for token in Path("/proc/cmdline").read_text().split():
        if token.split(",", 1)[0] == "console=ttyS8" or (
            token.startswith("earlycon=") and "2adb0000" in token.lower()
        ):
            raise RuntimeError("UART8 is a boot console")
    stdout = dt / "chosen/stdout-path"
    if stdout.exists():
        route = stdout.read_bytes().rstrip(b"\0").decode().split(":", 1)[0]
        if route == "serial8" or route.endswith("/serial@2adb0000"):
            raise RuntimeError("UART8 is the selected boot console")
    for node in dt.rglob("*fiq*"):
        serial_id = node / "rockchip,serial-id"
        status = node / "status"
        if (
            node.is_dir()
            and serial_id.exists()
            and (
                not status.exists()
                or status.read_bytes().rstrip(b"\0") in (b"okay", b"ok")
            )
        ):
            if struct.unpack(">I", serial_id.read_bytes())[0] == 8:
                raise RuntimeError("UART8 is used by the FIQ debugger")
    driver = UART_DEVICE / "driver"
    if not driver.exists():
        return
    if driver.resolve() != UART_DRIVER.resolve():
        raise RuntimeError("UART8 has an unexpected platform driver")
    tty = Path("/sys/class/tty/ttyS8/device")
    if (
        not tty.exists()
        or tty.resolve() != UART_DEVICE.resolve()
        or (UART_DEVICE / "of_node").resolve().name != "serial@2adb0000"
    ):
        raise RuntimeError("ttyS8 does not map to the verified UART8 controller")
    users = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except FileNotFoundError:
                    continue
                if target == "/dev/ttyS8":
                    users.append(int(process.name))
        except FileNotFoundError:
            pass
    if users:
        raise RuntimeError("UART8 is in use by processes: " + str(sorted(set(users))))


class GPIOV2Line:
    def __init__(self, active_high):
        if active_high is not False:
            raise ValueError(
                "TX control requires active_high=false: idle HIGH, pulse LOW"
            )
        self.active_high = False
        self.fd = None
        self.active = False
        self.uart_released = False
        self.info = probe()
        _check_uart8_available()
        ready = False
        if (UART_DEVICE / "driver").exists() and not self.info["uart8_mux_owned"]:
            raise RuntimeError("Bound UART8 does not own the verified TX_M2 pin")
        try:
            if self.info["uart8_mux_owned"]:
                if not (UART_DEVICE / "driver").exists():
                    raise RuntimeError("UART8 mux owner changed before release")
                (UART_DRIVER / "unbind").write_text(UART_DEVICE.name)
                self.uart_released = True
                self.info = probe()
                if self.info["uart8_mux_owned"]:
                    raise RuntimeError("UART8 did not release its TX pin")
            chip = os.open(self.info["device"], os.O_RDONLY | os.O_CLOEXEC)
            try:
                request = bytearray(592)
                struct.pack_into("=I", request, 0, 17)
                consumer = b"vision-pose-action"
                request[256 : 256 + len(consumer)] = consumer
                struct.pack_into("=QI", request, 288, 1 << 3, 1)

                struct.pack_into("=IIQQ", request, 320, 2, 0, 1, 1)
                struct.pack_into("=I", request, 560, 1)
                _ioctl(chip, GPIO_V2_GET_LINE_IOCTL, request)
                self.fd = struct.unpack_from("=i", request, 588)[0]
                if self.fd < 0:
                    raise RuntimeError(
                        "Kernel returned an invalid GPIO request descriptor"
                    )
                os.set_inheritable(self.fd, False)
                ready = True
            finally:
                os.close(chip)
        finally:
            if not ready:
                try:
                    if self.fd is not None and self.fd >= 0:
                        os.close(self.fd)
                    self.fd = None
                finally:
                    self._restore_uart8()

    def _restore_uart8(self):
        if self.uart_released:
            driver = UART_DEVICE / "driver"
            if not driver.exists():
                (UART_DRIVER / "bind").write_text(UART_DEVICE.name)
            elif driver.resolve() != UART_DRIVER.resolve():
                raise RuntimeError("UART8 was rebound to another driver")
            self.uart_released = False

    def set(self, active):
        if self.fd is None:
            raise RuntimeError("TX GPIO request is closed")
        physical_high = not bool(active)
        _ioctl(
            self.fd,
            GPIO_V2_LINE_SET_VALUES_IOCTL,
            bytearray(struct.pack("=QQ", int(physical_high), 1)),
        )
        self.active = bool(active)

    def snapshot(self):
        return dict(
            configured=self.fd is not None,
            chip_label="gpio0",
            line_offset=17,
            gpio="GPIO0_C1",
            net="UART8_TX_M2",
            voltage=3.3,
            active=self.active,
            active_high=False,
            physical_high=not self.active,
            uart_controller="2adb0000.serial",
            uart_controller_released=self.uart_released,
        )

    def close(self):
        try:
            if self.fd is not None:
                try:
                    self.set(False)
                finally:
                    os.close(self.fd)
                    self.fd = None
        finally:
            self._restore_uart8()
