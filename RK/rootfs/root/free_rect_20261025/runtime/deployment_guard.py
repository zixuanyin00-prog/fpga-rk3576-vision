from pathlib import Path
import hashlib
import json
import mmap
import struct


def validate_identity(identity, boot, registers):
    if (
        not identity.get("deployment_verified")
        or identity.get("verified_boot_id") != boot
    ):
        raise RuntimeError(
            "SRAM deployment is not verified for this boot; reload the matched FPGA image before starting preview."
        )
    expected = {
        0: 0x50434531,
        4: 0x001C0000,
        0x1E8: 0x31544352,
        0x1EC: 3,
        0x1F4: 0x10000,
        0x1F8: 0,
        0x1FC: 0,
        0x300: 0x32435250,
        0x304: 1,
        0x308: 0xFFF,
        0x30C: 10,
        0x208: 0x314D4753,
        0x20C: 0x01001014,
        0x240: 0x33525453,
        0x248: 518400,
    }
    if any(registers.get(offset) != value for offset, value in expected.items()):
        raise RuntimeError("FPGA rectangle ABI differs from the installed RK runtime.")
    if identity.get("native_ddr_required") and registers.get(0x1F0, 0) >> 24 != 0xDD:
        raise RuntimeError(
            "The installed native capture runtime requires the DDR FPGA image."
        )


def validate_deployment(root):
    root = Path(root).resolve()
    identity = json.loads((root / "camera_environment_identity.json").read_text())
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    if identity.get("verified_boot_id") != boot:
        validate_identity(identity, boot, {})
    devices = [
        p
        for p in Path("/sys/bus/pci/devices").iterdir()
        if (p / "vendor").read_text().strip() == "0x1edb"
        and (p / "device").read_text().strip() == "0xdcba"
    ]
    if len(devices) != 1:
        raise RuntimeError("Exactly one FPGA PCIe endpoint is required.")
    with (devices[0] / "resource0").open("rb", buffering=0) as f:
        with mmap.mmap(
            f.fileno(), 4096, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ
        ) as bar:
            registers = {
                offset: struct.unpack_from("<I", bar, offset)[0]
                for offset in (
                    0,
                    4,
                    0x1E8,
                    0x1EC,
                    0x1F0,
                    0x1F4,
                    0x300,
                    0x304,
                    0x308,
                    0x30C,
                    0x1F8,
                    0x1FC,
                    0x208,
                    0x20C,
                    0x240,
                    0x248,
                )
            }
    validate_identity(identity, boot, registers)
    driver = root.parent / "artifacts/pcie_camera_vision.ko"
    if hashlib.sha256(driver.read_bytes()).hexdigest() != identity["driver_sha256"]:
        raise RuntimeError("Installed driver changed after deployment verification.")
    if not Path("/sys/module/pcie_camera_vision").is_dir():
        raise RuntimeError("The matched FPGA driver is not loaded.")
