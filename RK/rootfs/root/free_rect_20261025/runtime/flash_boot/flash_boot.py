from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import mmap
import os
import re
import struct
import subprocess
import time
import uuid


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    raw = (
        value
        if isinstance(value, bytes)
        else (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    )
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_release(manifest):

    if manifest["schema"] != "rk_flash_release_v1" or manifest["launch_mode"] not in (
        "auto_selected",
        "preview",
    ):
        raise ValueError("Unknown fixed-release schema or launch mode")
    if manifest["launch_mode"] == "auto_selected":
        selected = manifest["selected_model"]
        if (
            not selected["run"]
            or not selected["batch"]
            or not selected["training_started_beijing"]
            or not selected["training_completed_beijing"]
            or selected.get("confirmed") is not True
            or not manifest["authorization"]["user_answer"]
        ):
            raise ValueError(
                "Fixed inference requires an explicitly selected training record and authorization"
            )
        for path_key, hash_key in [
            ("fine_model_path", "fine_sha256"),
            ("fine_config_path", "config_sha256"),
        ]:
            if digest(selected[path_key]) != selected[hash_key]:
                raise ValueError("Selected model artifact hash mismatch: " + path_key)
    for path, expected in manifest["runtime_files"].items():
        if digest(path) != expected:
            raise ValueError("Fixed runtime hash mismatch: " + path)
    if digest(manifest["driver"]["path"]) != manifest["driver"]["sha256"]:
        raise ValueError("Fixed driver hash mismatch")
    flash = manifest["flash"]
    if (
        not flash["verification_sha256"]
        or digest(flash["verification_file"]) != flash["verification_sha256"]
    ):
        raise ValueError("This release has no matching sealed Flash verification")
    proof = json.loads(Path(flash["verification_file"]).read_text(encoding="utf-8"))
    if (
        proof["schema"] != "fpga_flash_verified_release_v1"
        or proof["programmed"] is not True
        or proof["readback_verified"] is not True
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", proof[key])
            for key in ("payload_sha256", "readback_sha256", "firmware_sha256")
        )
        or proof["payload_sha256"] != proof["readback_sha256"]
        or proof["firmware_sha256"] != manifest["source_identity"]["firmware_sha256"]
    ):
        raise ValueError("Flash programming/readback proof does not match this release")
    imaging = manifest["imaging"]
    words = imaging["process_words"]
    enhance = imaging["enhancement"]
    if (
        len(words) != 10
        or any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in words)
        or len(enhance["knots"]) != 9
        or enhance["knots"] != sorted(enhance["knots"])
        or any(type(v) is not int or not 0 <= v <= 256 for v in enhance["knots"])
        or imaging["frozen"] is not True
        or imaging["saved_auto"]["frozen"] is not True
        or imaging["saved_isp"].get("wb_auto") is not False
    ):
        raise ValueError(
            "Invalid fixed processing bundle or automatic controls are not frozen"
        )
    return proof


