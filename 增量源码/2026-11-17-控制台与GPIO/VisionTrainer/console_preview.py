import json
from pathlib import Path
import shlex
import time

from console_store import sha, uid, write
from console_model_deploy import SERVICE, fixed_imaging_from_camera, read_fixed_release


def enter_preview(board, identity, bundle, job):
    fixed = read_fixed_release(board)
    if fixed is None:
        raise ValueError(
            "当前服务不是已支持的固定启动入口；新流程已保留，尚未确认停止旧推理，请先恢复固定纯预览服务"
        )
    bundle = Path(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "release_before.json").write_bytes(fixed["raw"])
    job.update(
        stage="disarm", message="关闭上一流程电磁阀，随后进入不加载模型的持久纯预览"
    )
    from console_action import disable

    try:
        output = disable(board, job)
        state = None
        if board.pid > 0 and fixed["manifest"]["launch_mode"] == "preview":
            current = board.fresh("runtime_status.json")
            if (
                current["pid"] == board.pid
                and current.get("inference_enabled") is False
                and current.get("selected_model") is None
            ):
                state = board.status()
        if state is None:
            manifest = dict(fixed["manifest"], launch_mode="preview")
            if board.pid > 0:
                manifest["imaging"] = fixed_imaging_from_camera(
                    board.fresh("camera_console/status.json")
                )
            raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            unit_sha = sha(board.command("systemctl cat " + SERVICE))
            guard = """import hashlib,subprocess
from pathlib import Path
expected=EXPECTED
pid=int(subprocess.check_output(['systemctl','show','fpga-vision-v3.service','-p','MainPID','--value']))
if pid!=expected['pid']:raise ValueError('Vision process changed; preview transition did not stop it')
if hashlib.sha256(Path(expected['path']).read_bytes()).hexdigest()!=expected['sha']:raise ValueError('Fixed release changed; preview transition did not stop it')
if hashlib.sha256(subprocess.check_output(['systemctl','cat','fpga-vision-v3.service'])).hexdigest()!=expected['unit_sha']:raise ValueError('Service entry changed; preview transition did not stop it')
""".replace(
                "EXPECTED",
                repr(
                    dict(
                        pid=board.pid,
                        path=fixed["path"],
                        sha=sha(fixed["raw"]),
                        unit_sha=unit_sha,
                    )
                ),
            )
            job.check()
            job.protected = True
            job.update(
                stage="preview", message="保留已有权重与数据，使用原固定入口恢复纯预览"
            )
            board.command(
                "/usr/bin/python3 -I -c "
                + shlex.quote(guard)
                + " && systemctl stop "
                + SERVICE,
                timeout=25,
            )
            temporary = fixed["path"] + ".preview-" + uid() + ".tmp"
            with board.s.open(temporary, "wb") as stream:
                stream.write(raw)
            board.s.posix_rename(temporary, fixed["path"])
            if board.bytes(fixed["path"]) != raw:
                raise ValueError("持久纯预览清单回读不一致；未启用任何模型")
            (bundle / "release_preview.json").write_bytes(raw)
            board.command(
                "systemctl reset-failed " + SERVICE + " && systemctl start " + SERVICE,
                timeout=30,
            )
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                pid = int(
                    board.command(
                        "systemctl show " + SERVICE + " -p MainPID --value"
                    ).strip()
                )
                if pid > 0:
                    try:
                        current = board.fresh("runtime_status.json")
                        if current.get("pid") == pid:
                            if (
                                current.get("inference_enabled") is not False
                                or current.get("selected_model") is not None
                            ):
                                raise RuntimeError(
                                    "固定入口仍报告推理或旧模型；新流程尚未就绪"
                                )
                            board.pid = pid
                            state = board.status()
                            break
                    except (OSError, ValueError):
                        pass
                time.sleep(0.5)
            else:
                raise TimeoutError(
                    "持久纯预览尚未就绪；新流程数据已保留，请恢复预览后重试，不会回退加载历史模型"
                )
        action = state.get("action_output", {})
        if action.get("installed") and (
            not action.get("fresh") or action["config"].get("enabled") is not False
        ):
            raise ValueError("GPIO关闭状态尚未回读；新流程尚未确认就绪")
        result = dict(
            state="ready",
            mode="preview",
            flow=identity,
            pid=board.pid,
            boot_id=board.boot,
            fixed_release=fixed["path"],
            persistent=True,
            model_loaded=False,
            old_models_and_data_preserved=True,
            output=output,
            board=state,
        )
        write(bundle / "result.json", result)
        return result
    finally:
        job.protected = False
