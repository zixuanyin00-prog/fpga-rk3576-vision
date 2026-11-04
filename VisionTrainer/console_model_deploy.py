from pathlib import Path, PurePosixPath
import json
import math
import re
import shlex
import time

from console_store import now, sha, uid, write

SERVICE = "fpga-vision-v3.service"
AUTH_FILE = "/run/systemd/system/" + SERVICE + ".d/zzzzzzzzz-model-choice.conf"
ACTIVATION_FILE = (
    "/run/systemd/system/" + SERVICE + ".d/zzzzzzzzzzzzzzzzzzzzz-selected-model.conf"
)
MODEL_FILES = frozenset({"detector_fp16.rknn", "config.json"})
MODEL_ARTIFACTS = frozenset({"detector_fp16.rknn", "detector_int8.rknn"})
AUTH_ENV = frozenset({"VISION_MODEL_SELECTION_NONCE", "VISION_MODEL_SELECTION_TICKET"})
MODEL_CONFIG_FIELDS = frozenset(
    {
        "labels",
        "titles",
        "class_ids",
        "class_mapping",
        "input_size",
        "input_transform",
        "mean",
        "std",
        "object_threshold",
        "state_threshold",
        "complete_body_margin",
        "window_model_id",
        "pixel_policy",
        "window_stride",
        "bbox_coordinates",
        "bbox_size_normalization",
        "center_stride",
        "output_size",
        "output_format",
        "architecture",
        "output_layout",
        "output_channels",
        "quantization",
        "image_processing",
    }
)


def snapshot_runtime(board):

    from console_runtime_probe import probe_script

    parts = [
        part.decode()
        for part in board.bytes("/proc/" + str(board.pid) + "/cmdline").split(b"\0")
        if part
    ]
    if not parts or not re.fullmatch(r"/[A-Za-z0-9_./-]+python(?:[0-9.]*)?", parts[0]):
        raise ValueError("无法确认当前运行进程的 Python 解释器；未停止服务")
    result = json.loads(
        board.command(parts[0] + " -I -c " + shlex.quote(probe_script(pid=board.pid)))
    )
    if result["command"] != parts or result.get("pid", board.pid) != board.pid:
        raise ValueError("板端进程在运行版本采集期间变化；未停止服务")
    if result["unsupported_path_mutations"]:
        raise ValueError(
            "当前入口有未支持的模块搜索路径修改；未停止服务："
            + ";".join(result["unsupported_path_mutations"])
        )
    return result


def _differences(before, after, after_commit=False):
    differences = []
    for field in ("command", "entry", "cwd", "modules", "config_files"):
        if before[field] != after[field]:
            if field in ("modules", "config_files"):
                for name in sorted(set(before[field]) | set(after[field])):
                    if before[field].get(name) != after[field].get(name):
                        a = before[field].get(name, {})
                        b = after[field].get(name, {})
                        if (
                            field == "config_files"
                            and isinstance(a.get("json"), dict)
                            and isinstance(b.get("json"), dict)
                        ):
                            if (
                                after_commit
                                and name == before["root"] + "/image_controls.json"
                            ):
                                old_config = a["json"].get("config")
                                new_config = b["json"].get("config")
                                if (
                                    isinstance(old_config, dict)
                                    and isinstance(new_config, dict)
                                    and old_config.get("revision")
                                    != new_config.get("revision")
                                ):
                                    old_parameters = dict(
                                        a["json"],
                                        config={
                                            k: v
                                            for k, v in old_config.items()
                                            if k != "revision"
                                        },
                                    )
                                    new_parameters = dict(
                                        b["json"],
                                        config={
                                            k: v
                                            for k, v in new_config.items()
                                            if k != "revision"
                                        },
                                    )
                                    if old_parameters == new_parameters:
                                        continue
                            keys = set(a["json"]) | set(b["json"])
                            changed = [
                                key
                                for key in sorted(keys)
                                if a["json"].get(key) != b["json"].get(key)
                            ]
                            differences.extend(
                                field + "." + name + "." + key for key in changed
                            )
                            if not changed:
                                differences.append(field + "." + name + ".bytes")
                        else:
                            differences.append(field + "." + name)
            else:
                differences.append(field)
    a, b = before["service"], after["service"]
    for field in ("exec_start", "exec_start_definition", "working_directory"):
        if a[field] != b[field]:
            differences.append("service." + field)
    old_env = {k: v for k, v in a["environment"].items() if k not in AUTH_ENV}
    new_env = {k: v for k, v in b["environment"].items() if k not in AUTH_ENV}
    if old_env != new_env:
        differences.extend(
            "environment." + k
            for k in sorted(set(old_env) | set(new_env))
            if old_env.get(k) != new_env.get(k)
        )
    if not after_commit and a.get("unit_sha256") != b.get("unit_sha256"):
        differences.append("service.unit_sha256")
    return differences


