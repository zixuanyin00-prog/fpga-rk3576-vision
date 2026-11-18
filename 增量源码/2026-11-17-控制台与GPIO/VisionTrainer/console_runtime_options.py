import json
from pathlib import Path
import shlex
import time

from console_model_deploy import SERVICE, fixed_imaging_from_camera, read_fixed_release
from console_store import now, sha, uid, write

SELECTION_FIELDS = (
    "run",
    "batch",
    "training_started_beijing",
    "training_completed_beijing",
    "fine_sha256",
    "config_sha256",
)


def state(board, runtime):
    path = board.remote + "/runtime_options.json"
    saved = (
        json.loads(board.bytes(path))
        if board.exists(path)
        else dict(schema_version=1, show_roi=True)
    )
    if saved.get("schema_version") != 1 or type(saved.get("show_roi")) is not bool:
        raise ValueError("全局显示配置格式无效")
    path = board.remote + "/flash_boot/release.json"
    manifest = json.loads(board.bytes(path)) if board.exists(path) else None
    if manifest is not None and (
        manifest.get("schema") != "rk_flash_release_v1"
        or manifest.get("runtime_root") != board.remote
    ):
        raise ValueError("固定启动清单身份无效")
    selected = manifest.get("selected_model") if manifest else None
    available = (
        selected
        if selected and all(selected.get(key) for key in SELECTION_FIELDS)
        else None
    )
    return dict(
        inference_enabled=runtime["inference_enabled"],
        show_roi=runtime.get("runtime_options", {}).get("show_roi"),
        requested_show_roi=saved["show_roi"],
        available_model=available,
        available_model_confirm_fields=list(SELECTION_FIELDS),
        launch_mode=manifest["launch_mode"] if manifest else None,
    )


