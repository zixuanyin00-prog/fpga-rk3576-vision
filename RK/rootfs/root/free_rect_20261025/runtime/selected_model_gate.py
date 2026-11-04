from pathlib import Path
import hashlib
import json
import os
import time

ROOT = Path("/root/free_rect_20261025/runtime")


class ModelSelectionRequired(ValueError):
    pass


def require_model_selection():
    try:
        ticket = json.loads(
            Path(os.environ["VISION_MODEL_SELECTION_TICKET"]).read_text()
        )
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if (
            ticket["boot_id"] != boot
            or ticket["nonce"] != os.environ["VISION_MODEL_SELECTION_NONCE"]
            or time.monotonic() > ticket["expires_monotonic"]
        ):
            raise ValueError("stale choice")
        if ticket.get("confirmed") is not True or not ticket.get(
            "training_completed_beijing"
        ):
            raise ValueError("explicit time selection required")
        for name, key in [
            ("detector_fp16.rknn", "fine_sha256"),
            ("config.json", "config_sha256"),
        ]:
            if (
                hashlib.sha256(
                    (
                        Path(ticket["fine_model_path"])
                        if key == "fine_sha256"
                        else Path(ticket["fine_config_path"])
                    ).read_bytes()
                ).hexdigest()
                != ticket[key]
            ):
                raise ValueError("artifact hash mismatch")
        config = json.loads(Path(ticket["fine_config_path"]).read_text())
        claim = Path(os.environ["VISION_MODEL_SELECTION_TICKET"] + ".claimed")
        with claim.open("x") as f:
            json.dump(dict(pid=os.getpid(), nonce=ticket["nonce"]), f)
        return ticket
    except (OSError, ValueError, KeyError) as exc:
        raise ModelSelectionRequired(
            "请按训练北京时间重新选择本次精推理模型：" + str(exc)
        ) from exc