def validate_plan(before, plan):

    allowed = {
        "kind",
        "files",
        "metadata_files",
        "command",
        "entry",
        "cwd",
        "module_changes",
        "config_changes",
        "environment_changes",
        "model_config",
        "transaction_directory",
        "model_link",
        "activate_inference",
    }
    errors = ["未知计划字段：" + k for k in sorted(set(plan) - allowed)]
    if plan.get("kind") != "model_switch":
        errors.append("kind 必须是 model_switch；运行时更新须独立执行")
    files = set(plan.get("files", {}))
    if (
        "config.json" not in files
        or len(files & MODEL_ARTIFACTS) != 1
        or files - MODEL_ARTIFACTS - {"config.json"}
    ):
        errors.append("模型文件必须是 config.json 和一个 FP16 或 INT8 detector")
    if plan.get("metadata_files") != ["selection.json"]:
        errors.append("仅允许 selection.json 模型授权元数据")
    target_command = (
        before["command"]
        if "--inference" in before["command"]
        else before["command"] + ["--inference"]
    )
    if plan.get("command") != target_command:
        errors.append("仅允许在现有入口追加 --inference")
    if plan.get("activate_inference") != ("--inference" not in before["command"]):
        errors.append("推理激活状态不匹配")
    for field in ("entry", "cwd"):
        if plan.get(field) != before[field]:
            errors.append("禁止修改运行入口：" + field)
    errors.extend(
        "禁止修改运行模块：" + name for name in sorted(plan.get("module_changes", {}))
    )
    errors.extend(
        "禁止修改现场配置：" + name for name in sorted(plan.get("config_changes", {}))
    )
    changes = plan.get("environment_changes", {})
    errors.extend(
        "禁止修改非模型环境：" + name for name in sorted(set(changes) - AUTH_ENV)
    )
    if set(changes) != AUTH_ENV:
        errors.append("必须签发完整的新模型选择 nonce/ticket")
    config = plan.get("model_config")
    if not isinstance(config, dict):
        errors.append("config.json 必须是模型配置对象")
    else:
        errors.extend(
            "白名单外模型配置字段：" + name
            for name in sorted(set(config) - MODEL_CONFIG_FIELDS)
        )

        labels = config.get("labels")
        if (
            not isinstance(labels, list)
            or not 1 <= len(labels) <= 256
            or any(not isinstance(label, str) or not label for label in labels)
            or len(labels) != len(set(labels))
        ):
            errors.append("模型 labels 必须是 1–256 个不同的非空字符串")
        size = config.get("input_size")
        if (
            not isinstance(size, list)
            or len(size) != 2
            or any(type(n) is not int or n <= 0 for n in size)
        ):
            errors.append("模型 input_size 必须是两个正整数")
        else:
            site_file = before["root"] + "/demo_config.json"
            site = before["config_files"].get(site_file, {}).get("json", {})
            rectangle = site.get("rectangle") if isinstance(site, dict) else None
            if (
                not isinstance(rectangle, list)
                or len(rectangle) != 4
                or any(type(n) is not int for n in rectangle)
            ):
                errors.append("无法确认当前现场 ROI 尺寸：" + site_file + ".rectangle")
            elif size != rectangle[2:]:
                errors.append(
                    "模型输入尺寸 "
                    + str(size)
                    + " 与当前现场 ROI 尺寸 "
                    + str(rectangle[2:])
                    + " 不一致；模型切换禁止修改 ROI，请先明确调整合法区"
                )
        if config.get("input_transform") not in (
            "native_tiles_v1",
            "native_full_roi_fpn_integer_crop_v1",
        ):
            errors.append("模型 input_transform 没有明确的原生输入适配接口")
        for field in ("mean", "std"):
            value = config.get(field)
            if (
                not isinstance(value, list)
                or len(value) != 3
                or any(
                    type(v) not in (int, float)
                    or not math.isfinite(v)
                    or (field == "std" and v <= 0)
                    for v in value
                )
            ):
                errors.append("模型 " + field + " 必须是三个有效通道参数")
        for field in ("object_threshold", "state_threshold"):
            if field in config and (
                type(config[field]) not in (int, float)
                or not math.isfinite(config[field])
                or not 0 <= config[field] <= 1
            ):
                errors.append("模型 " + field + " 必须在 0–1 之间")
        mapping = config.get("class_mapping", {})
        if not isinstance(mapping, dict) or set(mapping) - {
            "labels",
            "titles",
            "class_ids",
            "numbers",
        }:
            errors.append("class_mapping 只能包含模型类别映射")
    base = before["root"] + "/model_versions/"
    transaction = plan.get("transaction_directory", "")
    if not transaction.startswith(base) or not re.fullmatch(
        r"[a-f0-9]{32}", transaction[len(base) :]
    ):
        errors.append("模型事务目录必须位于当前运行根目录的 model_versions/<id>")
    if plan.get("model_link") != {
        "path": before["root"] + "/model",
        "target": transaction + "/model",
    }:
        errors.append("禁止修改模型目录之外的链接")
    if (
        changes.get("VISION_MODEL_SELECTION_TICKET")
        != transaction + "/model/selection.json"
    ):
        errors.append("模型授权 ticket 必须位于本次模型事务目录")
    if not re.fullmatch(
        r"[a-f0-9]{32}", str(changes.get("VISION_MODEL_SELECTION_NONCE", ""))
    ):
        errors.append("模型授权 nonce 无效")
    if errors:
        raise ValueError("模型部署计划已阻断；未停止服务：" + "；".join(errors))
    return plan