def prepare_startup(manifest, boot, now, root, temporary):

    imaging = manifest["imaging"]
    for name, values in [
        ("sensor", imaging["sensor"]),
        ("process", dict(words=imaging["process_words"])),
        ("enhancement", imaging["enhancement"]),
    ]:
        atomic(root / ("startup_" + name + ".json"), dict(values, boot_id=boot))
    atomic(root / "camera_console/saved_isp.json", imaging["saved_isp"])
    atomic(root / "camera_console/saved_auto.json", imaging["saved_auto"])
    environment = dict(os.environ, **manifest["environment"])
    environment.pop("VISION_MODEL_SELECTION_NONCE", None)
    environment.pop("VISION_MODEL_SELECTION_TICKET", None)
    args = [manifest["python"], "-u", str(root / "run_live.py"), "--monitors", "HDMI-1"]
    counter_mode = "preview_no_model"
    if manifest["launch_mode"] == "auto_selected":
        selected = dict(manifest["selected_model"])
        for key in (
            "nonce",
            "boot_id",
            "expires_monotonic",
            "counter_restore_path",
            "counter_restore_sha256",
        ):
            selected.pop(key, None)
        nonce = uuid.uuid4().hex
        temporary.mkdir(parents=True, exist_ok=True)
        ticket = dict(
            selected,
            confirmed=True,
            nonce=nonce,
            boot_id=boot,
            expires_monotonic=now + 180,
            fixed_release_id=manifest["release_id"],
            authorization=manifest["authorization"],
        )
        counter = root / "counter_state.json"
        counter_mode = "new_boot_new_session"
        if counter.exists():
            raw = counter.read_bytes()
            record = json.loads(raw)
            if record["boot_id"] == boot:
                rectangle = json.loads(
                    (root / "demo_config.json").read_text(encoding="utf-8")
                )["rectangle"]
                if record["run"] != selected["run"] or record["rectangle"] != rectangle:
                    raise ValueError(
                        "Same-boot count history belongs to another model or ROI"
                    )
                restored = temporary / ("counter_" + nonce + ".json")
                atomic(restored, raw)
                ticket.update(
                    counter_restore_path=str(restored),
                    counter_restore_sha256=hashlib.sha256(raw).hexdigest(),
                )
                counter_mode = "same_boot_exact_sha_restore"
            else:
                atomic(root / "flash_boot/previous_boot_counter.json", raw)
        ticket_path = temporary / ("selection_" + nonce + ".json")
        atomic(ticket_path, ticket)
        environment.update(
            VISION_MODEL_SELECTION_NONCE=nonce,
            VISION_MODEL_SELECTION_TICKET=str(ticket_path),
        )
        args.append("--inference")
    return args, environment, counter_mode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    proof = validate_release(manifest)
    root = Path(manifest["runtime_root"]).resolve()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()

    spec = importlib.util.spec_from_file_location(
        "verified_release_guard", root / "deployment_guard.py"
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    pci = Path("/sys/bus/pci/devices")

    def endpoints():
        return [
            vendor.parent
            for vendor in pci.glob("*/vendor")
            if vendor.read_text(encoding="utf-8").strip() == "0x1edb"
            and (vendor.parent / "device").read_text(encoding="utf-8").strip()
            == "0xdcba"
        ]

    devices = endpoints()
    if not devices:
        host = Path("/sys/bus/platform/drivers/rk-pcie")
        controller = "2a200000.pcie"
        if (host / controller).exists():
            (host / "unbind").write_text(controller)
        (host / "bind").write_text(controller)
        deadline = time.monotonic() + 20
        while not devices and time.monotonic() < deadline:
            time.sleep(0.2)
            devices = endpoints()
    if len(devices) != 1:
        raise RuntimeError(
            "Exactly one matching FPGA endpoint is required for Flash boot"
        )
    offsets = (
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
    with (devices[0] / "resource0").open("rb", buffering=0) as stream:
        with mmap.mmap(
            stream.fileno(), 4096, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ
        ) as bar:
            registers = {
                offset: struct.unpack_from("<I", bar, offset)[0] for offset in offsets
            }
    identity = dict(
        manifest["source_identity"],
        verified_boot_id=boot,
        verification_method="verified_flash_release_and_live_abi",
        verification_origin=manifest["source_identity"],
        verified_flash_release=dict(
            release_id=manifest["release_id"],
            manifest_sha256=digest(manifest_path),
            verification_sha256=manifest["flash"]["verification_sha256"],
            payload_sha256=proof["payload_sha256"],
            readback_sha256=proof["readback_sha256"],
        ),
        boot_registers={hex(offset): value for offset, value in registers.items()},
    )
    guard.validate_identity(identity, boot, registers)
    if Path("/sys/module/pcie_camera_vision").is_dir():
        subprocess.run(["/sbin/rmmod", "pcie_camera_vision"], check=True)
    subprocess.run(["/sbin/insmod", manifest["driver"]["path"]], check=True)
    atomic(root / "camera_environment_identity.json", identity)
    guard.validate_deployment(root)
    command, environment, counter_mode = prepare_startup(
        manifest, boot, time.monotonic(), root, Path("/run/vision-fixed-release")
    )
    atomic(
        root / "flash_boot/last_start.json",
        dict(
            release_id=manifest["release_id"],
            boot_id=boot,
            recorded_monotonic=time.monotonic(),
            launch_mode=manifest["launch_mode"],
            counter_mode=counter_mode,
            selected_run=(
                manifest["selected_model"]["run"]
                if manifest["launch_mode"] == "auto_selected"
                else None
            ),
            verification_method=identity["verification_method"],
            physical_scanout_measured=False,
        ),
    )
    os.chdir(root)
    os.execve(command[0], command, environment)


if __name__ == "__main__":
    main()
