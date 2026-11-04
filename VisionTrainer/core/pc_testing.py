from pathlib import Path
import copy
import hashlib
import time
from . import store as db


def camera_state(service):
    deadline = time.monotonic() + 10
    while True:
        service.check_cancel()
        s = service.camera_console.status()
        if s.get("connected"):
            return s
        if time.monotonic() >= deadline:
            raise ValueError(s.get("error") or "相机连接尚未就绪")
        time.sleep(0.2)


def same_parameters(profile, status):
    board = status["board"]
    differences = []
    for key in ("shr0", "gain_code", "vmax", "hmax"):
        if profile["sensor"].get(key) != board["sensor"].get(key):
            differences.append("sensor." + key)
    for key in ("rg", "bg", "contrast", "brightness", "gaussian", "saturation", "he"):
        if profile["isp"]["config"].get(key) != board["isp"]["config"].get(key):
            differences.append("ISP." + key)
    for key in ("gain_q8", "strength_q8", "knots"):
        if profile["enhancement"].get(key) != board["automatic"]["enhancement"].get(
            key
        ):
            differences.append("enhancement." + key)
    if profile["isp"].get("wb_auto") != board["isp"].get("wb_auto"):
        differences.append("white balance mode")
    return differences


def operation(service, op, values):
    before = camera_state(service)
    if before.get("busy"):
        raise ValueError("相机正在处理其他请求，请稍后重试")
    ticket = service.camera_console.submit(
        op,
        values,
        dict(
            identity=before["board"]["identity"],
            sensor_request_id=before["board"]["sensor"]["request_id"],
        ),
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        service.check_cancel()
        time.sleep(0.2)
        current = camera_state(service)
        if current.get("job_id") != ticket["id"]:
            raise ValueError("相机控制被其他操作改变，测试未继续")
        if not current.get("busy"):
            if current.get("apply_error"):
                raise ValueError(current["apply_error"])
            if current.get("last_result", {}).get("id") != ticket["id"]:
                continue
            return current
    raise ValueError("等待相机确认超时")


def start_test(service, id, run, trained_at):
    if service.busy():
        raise ValueError("请等待当前任务结束")
    p = db.load(id)
    if run not in p["runs"]:
        raise ValueError("本环境不存在该模型版本")
    folder = db.project_path(id) / "runs" / run
    manifest = db.read(folder / "training_manifest.json")
    recorded = manifest.get("training_completed_beijing")
    if not recorded or trained_at != recorded:
        raise ValueError("请按实际训练时间选择本次PC测试模型")
    profile = p.get("capture_profiles", {}).get(manifest.get("batch"))
    if not profile:
        raise ValueError("该模型缺少训练批次成像配置，不能声称同ISP测试")
    selected = dict(
        project=id,
        run=run,
        training_completed_beijing=recorded,
        batch=manifest["batch"],
        model_sha256=hashlib.sha256((folder / "detector.pt").read_bytes()).hexdigest(),
    )
    return service.launch(
        "test",
        id,
        lambda: run_test(
            service, id, folder, manifest, copy.deepcopy(profile), selected
        ),
    )


def run_test(service, id, folder, manifest, profile, selected):
    service.message("检查训练时的曝光、增益、ISP和直方图映射，准备10张独立测试图")
    status = camera_state(service)
    mismatch = same_parameters(profile, status)
    if mismatch:
        raise ValueError(
            "当前成像配置与训练不同："
            + ", ".join(mismatch)
            + "。请先恢复该训练批次的参数；未开始采样。"
        )
    operation(service, "isp", dict(auto=dict(frozen=True)))
    status = operation(service, "readback", {})
    mismatch = same_parameters(profile, status)
    if mismatch:
        raise ValueError("实读成像参数与训练不一致：" + ", ".join(mismatch))
    p = db.load(id)
    train = next(
        f
        for f in p["frames"]
        if f.get("batch") == manifest["batch"] and f.get("role") == "train"
    )
    p["calibration"] = copy.deepcopy(train["calibration"])
    db.save(p)
    service.approve_environment(id, True, internal=True)
    service.message("采集10张独立原生测试图；不用于训练")
    frames = service.capture_action(id, "test")
    if len(frames) != 10:
        raise ValueError("本次不足10张独立测试图，未运行模型；已采数据保留")
    current = camera_state(service)
    mismatch = same_parameters(profile, current)
    if mismatch:
        raise ValueError("采样期间成像配置变化，禁止作为同ISP测试")
    test_id = db.uid()
    out = folder / "tests" / test_id
    out.mkdir(parents=True)
    job = dict(
        root=str(db.project_path(id)),
        output=str(out),
        test_id=test_id,
        frames=frames,
        selected_model=selected,
        model=str(folder / "detector.pt"),
        config=str(folder / "config.json"),
        training_profile=profile,
        captured_profile=db.load(id)["capture_profiles"][frames[0]["batch"]],
        same_imaging_parameters=True,
        white_balance_policy=profile.get("white_balance_policy", "manual_fixed"),
        effective_white_balance_gains_verified=(
            False if profile["isp"].get("wb_auto") else None
        ),
    )
    db.write(out / "job.json", job)
    db.write(out / "selected_model.json", selected)
    service.message("在PC运行已选识别模型并生成10张推理输出图")
    service.subprocess("infer", out / "job.json", "pc_test_worker.py")
    result = db.read(out / "result.json")
    db.write(folder / "latest_test.json", result)
    service.job["test_id"] = test_id
    service.job["model_run"] = selected["run"]
