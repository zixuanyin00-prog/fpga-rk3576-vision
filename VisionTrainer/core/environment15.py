import hashlib
import json


def approved_profile(status, runtime, now, origin):
    if any(not -0.011 <= now - x["recorded_monotonic"] < 3 for x in (status, runtime)):
        raise ValueError("板端遥测已过期，请稍后重新确认")
    identity = runtime.get("environment_identity") or {}
    if identity != status.get("identity") or identity.get("origin") != origin:
        raise ValueError("当前识别区域与已选采样区域不一致，请先应用所选区域并重新确认")
    sensor = status["sensor"]
    auto = status.get("automatic") or {}
    modes = auto.get("config") or {}
    frozen = modes.get("frozen") is True
    disabled = all(
        modes.get(k) is False
        for k in ("contrast", "histogram", "brightness", "digital_gain", "analog_gain")
    )
    if not (frozen or disabled):
        raise ValueError("请先暂停五项自动调节，再准备采样；保留当前白平衡模式")
    if (
        auto.get("error")
        or sensor.get("sensor_transition") is not False
        or not sensor.get("sensor_register_readback")
        or status.get("busy")
    ):
        raise ValueError("相机尚未稳定或有控制错误，请先完成实读与参数确认")
    e = auto.get("enhancement") or {}
    if not e or e.get("revision") != e.get("effective_revision"):
        raise ValueError("等待FPGA增强参数生效")
    profile = dict(
        identity=identity,
        origin=list(origin),
        sensor=sensor,
        isp=status["isp"],
        enhancement=e,
        auto_frozen=frozen,
        automatic_functions_disabled=disabled,
        white_balance_policy=(
            "automatic_preserved" if status["isp"].get("wb_auto") else "manual_fixed"
        ),
        effective_white_balance_gains_exported=False,
        per_image_enhancement_revision_exported=False,
    )
    profile["profile_digest"] = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return profile


def capture_admission(project, runtime, sftp, remote, origin):
    profile = project.get("environment", {}).get("approved")
    if not profile:
        raise ValueError(
            "请先由相机环境工作流确认当前固定场景配置；本候选不会自动改曝光或冻结AutoISP"
        )
    if (
        profile.get("auto_frozen") is not True
        and profile.get("automatic_functions_disabled") is not True
    ):
        raise ValueError(
            "固定场景采样需相机配置已明确冻结AutoISP或关闭所有自动功能；请在相机任务确认，本任务不操作控制"
        )
    inference = runtime.get("inference") or {}
    context = inference.get("sensor_context") or {}
    if not inference or not context:
        raise ValueError(
            "当前采样接口依赖推理协调器，当前未启用；需本次选型授权后由集成方处理，本候选不自动启动模型"
        )
    status = json.loads(sftp.open(remote + "/camera_console/status.json").read())
    now = float(sftp.open("/proc/uptime").read().split()[0])
    live = approved_profile(status, runtime, now, origin)
    if (
        live["isp"].get("wb_auto") != profile["isp"].get("wb_auto")
        or live["isp"]["config"] != profile["isp"]["config"]
        or any(
            live["enhancement"][k] != profile["enhancement"][k]
            for k in ("gain_q8", "strength_q8", "revision", "knots")
        )
    ):
        raise ValueError("固定成像配置已改变，请重新确认本场景")
    identity = runtime.get("environment_identity") or {}
    if identity != profile.get("identity") or profile.get("origin") != origin:
        raise ValueError("环境身份或ROI与已确认配置不符")
    sensor = json.loads(
        sftp.open(remote + "/camera_environment/sensor_status.json").read()
    )
    if sensor.get("sensor_transition") is not False or not sensor.get(
        "sensor_register_readback"
    ):
        raise ValueError("传感器回读或稳定条件未通过")
    for key in ("shr0", "gain_code", "vmax", "hmax"):
        if sensor.get(key) != profile.get("sensor", {}).get(key):
            raise ValueError("曝光/增益与已确认配置不符")
    if sensor.get("sensor_generation") != context.get("sensor_generation"):
        raise ValueError("传感器代际已改变")
    required = ("boot_id", "runtime_session_id", "region_version", "isp_generation")
    if (
        any(identity.get(k) is None for k in required)
        or sensor.get("sensor_stable_source_id") is None
    ):
        raise ValueError("缺少固定场景身份或稳定源帧屏障")
    return dict(
        environment_profile_digest=profile["profile_digest"],
        expected_sensor_generation=sensor["sensor_generation"],
        sensor_stable_source_id=sensor["sensor_stable_source_id"],
        **{k: identity[k] for k in required}
    )
