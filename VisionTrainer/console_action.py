import json
import math
import time

CONFIG_PATH = "/root/gpio_action_20261030/action_config.json"
STATUS_PATH = "/run/vision-action/status.json"


def state(board, runtime, rect):
    result = dict(
        installed=False,
        config=None,
        daemon=None,
        fresh=False,
        ready=False,
        error=None,
        line_x=rect["settings"]["rectangle"][0] + rect["settings"]["rectangle"][2] / 2,
    )
    if not board.exists(CONFIG_PATH):
        return result
    result["installed"] = True
    try:
        result["config"] = json.loads(board.bytes(CONFIG_PATH))
        if board.exists(STATUS_PATH):
            daemon = json.loads(board.bytes(STATUS_PATH))
            result["daemon"] = daemon
            now = float(board.bytes("/proc/uptime").split()[0])
            result["fresh"] = (
                not daemon.get("stopped", True)
                and -0.05 <= now - daemon["recorded_monotonic"] < 3
            )
            result["error"] = daemon.get("config_error") or daemon.get("error")
            pin = daemon.get("pin", {})
            if pin.get("gpio") != "GPIO0_C1" or pin.get("active_high") is not False:
                result["error"] = "板端尚未切换到 TX GPIO0_C1 常高、低脉冲输出接口"
            result["ready"] = (
                result["fresh"]
                and daemon.get("configured") is True
                and not result["error"]
            )
        else:
            result["error"] = "GPIO输出服务尚未报告状态"
    except (OSError, ValueError, KeyError) as exc:
        result["error"] = "GPIO状态读取失败：" + str(exc)
    return result


def apply(board, values, job):
    if not isinstance(values, dict) or set(values) != {
        "enabled",
        "target_labels",
        "model_sha256",
        "delay_ms",
        "pulse_ms",
    }:
        raise ValueError("输出设置字段无效，请刷新控制台后重试")
    if type(values["enabled"]) is not bool:
        raise ValueError("输出启用状态必须为开或关")
    labels = values["target_labels"]
    if (
        not isinstance(labels, list)
        or len(labels) > 30
        or any(not isinstance(v, str) for v in labels)
        or len(set(labels)) != len(labels)
    ):
        raise ValueError("请选择有效且不重复的姿态")
    for key, low, high in [("delay_ms", 0, 5000), ("pulse_ms", 1, 1000)]:
        value = values[key]
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not low <= value <= high
        ):
            raise ValueError("延迟范围为0–5000毫秒，脉宽范围为1–1000毫秒")
    runtime = board.fresh("runtime_status.json")
    selected = runtime.get("selected_model") or {}
    actual = runtime.get("inference", {}).get("model_sha256")
    if (
        not actual
        or actual != selected.get("fine_sha256")
        or values["model_sha256"] != actual
    ):
        raise ValueError(
            "板端模型已变化，请读取板端后重新选择触发姿态；本操作不加载模型"
        )
    valid = {r["label"] for r in runtime["inference"]["counting"]["class_counts"]}
    if not set(labels) <= valid or values["enabled"] and not labels:
        raise ValueError("启用前至少选择一种当前板端模型的姿态")
    if not board.exists(CONFIG_PATH):
        raise ValueError("板端尚未安装GPIO输出接口")
    if not board.exists(STATUS_PATH):
        raise ValueError("GPIO输出服务尚未报告状态")
    current = json.loads(board.bytes(STATUS_PATH))
    pin = current.get("pin", {})
    now = float(board.bytes("/proc/uptime").split()[0])
    if (
        pin.get("gpio") != "GPIO0_C1"
        or pin.get("active_high") is not False
        or current.get("configured") is not True
        or current.get("stopped", True)
        or not -0.05 <= now - current["recorded_monotonic"] < 3
    ):
        raise ValueError("请等待板端 TX GPIO0_C1 常高、低脉冲输出接口就绪后再保存")
    config = dict(
        schema_version=1,
        enabled=values["enabled"],
        active_high=False,
        target_labels=labels,
        model_sha256=actual,
        delay_ms=values["delay_ms"],
        pulse_ms=values["pulse_ms"],
        trigger_mode="center_line",
    )
    job.check()
    job.update(message="正在保存输出设置，等待GPIO服务实际应用")

    job.protected = True
    temporary = CONFIG_PATH + "." + job.id + ".tmp"
    with board.s.open(temporary, "w") as stream:
        stream.write(json.dumps(config, ensure_ascii=False))
    board.s.posix_rename(temporary, CONFIG_PATH)
    deadline = time.monotonic() + 3
    last_error = None
    while time.monotonic() < deadline:
        if board.exists(STATUS_PATH):
            status = json.loads(board.bytes(STATUS_PATH))
            last_error = status.get("config_error") or status.get("error")
            if (
                status.get("config") == config
                and not status.get("stopped", True)
                and not last_error
            ):
                now = float(board.bytes("/proc/uptime").split()[0])
                if -0.05 <= now - status["recorded_monotonic"] < 3:
                    return dict(
                        config=config,
                        daemon=status,
                        applied=True,
                        message="GPIO输出设置已由板端回读确认",
                    )
        time.sleep(0.15)
    raise TimeoutError(
        "设置文件已保存，但GPIO服务未在3秒内确认；请读取板端查看实际状态后重试"
        + ("：" + str(last_error) if last_error else "")
    )
