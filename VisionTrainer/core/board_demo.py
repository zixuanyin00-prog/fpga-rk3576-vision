from pathlib import Path
import hashlib
import json
import re
import secrets
import shlex
import shutil
import time
from collections import OrderedDict
from . import store as db
from .rectangles import settings
from .rect_capture import status, request, read


def candidate(project, run):
    p = db.load(project)
    if run not in p["runs"]:
        raise ValueError("请选择本场景训练版本")
    folder = db.project_path(project) / "runs" / run
    manifest = db.read(folder / "training_manifest.json")
    trained = manifest.get("training_completed_beijing")
    if not trained:
        raise ValueError("缺少实际训练完成时间，不能部署")
    hashes = {}
    for name in ("detector_fp16.rknn", "config.json"):
        path = folder / name
        if not path.is_file():
            raise ValueError("缺少 " + name + "，请先完成RKNN转换")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if manifest.get("output_hashes", {}).get(name) != hashes[name]:
            raise ValueError("训练记录与模型文件哈希不符：" + name)
    pt = folder / "detector.pt"
    if not pt.is_file():
        raise ValueError("缺少PC测试模型")
    pt_sha = hashlib.sha256(pt.read_bytes()).hexdigest()
    if manifest.get("output_hashes", {}).get("detector.pt") != pt_sha:
        raise ValueError("PC模型与训练记录不符")
    tests = []
    for path in (folder / "tests").glob("*/result.json"):
        result = db.read(path)
        chosen = result.get("selected_model", {})
        rows = result.get("rows", [])
        if (
            chosen.get("project") == project
            and chosen.get("run") == run
            and chosen.get("training_completed_beijing") == trained
            and chosen.get("model_sha256") == pt_sha
            and result.get("same_imaging_parameters") is True
            and result.get("used_for_training") is False
            and result.get("frames") == 10
            and len(rows) == 10
            and len(
                {
                    r.get("source_frame_id")
                    for r in rows
                    if type(r.get("source_frame_id")) is int
                }
            )
            == 10
            and len({r.get("sha256") for r in rows if r.get("sha256")}) == 10
        ):
            tests.append(path)
    if not tests:
        raise ValueError(
            "请先完成此模型的10张独立源帧PC最终测试；旧模型或不完整记录不能用于部署"
        )
    config = db.read(folder / "config.json")
    return dict(
        project=project,
        run=run,
        project_name=p["name"],
        training_started_beijing=manifest.get("training_started_beijing"),
        training_completed_beijing=trained,
        batch=manifest.get("batch"),
        scene=manifest.get("scene"),
        training_rectangle=manifest.get("roi"),
        hashes=hashes,
        input_transform=config.get("input_transform", "native_512x288"),
        tested=True,
        pc_test_count=len(tests),
    )


def prepare(service, args):
    selected = candidate(args["project"], args["run"])
    if (
        args.get("trained_at") != selected["training_completed_beijing"]
        or args.get("hashes") != selected["hashes"]
        or args.get("confirmed") is not True
    ):
        raise ValueError("请按训练北京时间、场景和哈希明确选择本次部署模型")
    config = settings(args["settings"])
    if (
        config["rectangle"][2:] != [512, 288]
        and selected["input_transform"] != "stretch_bilinear"
    ):
        raise ValueError("此模型没有自由尺寸训练记录；请使用新采集流程训练后部署")
    bundle = db.project_path(args["project"]) / "deployments" / db.uid()
    bundle.mkdir(parents=True)
    source = db.project_path(args["project"]) / "runs" / args["run"]
    for name in selected["hashes"]:
        (bundle / "model").mkdir(exist_ok=True)
        shutil.copy2(source / name, bundle / "model" / name)
    db.write(
        bundle / "selection.json", dict(selected, confirmed=True, selected_at=db.now())
    )
    db.write(bundle / "demo_config.json", config)
    service.deployment_selection = dict(
        bundle=str(bundle),
        selection=selected,
        nonce=secrets.token_hex(24),
        created=time.monotonic(),
    )
    return dict(
        bundle=str(bundle),
        selection=selected,
        nonce=service.deployment_selection["nonce"],
        status="prepared_not_deployed",
    )


