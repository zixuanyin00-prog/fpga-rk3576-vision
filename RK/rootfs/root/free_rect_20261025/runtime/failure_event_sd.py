from pathlib import Path
import os


def inspect_sd(config):
    point = config.get("mount_point", "/media/vision_sd")
    result = dict(
        enabled=bool(config.get("enabled", False)),
        available=False,
        mount_point=point,
        receive_history=bool(config.get("receive_history", False)),
        reason="disabled",
    )
    if not result["enabled"]:
        return result
    mount = Path(point)
    if not mount.is_absolute() or not os.path.ismount(mount):
        result.update(reason="not_present", detail="exact_mount_point_not_mounted")
        return result
    rows = Path("/proc/self/mountinfo").read_text().splitlines()
    row = next(
        (
            line.split()
            for line in rows
            if line.split()[4].replace("\\040", " ") == str(mount)
        ),
        None,
    )
    if row is None:
        result["reason"] = "mount_identity_unavailable"
        return result
    device = (Path("/sys/dev/block") / row[2]).resolve()
    if not device.exists():
        result["reason"] = "not_a_block_device"
        return result
    is_sd = any(
        (p / "device/type").is_file()
        and (p / "device/type").read_text().strip() == "SD"
        for p in [device, *device.parents]
    )
    removable = any(
        (p / "removable").is_file() and (p / "removable").read_text().strip() == "1"
        for p in [device, *device.parents]
    )
    result.update(
        available=is_sd or removable,
        reason=None if is_sd or removable else "not_sd_or_removable",
        block_device=row[2],
        device_path=str(device),
        is_sd=is_sd,
        removable=removable,
    )
    return result