def _stop_guard(before):

    expected_files = {row["path"]: row["sha256"] for row in before["modules"].values()}
    expected_config = {
        path: row["sha256"] for path, row in before["config_files"].items()
    }
    return """import hashlib,os,subprocess
from pathlib import Path
os.sched_setaffinity(0,{0})
expected=EXPECTED_LITERAL
pid=int(subprocess.check_output(['systemctl','show','fpga-vision-v3.service','-p','MainPID','--value']))
if pid!=expected['pid']:raise ValueError('MainPID changed; service was not stopped')
proc=Path('/proc')/str(pid)
actual=[part.decode() for part in (proc/'cmdline').read_bytes().split(b'\\0') if part]
if actual!=expected['command']:raise ValueError('Runtime command changed; service was not stopped')
if str((proc/'cwd').resolve())!=expected['cwd']:raise ValueError('Runtime cwd changed; service was not stopped')
unit=subprocess.check_output(['systemctl','cat','fpga-vision-v3.service'])
if hashlib.sha256(unit).hexdigest()!=expected['unit_sha256']:raise ValueError('Service unit changed; service was not stopped')
for path,digest in expected['modules'].items():
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:raise ValueError('Runtime module changed; service was not stopped: '+path)
if str(Path(expected['model_link']).resolve())!=expected['model_target']:raise ValueError('Model link changed; service was not stopped')
for path,digest in expected['config'].items():
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:raise ValueError('Site config changed; service was not stopped: '+path)
""".replace(
        "EXPECTED_LITERAL",
        repr(
            dict(
                pid=before["pid"],
                command=before["command"],
                cwd=before["cwd"],
                unit_sha256=before["service"]["unit_sha256"],
                modules=expected_files,
                config=expected_config,
                model_link=before["root"] + "/model",
                model_target=before["model"]["target"],
            )
        ),
    )