def deploy(service, args):
    choice = getattr(service, "deployment_selection", None)
    if (
        not choice
        or args.get("nonce") != choice["nonce"]
        or time.monotonic() - choice["created"] > 600
    ):
        raise ValueError("部署选择已过期，请重新按训练时间选型")
    if service.busy():
        raise ValueError("请等待当前任务完成")
    service.deployment_selection = None
    return service.launch(
        "deploy", choice["selection"]["project"], lambda: deploy_action(service, choice)
    )


def deploy_action(service, choice):

    bundle = Path(choice["bundle"])
    selected = choice["selection"]
    c = service.connect()
    s = c.open_sftp()
    remote = service.settings["remote"].rstrip("/")

    def run(command):
        _, o, e = c.exec_command(command, timeout=30)
        out = o.read().decode()
        err = e.read().decode()
        if o.channel.recv_exit_status():
            raise RuntimeError(err or out)
        return out.strip()

    try:
        current = status(s, remote)
        if not re.fullmatch(r"/root/[A-Za-z0-9_/-]+", remote):
            raise ValueError("运行目录无效")
        pid = int(run("systemctl show fpga-vision-v3.service -p MainPID --value"))
        if current["pid"] != pid or s.readlink(f"/proc/{pid}/cwd") != remote:
            raise ValueError("活动运行目录已改变")
        unit = run("systemctl cat fpga-vision-v3.service")
        (bundle / "before_unit.txt").write_text(unit, encoding="utf-8")
        runtime = read(s, remote + "/runtime_status.json")
        monitors = runtime["display_outputs"]
        if not monitors or any(not re.fullmatch("[A-Za-z0-9_-]+", m) for m in monitors):
            raise ValueError("显示端清单无效")
        for name, expected in selected["hashes"].items():
            if (
                hashlib.sha256((bundle / "model" / name).read_bytes()).hexdigest()
                != expected
            ):
                raise ValueError("已选模型文件改变")
        dest = remote + "/deployments/" + bundle.name
        run("mkdir -p " + shlex.quote(dest + "/model"))
        for name, expected in selected["hashes"].items():
            s.put(str(bundle / "model" / name), dest + "/model/" + name)
            if (
                hashlib.sha256(s.open(dest + "/model/" + name).read()).hexdigest()
                != expected
            ):
                raise ValueError("上板校验失败")
        nonce = secrets.token_hex(24)
        ticket = dict(
            selected,
            confirmed=True,
            nonce=nonce,
            fine_sha256=selected["hashes"]["detector_fp16.rknn"],
            config_sha256=selected["hashes"]["config.json"],
            boot_id=s.open("/proc/sys/kernel/random/boot_id").read().decode().strip(),
            expires_monotonic=float(s.open("/proc/uptime").read().split()[0]) + 120,
        )
        ticket_path = dest + "/selection.json"
        with s.open(ticket_path, "w") as f:
            f.write(json.dumps(ticket))
        if status(s, remote)["version"] != current["version"]:
            raise ValueError("部署前运行状态改变，未停止服务")
        run("systemctl stop fpga-vision-v3.service")

        code = (
            "from pathlib import Path; r=Path("
            + repr(remote)
            + "); d=Path("
            + repr(dest)
            + "); p=r/'model'; p.rename(d/'previous_model') if p.exists() or p.is_symlink() else None; p.symlink_to(d/'model',target_is_directory=True)"
        )
        run("python3 -c " + shlex.quote(code))
        s.put(str(bundle / "demo_config.json"), remote + "/demo_config.json")
        override = (
            "[Service]\nWorkingDirectory="
            + remote
            + "\nExecStartPre=\nExecStart=\nExecStart=/root/integrated_vision_v3_20261021/.venv/bin/python -u "
            + remote
            + "/run_live.py --inference --monitors "
            + " ".join(monitors)
            + "\nEnvironment=VISION_MODEL_SELECTION_NONCE="
            + nonce
            + "\nEnvironment=VISION_MODEL_SELECTION_TICKET="
            + ticket_path
            + "\nStandardOutput=append:"
            + remote
            + "/run.log\nStandardError=append:"
            + remote
            + "/run.log\nRestart=no\n"
        )
        run("mkdir -p /run/systemd/system/fpga-vision-v3.service.d")
        with s.open(
            "/run/systemd/system/fpga-vision-v3.service.d/zzzzzzz-free-rectangle.conf",
            "w",
        ) as f:
            f.write(override)
        run("systemctl daemon-reload")
        run("systemctl start fpga-vision-v3.service")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                after = status(s, remote)
            except Exception:
                continue
            if (
                after["pid"] != pid
                and after.get("inference_enabled")
                and after["model_sha256"] == ticket["fine_sha256"]
            ):
                break
        else:
            raise ValueError("新模型未就绪；未自动回退旧模型，请检查日志")
        db.write(
            bundle / "deployment_result.json",
            dict(
                status="running_display_acceptance_pending",
                selection=selected,
                remote=remote,
                board=after,
                fpga_programmed=False,
            ),
        )
    finally:
        s.close()
        c.close()