def apply(board, values, confirmed, selection, bundle, job):
    if (
        not isinstance(values, dict)
        or set(values) != {"inference_enabled", "show_roi"}
        or any(type(value) is not bool for value in values.values())
    ):
        raise ValueError("全局选项必须包含是否开启推理、是否显示ROI框两个开关")
    bundle = Path(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    runtime = (
        board.fresh("runtime_status.json")
        if board.pid > 0
        else dict(inference_enabled=False)
    )
    if board.pid > 0 and runtime.get("pid") != board.pid:
        raise ValueError("视觉进程状态尚未同步；请读取板端后重试")
    enabled = runtime["inference_enabled"]
    fixed = read_fixed_release(board)
    if fixed is None:
        raise ValueError("当前服务不是支持全局推理开关的固定启动入口；未修改运行状态")
    desired = values["inference_enabled"]
    if desired and not enabled:
        selected = fixed["manifest"].get("selected_model")
        if (
            confirmed is not True
            or not selected
            or not isinstance(selection, dict)
            or any(
                not selected.get(key) or selection.get(key) != selected[key]
                for key in SELECTION_FIELDS
            )
        ):
            raise ValueError(
                "开启推理前请明确确认本次实际训练日期、场景批次和模型哈希；未加载模型"
            )
        if board.pid <= 0:
            raise ValueError("请先关闭推理，恢复已有纯预览后，再明确确认开启模型")
        from console_action import disable

        try:
            disable(board, job)
            imaging = fixed_imaging_from_camera(
                board.fresh("camera_console/status.json")
            )
            manifest = dict(
                fixed["manifest"],
                launch_mode="auto_selected",
                imaging=imaging,
                authorization=dict(
                    user_answer="Web全局推理开关本次明确选择："
                    + selected["training_started_beijing"]
                    + " / "
                    + selected["run"],
                    power_on_answer="本次开启确认包含断电自启动所选版本",
                    confirmed_utc=now(),
                    scope="explicit_selected_model_global_inference",
                ),
            )
            raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            (bundle / "release_before.json").write_bytes(fixed["raw"])
            expected = dict(
                pid=board.pid,
                path=fixed["path"],
                sha=sha(fixed["raw"]),
                unit_sha=sha(board.command("systemctl cat " + SERVICE)),
                artifacts={
                    selected[path]: selected[digest]
                    for path, digest in (
                        ("fine_model_path", "fine_sha256"),
                        ("fine_config_path", "config_sha256"),
                    )
                },
            )
            guard = """import hashlib,subprocess
from pathlib import Path
expected=EXPECTED
pid=int(subprocess.check_output(['systemctl','show','fpga-vision-v3.service','-p','MainPID','--value']))
if pid!=expected['pid']:raise ValueError('Vision process changed; inference was not enabled')
if hashlib.sha256(Path(expected['path']).read_bytes()).hexdigest()!=expected['sha']:raise ValueError('Fixed release changed; inference was not enabled')
if hashlib.sha256(subprocess.check_output(['systemctl','cat','fpga-vision-v3.service'])).hexdigest()!=expected['unit_sha']:raise ValueError('Service entry changed; inference was not enabled')
for path,digest in expected['artifacts'].items():
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:raise ValueError('Selected model hash mismatch; inference was not enabled')
""".replace("EXPECTED", repr(expected))
            job.check()
            job.protected = True
            job.update(
                stage="inference",
                message="使用原固定入口开启本次明确确认的模型，保留当前成像和GPIO关闭状态",
            )
            board.command(
                "/usr/bin/python3 -I -c "
                + shlex.quote(guard)
                + " && systemctl stop "
                + SERVICE,
                timeout=25,
            )
            archive = """import hashlib,json,os
from pathlib import Path
expected=EXPECTED
root=Path(expected['root']);counter=root/'counter_state.json';result={'archived':False}
if counter.exists():
    raw=counter.read_bytes();record=json.loads(raw)
    rectangle=json.loads((root/'demo_config.json').read_text())['rectangle']
    if record['boot_id']==expected['boot'] and (record['run']!=expected['run'] or record['rectangle']!=rectangle):
        target=Path(expected['target']);target.parent.mkdir(parents=True,exist_ok=True);os.replace(counter,target)
        result={'archived':True,'path':str(target),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'run':record['run'],'rectangle':record['rectangle']}
print(json.dumps(result))
""".replace(
                "EXPECTED",
                repr(
                    dict(
                        root=board.remote,
                        boot=board.boot,
                        run=selected["run"],
                        target=board.remote
                        + "/runtime_option_transitions/"
                        + job.id
                        + "/counter_before_enable.json",
                    )
                ),
            )
            counter = json.loads(
                board.command("/usr/bin/python3 -I -c " + shlex.quote(archive))
            )
            write(bundle / "counter_archive.json", counter)
            if counter["archived"]:
                original = board.bytes(counter["path"])
                if sha(original) != counter["sha256"]:
                    raise ValueError("原计数归档回读不一致；历史记录仍保留")
                (bundle / "counter_before_enable.json").write_bytes(original)
            temporary = fixed["path"] + ".options-" + job.id + ".tmp"
            with board.s.open(temporary, "wb") as stream:
                stream.write(raw)
            board.s.posix_rename(temporary, fixed["path"])
            if board.bytes(fixed["path"]) != raw:
                raise ValueError("推理启动清单回读不一致；未启用其他模型")
            (bundle / "release_selected.json").write_bytes(raw)
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
                if pid > 0 and pid != board.pid:
                    try:
                        current = board.fresh("runtime_status.json")
                    except (OSError, ValueError):
                        current = None
                    if (
                        current
                        and current.get("pid") == pid
                        and current.get("inference_enabled") is True
                    ):
                        actual = current.get("selected_model") or {}
                        if any(
                            actual.get(key) != selected[key] for key in SELECTION_FIELDS
                        ):
                            raise ValueError(
                                "运行中的模型与本次明确选择不一致；未自动选择其他模型"
                            )
                        board.pid = pid
                        runtime = current
                        break
                time.sleep(0.5)
            else:
                raise TimeoutError("所选推理尚未回读就绪；未自动启用其他模型")
        finally:
            job.protected = False
    elif not desired and (
        enabled or fixed["manifest"]["launch_mode"] != "preview" or board.pid <= 0
    ):
        from console_preview import enter_preview

        enter_preview(board, "global", bundle, job)
    job.check()
    job.protected = True
    try:
        path = board.remote + "/runtime_options.json"
        temporary = path + "." + uid() + ".tmp"
        raw = (
            json.dumps(dict(schema_version=1, show_roi=values["show_roi"])) + "\n"
        ).encode()
        with board.s.open(temporary, "wb") as stream:
            stream.write(raw)
        board.s.posix_rename(temporary, path)
        if board.bytes(path) != raw:
            raise ValueError("ROI框显示开关持久配置回读不一致")
        job.update(stage="readback", message="等待推理与ROI框显示开关实际生效")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            runtime = board.fresh("runtime_status.json")
            if (
                runtime.get("pid") == board.pid
                and runtime["inference_enabled"] is desired
                and runtime.get("runtime_options", {}).get("show_roi")
                is values["show_roi"]
            ):
                break
            time.sleep(0.25)
        else:
            raise TimeoutError("全局开关已保存，板端尚未回读生效；请读取实际状态后重试")
        current = board.status()
        result = dict(
            board=current, runtime_options=current["runtime_options"], persistent=True
        )
        write(bundle / "result.json", result)
        return result
    finally:
        job.protected = False