def deploy_model(board, local_model_dir, selection, bundle, job):

    model_name = selection.get("model_file", "detector_fp16.rknn")
    if model_name not in MODEL_ARTIFACTS:
        raise ValueError("模型文件类型无效；未停止服务")
    model_files = frozenset({model_name, "config.json"})
    bundle = Path(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    local_model_dir = Path(local_model_dir)
    before = snapshot_runtime(board)
    write(bundle / "runtime_before.json", before)
    target_command = (
        before["command"]
        if "--inference" in before["command"]
        else before["command"] + ["--inference"]
    )
    unset = shlex.split(
        board.command(
            "systemctl show " + SERVICE + " -p UnsetEnvironment --value"
        ).decode()
    )
    retained_unset = [v for v in unset if v.split("=", 1)[0] not in AUTH_ENV]
    activation = (
        "[Service]\nExecStart=\nExecStart="
        + shlex.join(target_command)
        + "\nUnsetEnvironment=\n"
    )
    if retained_unset:
        activation += (
            "UnsetEnvironment=" + " ".join(json.dumps(v) for v in retained_unset) + "\n"
        )
    if board.exists(ACTIVATION_FILE):
        previous = board.bytes(ACTIVATION_FILE)
        if previous.decode() != activation:
            raise ValueError("已有模型激活入口不同；未停止服务，请先核对运行入口")
        (bundle / "previous_model_activation.conf").write_bytes(previous)
    write(
        bundle / "inference_activation_plan.json",
        dict(
            path=ACTIVATION_FILE,
            content=activation,
            command_before=before["command"],
            command_after=target_command,
        ),
    )
    payloads = {name: (local_model_dir / name).read_bytes() for name in model_files}
    for name, raw in payloads.items():
        if sha(raw) != selection["hashes"][name]:
            raise ValueError("选择后模型文件发生变化：" + name + "；未停止服务")
    if (
        sha(payloads[model_name]) != selection["fine_sha256"]
        or sha(payloads["config.json"]) != selection["config_sha256"]
    ):
        raise ValueError("模型文件与本次明确选择的身份哈希不一致；未停止服务")
    config = json.loads(payloads["config.json"])
    remote = before["root"] + "/model_versions/" + uid()
    nonce = uid()
    plan = dict(
        kind="model_switch",
        files={name: sha(raw) for name, raw in payloads.items()},
        metadata_files=["selection.json"],
        command=target_command,
        entry=before["entry"],
        cwd=before["cwd"],
        activate_inference="--inference" not in before["command"],
        module_changes={},
        config_changes={},
        model_config=config,
        transaction_directory=remote,
        model_link=dict(path=before["root"] + "/model", target=remote + "/model"),
        environment_changes=dict(
            VISION_MODEL_SELECTION_NONCE=nonce,
            VISION_MODEL_SELECTION_TICKET=remote + "/model/selection.json",
        ),
    )
    validate_plan(before, plan)
    write(bundle / "model_plan.json", plan)

    if board.exists(AUTH_FILE):
        previous_auth = board.bytes(AUTH_FILE)
        lines = [
            line.strip()
            for line in previous_auth.decode().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        invalid = [
            line
            for line in lines
            if line != "[Service]"
            and not any(
                line.startswith("Environment=" + name + "=") for name in AUTH_ENV
            )
        ]
        if invalid:
            raise ValueError("授权文件含非模型配置，未停止服务：" + ";".join(invalid))
        (bundle / "previous_model_authorization.conf").write_bytes(previous_auth)

    if not before["model"].get("is_symlink"):
        raise ValueError(
            "当前 model 不是可原子切换的链接；需要独立迁移运行时，未停止服务"
        )
    job.update(
        stage="preflight", message="模型专用预检：入口、运行模块及现场配置均保持不变"
    )
    board.command("mkdir -p " + shlex.quote(remote + "/model"))
    for name in sorted(model_files):
        board.s.put(str(local_model_dir / name), remote + "/model/" + name)
        if sha(board.bytes(remote + "/model/" + name)) != plan["files"][name]:
            raise ValueError("上传模型哈希不一致：" + name + "；未停止服务")
    python = before["command"][0]
    compatibility = (
        "import sys,json; sys.path[:0]="
        + repr(before["search_path"])
        + "; from full_roi_runtime import validate_model_config; validate_model_config(json.loads(open("
        + repr(remote + "/model/config.json")
        + ").read()))"
    )
    board.command(python + " -c " + shlex.quote(compatibility))
    current = snapshot_runtime(board)
    differences = _differences(before, current)
    if before["pid"] != current["pid"]:
        differences.append("pid")
    if differences:
        raise ValueError(
            "运行基线在模型准备期间变化；未停止服务：" + "；".join(differences)
        )
    ticket = dict(
        selection,
        confirmed=True,
        nonce=nonce,
        boot_id=board.boot,
        fine_model_path=remote + "/model/" + model_name,
        fine_config_path=remote + "/model/config.json",
        expires_monotonic=float(board.bytes("/proc/uptime").split()[0]) + 120,
    )
    board.atomic(remote + "/model/selection.json", ticket)
    authorization = "[Service]\n" + "".join(
        "Environment=" + key + "=" + plan["environment_changes"][key] + "\n"
        for key in sorted(AUTH_ENV)
    )
    write(
        bundle / "preflight.json",
        dict(
            state="passed",
            checked_at=now(),
            plan=plan,
            runtime_preserved=True,
            site_config_preserved=True,
            upload_hashes_verified=True,
            model_interface_checked=True,
        ),
    )
    job.check()
    job.protected = True
    job.update(stage="commit", message="仅切换明确选择的模型；重启仍使用原运行入口")
    try:
        try:
            board.command(
                python
                + " -I -c "
                + shlex.quote(_stop_guard(before))
                + " && systemctl stop "
                + SERVICE,
                timeout=25,
            )
        except RuntimeError as exc:
            write(
                bundle / "preflight.json",
                dict(
                    state="failed",
                    phase="final_stop_guard",
                    error=str(exc),
                    checked_at=now(),
                ),
            )
            raise ValueError("最终模型切换检查失败；未提交模型：" + str(exc)) from exc
        switch = (
            "from pathlib import Path; import os; p=Path("
            + repr(before["root"] + "/model")
            + '); t=p.with_name(".model-link-'
            + nonce
            + '"); t.symlink_to('
            + repr(remote + "/model")
            + ",target_is_directory=True); os.replace(t,p)"
        )
        board.command(python + " -c " + shlex.quote(switch))
        board.command("mkdir -p " + shlex.quote(str(PurePosixPath(AUTH_FILE).parent)))
        with board.s.open(AUTH_FILE, "w") as stream:
            stream.write(authorization)
        with board.s.open(ACTIVATION_FILE, "w") as stream:
            stream.write(activation)
        board.command("systemctl daemon-reload")
        board.command("systemctl start " + SERVICE, timeout=25)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            pid = int(
                board.command(
                    "systemctl show " + SERVICE + " -p MainPID --value"
                ).strip()
            )
            if pid > 0 and pid != before["pid"]:
                try:
                    after = board.fresh("rect_control/status.json")
                except (OSError, ValueError):
                    time.sleep(0.5)
                    continue
                if (
                    after.get("pid") == pid
                    and after.get("model_sha256") == selection["fine_sha256"]
                    and after.get("inference_enabled")
                ):
                    break
            time.sleep(0.5)
        else:
            raise ValueError(
                "原入口未报告所选新模型就绪；未自动启用历史模型，现场配置和历史文件保留"
            )
        board.pid = pid
        actual = snapshot_runtime(board)
        expected = dict(
            before,
            command=target_command,
            service=dict(
                before["service"],
                exec_start=target_command,
                exec_start_definition=shlex.join(target_command),
            ),
        )
        differences = _differences(expected, actual, after_commit=True)
        if differences:
            raise ValueError(
                "模型已加载，但运行功能或现场配置不一致：" + "；".join(differences)
            )
        for key in AUTH_ENV:
            if (
                actual["service"]["environment"].get(key)
                != plan["environment_changes"][key]
            ):
                raise ValueError("模型授权环境回读不一致：" + key)
        write(bundle / "runtime_after.json", actual)
        result = dict(
            state="loaded",
            selection=selection,
            board=after,
            model_only=True,
            runtime_preserved=True,
            site_config_preserved=True,
            model_files_changed=sorted(model_files),
            model_authorization_file=AUTH_FILE,
            inference_activation_file=ACTIVATION_FILE,
            inference_flag_enabled=True,
            runtime_entry=actual["entry"],
            display_60fps_verified=False,
            board_accuracy_verified=False,
        )
        write(bundle / "result.json", result)
        return result
    finally:
        job.protected = False