def demo_state(service):
    c = service.connect()
    s = c.open_sftp()
    remote = service.settings["remote"].rstrip("/")
    try:
        value = status(s, remote)
        value["failure"] = pin_failure(service, s, remote, value)
        value["runtime"] = read(s, remote + "/runtime_status.json")
        value["charts"] = {}
        for monitor in value["runtime"].get("display_outputs", []):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", monitor):
                continue
            try:
                report = read(s, remote + "/screen_performance_" + monitor + ".json")
            except FileNotFoundError:
                continue
            value["charts"][monitor] = {
                k: report.get(k)
                for k in (
                    "recorded_monotonic",
                    "chart_interval_seconds",
                    "chart_sample_count",
                    "chart_session_id",
                    "chart_latest",
                    "chart_enabled_fields",
                )
            }
        return value
    finally:
        s.close()
        c.close()


def apply(service, args):
    if service.busy():
        raise ValueError("采集或部署任务正在运行")
    values = settings(args["values"])
    expected = args.get("expected_version")
    if not expected:
        raise ValueError("请先读取板端当前配置")

    def work():
        c = service.connect()
        s = c.open_sftp()
        try:
            request(
                service,
                s,
                service.settings["remote"].rstrip("/"),
                dict(operation="configure", values=values, expected_version=expected),
                None,
            )
        finally:
            s.close()
            c.close()

    return service.launch("demo_config", args.get("project"), work)


def failure_image(service, sha):
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("无效画面哈希")
    with service.lock:
        cached = getattr(service, "demo_failure_cache", {}).get(sha)
        if cached is not None:
            return cached["raw"]
    raise ValueError("失败画面缓存已过期，请刷新板端状态")


def pin_failure(service, s, remote, current):

    epoch = (current["pid"], current["model_sha256"], current["version"])
    with service.lock:
        if getattr(service, "demo_failure_epoch", None) != epoch:
            service.demo_failure_epoch = epoch
            service.demo_failure_cache = OrderedDict()
    for _ in range(5):
        try:
            meta = read(s, remote + "/demo_failure/latest.json")
            if (
                meta["model_sha256"] != current["model_sha256"]
                or meta["version"] != current["version"]
            ):
                return None
            if not re.fullmatch(r"[0-9a-f]{32}\.png", meta["image"]):
                raise ValueError("无效失败画面路径")
            raw = s.open(remote + "/demo_failure/" + meta["image"]).read()
            if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                continue
        except FileNotFoundError:
            continue
        with service.lock:
            if service.demo_failure_epoch != epoch:
                return None
            cache = service.demo_failure_cache
            cache[meta["sha256"]] = dict(raw=raw, metadata=meta)
            cache.move_to_end(meta["sha256"])
            while (
                len(cache) > 8
                or len(cache) > 1
                and sum(len(v["raw"]) for v in cache.values()) > 16 * 1024 * 1024
            ):
                cache.popitem(last=False)
        return meta
    with service.lock:
        if service.demo_failure_epoch == epoch and service.demo_failure_cache:
            return next(reversed(service.demo_failure_cache.values()))["metadata"]
    return None


def reset_counts(service, args):
    if service.busy():
        raise ValueError("采集或部署任务正在运行")
    if not args.get("expected_version") or not args.get("expected_session_id"):
        raise ValueError("请先读取板端当前计数会话")

    def work():
        c = service.connect()
        s = c.open_sftp()
        try:
            result = request(
                service,
                s,
                service.settings["remote"].rstrip("/"),
                dict(
                    operation="reset_counts",
                    expected_version=args["expected_version"],
                    expected_session_id=args["expected_session_id"],
                ),
                None,
            )
            service.job["result"] = result
        finally:
            s.close()
            c.close()

    return service.launch("count_reset", args.get("project"), work)
