from pathlib import Path
import io
import json
import math
import re
import shlex
import struct
import time
import numpy as np
from PIL import Image
from console_store import ROOT, uid, sha, rectangle, write
from process_controls import IDENTITY, validate_patch


class Board:
    def __init__(self, settings):
        self.settings = settings

    def __enter__(self):
        from service import Service

        service = Service()
        service.settings = dict(self.settings)
        self.client = service.connect()
        self.s = self.client.open_sftp()
        self.s.get_channel().settimeout(12)
        try:
            self.pid = int(
                self.command(
                    "systemctl show fpga-vision-v3.service -p MainPID --value"
                ).strip()
            )
            if self.pid <= 0:
                raise ValueError("板端视觉服务未运行；请先启动已有预览服务")
            self.remote = self.s.readlink("/proc/" + str(self.pid) + "/cwd")
            if not re.fullmatch(r"/root/[A-Za-z0-9_/-]+", self.remote):
                raise ValueError("板端运行目录无效")
            self.boot = self.bytes("/proc/sys/kernel/random/boot_id").decode().strip()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.s.close()
        self.client.close()

    def command(self, command, timeout=12):
        _, out, err = self.client.exec_command(command, timeout=timeout)
        raw = out.read()
        error = err.read()
        if out.channel.recv_exit_status():
            raise RuntimeError(error.decode(errors="replace") or "板端命令失败")
        return raw

    def bytes(self, path):
        with self.s.open(path, "rb") as f:
            return f.read()

    def read(self, name):
        return json.loads(self.bytes(self.remote + "/" + name))

    def fresh(self, name):
        state = self.read(name)
        uptime = float(self.bytes("/proc/uptime").split()[0])
        if not -0.05 <= uptime - state.get("recorded_monotonic", -100) < 5:
            raise ValueError(
                "板端状态尚未更新：" + name + "；稍后重试即可，不影响新建流程"
            )
        return state

    def status(self):
        camera = self.fresh("camera_console/status.json")
        runtime = self.fresh("runtime_status.json")
        rect = self.fresh("rect_control/status.json")
        displays = {}
        for monitor in runtime.get("display_outputs", []):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", monitor):
                continue
            try:
                perf = self.read("screen_performance_" + monitor + ".json")
                source = self.read("display_" + monitor + ".json")
                displays[monitor] = dict(
                    fresh_fps=source.get("actual_fresh_fps"),
                    unique_frames=source.get("unique_frames"),
                    source_gaps=source.get("skipped_source_frames"),
                    redraw_fps=perf.get("window_redraws_per_second"),
                    recorded_monotonic=source.get("recorded_monotonic"),
                )
            except FileNotFoundError:
                displays[monitor] = dict(fresh_fps=None, note="缺少源帧去重遥测")
        from console_action import state as action_state

        return dict(
            connected=True,
            camera=camera,
            runtime=runtime,
            rect=rect,
            displays=displays,
            action_output=action_state(self, runtime, rect),
            root=self.remote,
            boot_id=self.boot,
        )

    def atomic(self, path, value):
        tmp = path + "." + uid() + ".tmp"
        with self.s.open(tmp, "w") as f:
            f.write(json.dumps(value, ensure_ascii=False))
        self.s.rename(tmp, path)

    def exists(self, path):
        try:
            self.s.stat(path)
            return True
        except FileNotFoundError:
            return False

    def transaction(self, folder, body, job, timeout=100):
        base = self.remote + "/" + folder
        deadline = time.monotonic() + timeout

        while True:
            occupied = False
            for name in ("processing.json", "request.json"):
                path = base + "/" + name
                if not self.exists(path):
                    continue
                try:
                    pending = json.loads(self.bytes(path))
                    ident = pending.get("id", "")
                except (OSError, ValueError):
                    ident = ""
                if re.fullmatch(r"[A-Za-z0-9_]{1,80}", str(ident)) and self.exists(
                    base + "/" + ident + "/result.json"
                ):
                    try:
                        self.s.rename(path, base + "/" + name + ".completed." + uid())
                    except OSError:
                        pass
                else:

                    stale = False
                    if name == "request.json" and ident:
                        if folder == "rect_control":
                            live = self.fresh("rect_control/status.json")
                            stale = pending.get("expected_version") != live["version"]
                        elif folder == "camera_console":
                            live = self.fresh("camera_console/status.json")
                            old = pending.get("identity", {})
                            current = live["identity"]
                            stale = any(
                                old.get(k) != current.get(k)
                                for k in (
                                    "boot_id",
                                    "runtime_session_id",
                                    "firmware_sha256",
                                    "driver_sha256",
                                )
                            )
                    if stale:
                        try:
                            self.s.rename(
                                path, base + "/" + name + ".superseded." + uid()
                            )
                        except OSError:
                            occupied = True
                    else:
                        occupied = True
            if not occupied:
                break
            job.check()
            job.update(message="板端上一笔有界事务尚未结束，正在等待结果")
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "板端事务未结束；当前操作已退出等待。可新建流程，稍后重新操作；没有覆盖正在写入的采集"
                )
            time.sleep(0.25)
        job.check()
        ident = uid()
        body = dict(body, id=ident)

        if folder == "rect_control":
            body["expected_version"] = self.fresh("rect_control/status.json")["version"]
        if folder == "camera_console":
            body["identity"] = self.fresh("camera_console/status.json")["identity"]
        self.atomic(base + "/request.json", body)
        job.update(remote_transaction=ident, message="请求已提交，等待板端实际完成")
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                result = json.loads(self.bytes(base + "/" + ident + "/result.json"))
                break
            except FileNotFoundError:
                time.sleep(0.2)
        else:
            raise TimeoutError(
                "板端结果等待超时，事务 " + ident + "；没有重复提交，原图仍保留在板端"
            )
        if result.get("id") != ident:
            raise ValueError("板端事务编号不一致")
        if not result.get("ok") and result.get("terminal_status") != "exhausted":
            raise ValueError(result.get("error") or "板端未完成操作")
        return result, base + "/" + ident

    def camera(self, operation, values, job):
        if operation == "auto_adjust":
            return self.auto_adjust(job)
        if operation == "patch":
            return self.camera_patch(values, job)
        if operation == "auto_exposure":
            return self.auto_exposure(job)
        if operation == "sensor":
            exposure = float(values["exposure_us"])
            gain = float(values["gain_db"])
            if not 10 <= exposure <= 16000 or not 0 <= gain <= 30:
                raise ValueError("曝光10–16000 μs，模拟增益0–30 dB")

            self.camera("isp", {"auto": {"analog_gain": False}}, job)
            body = dict(
                operation="exposure",
                values=dict(exposure_us=exposure, gain_code=round(gain / 0.3)),
            )
        elif operation == "process":
            body = dict(operation="process", values=validate_patch(values))
        elif operation == "readback":
            body = dict(operation="readback", values={})
        elif operation == "isp":
            limits = {
                "rg": (128, 512),
                "bg": (128, 512),
                "contrast": (76, 768),
                "brightness": (-100, 100),
                "gaussian": (0, 256),
                "digital_gain": (64, 1024),
                "histogram": (0, 256),
            }
            for key, val in values.items():
                if key in limits:
                    if (
                        type(val) is not int
                        or not limits[key][0] <= val <= limits[key][1]
                    ):
                        raise ValueError("参数超出范围：" + key)
                elif key == "wb_auto":
                    if type(val) is not bool:
                        raise ValueError("白平衡开关无效")
                    if val:
                        raise ValueError(
                            "白平衡已统一使用可见R/B参数，请刷新控制台后使用自动调整"
                        )
                elif key == "auto":
                    if not isinstance(val, dict) or set(val) - {
                        "contrast",
                        "brightness",
                        "digital_gain",
                        "analog_gain",
                        "histogram",
                        "frozen",
                        "analog_ceiling_db",
                        "digital_ceiling",
                    }:
                        raise ValueError("自动参数无效")
                else:
                    raise ValueError("不支持的ISP参数：" + key)
            body = dict(operation="isp", isp=values)
        else:
            raise ValueError("相机操作无效")
        result, _ = self.transaction("camera_console", body, job, 40)
        self.wait_camera_result(result["id"])
        return result

    def camera_patch(self, values, job):

        if not isinstance(values, dict) or not values:
            raise ValueError("没有需要保存的参数")
        allowed = {
            "exposure_us",
            "gain_db",
            "rg",
            "bg",
            "contrast",
            "brightness",
            "gaussian",
            "digital_gain",
            "histogram",
            "wb_auto",
            "auto",
        }
        if set(values) - allowed:
            raise ValueError("不支持的成像参数")
        limits = {
            "exposure_us": (10, 16000),
            "gain_db": (0, 30),
            "rg": (128, 512),
            "bg": (128, 512),
            "contrast": (76, 768),
            "brightness": (-100, 100),
            "gaussian": (0, 256),
            "digital_gain": (64, 1024),
            "histogram": (0, 256),
        }
        for key, value in values.items():
            if key in limits:
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not limits[key][0] <= value <= limits[key][1]
                ):
                    raise ValueError("参数超出范围：" + key)
                if key not in ("exposure_us", "gain_db") and type(value) is not int:
                    raise ValueError("参数须为整数：" + key)
            elif key == "wb_auto" and type(value) is not bool:
                raise ValueError("白平衡开关无效")
            elif key == "auto":
                modes = {
                    "contrast",
                    "brightness",
                    "digital_gain",
                    "analog_gain",
                    "histogram",
                    "frozen",
                }
                if not isinstance(value, dict) or set(value) - modes - {
                    "analog_ceiling_db",
                    "digital_ceiling",
                }:
                    raise ValueError("自动参数无效")
                for mode, setting in value.items():
                    if mode in modes and type(setting) is not bool:
                        raise ValueError("自动开关无效：" + mode)
                    if mode not in modes and (
                        type(setting) not in (int, float)
                        or not math.isfinite(setting)
                        or not (
                            0 <= setting <= 30
                            if mode == "analog_ceiling_db"
                            else 1 <= setting <= 4
                        )
                    ):
                        raise ValueError("自动调节上限无效")
        if values.get("wb_auto") is True:
            raise ValueError("白平衡已统一使用可见R/B参数，请刷新控制台")
        if "rg" in values or "bg" in values:
            values = dict(values, wb_auto=False)
        sensor = {k: values[k] for k in ("exposure_us", "gain_db") if k in values}
        isp = {k: v for k, v in values.items() if k not in sensor}
        results = []
        if sensor:

            results.append(self.camera("isp", {"auto": {"analog_gain": False}}, job))
            current = self.fresh("camera_console/status.json")["sensor"]
            exposure = float(sensor.get("exposure_us", current["exposure_us"]))
            gain = float(
                sensor.get(
                    "gain_db", current.get("analog_gain_db", current["gain_code"] * 0.3)
                )
            )
            if not 10 <= exposure <= 16000 or not 0 <= gain <= 30:
                raise ValueError("曝光10–16000 μs，模拟增益0–30 dB")
            result, _ = self.transaction(
                "camera_console",
                dict(
                    operation="exposure",
                    values=dict(exposure_us=exposure, gain_code=round(gain / 0.3)),
                ),
                job,
                40,
            )
            self.wait_camera_result(result["id"])
            results.append(result)
        if isp:
            results.append(self.camera("isp", isp, job))
        results.append(self.camera("readback", {}, job))
        current = self.fresh("camera_console/status.json")
        config = current["isp"]["config"]
        automatic = current["automatic"]["config"]
        enhancement = current["automatic"]["enhancement"]
        if "wb_auto" in values and current["isp"]["wb_auto"] is not values["wb_auto"]:
            raise ValueError("白平衡模式实读与保存请求不一致")
        for key in ("rg", "bg", "contrast", "brightness", "gaussian"):
            if (
                key in values
                and not (
                    key in ("contrast", "brightness")
                    and automatic.get(key)
                    and not automatic.get("frozen")
                )
                and config.get(key) != values[key]
            ):
                raise ValueError("参数实读与保存请求不一致：" + key)
        for key, actual in (
            ("digital_gain", enhancement["gain_q8"]),
            ("histogram", enhancement["strength_q8"]),
        ):
            if (
                key in values
                and not (automatic.get(key) and not automatic.get("frozen"))
                and actual != values[key]
            ):
                raise ValueError("参数实读与保存请求不一致：" + key)
        for key, value in values.get("auto", {}).items():
            if automatic.get(key) != value:
                raise ValueError("自动控制实读与保存请求不一致：" + key)
        return dict(
            ok=True, saved_values=values, transactions=[r["id"] for r in results]
        )

    def wait_camera_result(self, ident):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            state = self.fresh("camera_console/status.json")
            if (state.get("last_result") or {}).get("id") == ident and not state.get(
                "busy"
            ):
                return state
            time.sleep(0.15)
        raise ValueError("控制已返回，但状态发布尚未同步；请实读参数后继续")

    def white_balance_statistics(self, job, last_source=None, after=0):

        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            job.check()
            current = self.fresh("camera_console/status.json")
            snapshot = self.read("camera_console/snapshots.json")
            entry = snapshot.get("entries", {}).get("overview")
            identity = snapshot.get("identity", {})
            actual = current["identity"]
            same = all(
                identity.get(k) == actual.get(k)
                for k in ("boot_id", "runtime_session_id", "isp_generation")
            )
            if (
                entry
                and same
                and entry.get("source_frame_id") != last_source
                and entry.get("recorded_monotonic", 0) > after + 0.25
            ):
                root = current["image_root"]
                name = entry["file"]
                w, h = entry["size"]
                if not re.fullmatch(
                    r"/dev/shm/[A-Za-z0-9_/-]+", root
                ) or not re.fullmatch(r"overview_\d+\.rgb", name):
                    raise ValueError("原生统计快照路径无效")
                if (
                    type(w) is not int
                    or type(h) is not int
                    or not 0 < w <= 1920
                    or not 0 < h <= 1080
                ):
                    raise ValueError("原生统计快照尺寸无效")
                raw = self.bytes(root + "/" + name)
                if len(raw) == w * h * 3 and sha(raw) == entry["sha256"]:
                    rgb = np.frombuffer(raw, np.uint8).reshape(-1, 3)
                    lo = rgb.min(axis=1)
                    hi = rgb.max(axis=1)

                    candidates = (
                        (lo >= 35)
                        & (hi <= 235)
                        & ((hi.astype(np.int16) - lo) <= hi * 0.45)
                    )
                    if int(candidates.sum()) < 256:
                        raise ValueError(
                            "画面中没有足够的灰白参考区域，未自动改变R/B增益；可直接调整R/B数值"
                        )
                    neutral = rgb[candidates]
                    light = neutral.mean(axis=1)
                    neutral = neutral[light >= np.percentile(light, 60)]
                    if len(neutral) < 128:
                        raise ValueError(
                            "灰白参考像素不足，未自动改变R/B增益；可直接调整R/B数值"
                        )
                    means = neutral.mean(axis=0)
                    return dict(
                        source_frame_id=entry["source_frame_id"],
                        means=means.tolist(),
                        neutral_pixels=len(neutral),
                        sha256=entry["sha256"],
                        size=[w, h],
                        pixel_policy="statistics_only_original_bytes_unchanged",
                    )
            time.sleep(0.2)
        raise ValueError("等待参数生效后的新原生统计帧超时，未继续调整白平衡")

    def auto_adjust(self, job):

        modes = ("analog_gain", "digital_gain", "contrast", "brightness", "histogram")
        job.update(message="自动调整：暂停调光，以可见R/B参数校准白平衡")
        self.camera("isp", {"auto": {"frozen": True}}, job)
        config = self.fresh("camera_console/status.json")["isp"]["config"]
        self.camera_patch(
            dict(rg=int(config["rg"]), bg=int(config["bg"]), wb_auto=False), job
        )
        history = []
        last_source = None
        converged = False
        for index in range(6):
            job.check()
            current = self.fresh("camera_console/status.json")
            config = current["isp"]["config"]

            after = (current.get("last_result") or {}).get("completed_monotonic", 0)
            stats = self.white_balance_statistics(job, last_source, after)
            last_source = stats["source_frame_id"]
            r, g, b = stats["means"]
            gain_r = int(config["rg"])
            gain_b = int(config["bg"])
            error = max(abs(r / g - 1), abs(b / g - 1))
            history.append(
                dict(iteration=index + 1, rg=gain_r, bg=gain_b, error=error, **stats)
            )
            job.update(
                white_balance_progress=dict(
                    iteration=index + 1, rg=gain_r, bg=gain_b, reference_error=error
                )
            )
            if error <= 0.04:
                converged = True
                break

            target_r = max(128, min(512, round(gain_r * g / r)))
            target_b = max(128, min(512, round(gain_b * g / b)))
            next_r = gain_r + max(-24, min(24, target_r - gain_r))
            next_b = gain_b + max(-24, min(24, target_b - gain_b))
            if (next_r, next_b) == (gain_r, gain_b):
                break
            if index == 5:
                break
            job.update(
                message=f"自动调整白平衡 {index+1}/6：R {next_r} / B {next_b}，同步到输入参数"
            )
            self.camera_patch(dict(rg=next_r, bg=next_b, wb_auto=False), job)
        job.check()

        self.camera_patch(
            {"auto": dict(frozen=False, **dict.fromkeys(modes, True))}, job
        )
        return dict(
            ok=True,
            white_balance=dict(
                policy="visible_rgb_gains_v1",
                converged=converged,
                history=history,
                message=(
                    "R/B参数已自动校准"
                    if converged
                    else "R/B已保存；灰白参考仍有偏差，请查看画面并直接微调R/B"
                ),
            ),
            automatic_updates_visible_fields=True,
        )

    def auto_exposure(self, job):

        before = self.fresh("camera_console/status.json")
        sensor = before["sensor"]
        exposure = float(sensor["exposure_us"])
        gain = float(sensor.get("analog_gain_db", sensor["gain_code"] * 0.3))
        self.camera("isp", {"auto": {"frozen": True, "analog_gain": False}}, job)
        last_source = None
        history = []
        for _ in range(8):
            job.check()
            deadline = time.monotonic() + 4
            while True:
                current = self.fresh("camera_console/status.json")
                snapshot = self.read("camera_console/snapshots.json")
                entry = snapshot.get("entries", {}).get("overview")
                if entry and entry.get("source_frame_id") != last_source:
                    root = current["image_root"]
                    name = entry["file"]
                    if not re.fullmatch(
                        r"/dev/shm/[A-Za-z0-9_/-]+", root
                    ) or not re.fullmatch(r"overview_\d+\.rgb", name):
                        raise ValueError("预览快照路径无效")
                    raw = self.bytes(root + "/" + name)
                    w, h = entry["size"]
                    if len(raw) == w * h * 3 and sha(raw) == entry["sha256"]:
                        values = np.frombuffer(raw, np.uint8).reshape(-1, 3)
                        brightness = float(values.mean())
                        last_source = entry["source_frame_id"]
                        break
                if time.monotonic() > deadline:
                    raise ValueError("等待新预览帧超时，自动曝光已停止")
                time.sleep(0.2)
            history.append(
                dict(
                    source_frame_id=last_source,
                    brightness=brightness,
                    exposure_us=exposure,
                )
            )
            if abs(brightness - 110) < 8:
                break
            target = max(
                10, min(16000, exposure * max(0.6, min(1.6, 110 / max(brightness, 1))))
            )
            if abs(target - exposure) < 2:
                break
            job.update(
                message="自动曝光一次：当前亮度 " + str(round(brightness)) + "，目标110"
            )
            self.camera("sensor", dict(exposure_us=target, gain_db=gain), job)
            self.camera("readback", {}, job)
            exposure = float(
                self.fresh("camera_console/status.json")["sensor"]["exposure_us"]
            )
            time.sleep(0.6)
        return dict(
            ok=True,
            automatic="one_shot",
            history=history,
            exposure_us=exposure,
            continuous_auto_frozen=True,
        )

    def freeze(self, job, edited=None):

        if edited:
            raise ValueError("成像编辑尚未自动保存，请等待保存完成后再固定参数")
        before = self.fresh("camera_console/status.json")

        if before["isp"].get("wb_auto") is not False:
            raise ValueError(
                "板端仍在使用旧白平衡模式。请先点击“自动调整”或修改R/B参数，等待保存后检查画面，再固定。本次未切换白平衡或采图。"
            )
        if before.get("busy") or before["sensor"].get("sensor_transition"):
            raise ValueError("成像调节尚未完成，请等完成提示、检查画面后再固定")
        expected = self.profile(
            dict(
                camera=before,
                boot_id=self.boot,
                rect=self.fresh("rect_control/status.json"),
            )
        )

        expected["frozen"] = True
        expected["digest"] = sha(
            json.dumps(
                {k: v for k, v in expected.items() if k not in ("identity", "digest")},
                sort_keys=True,
            ).encode()
        )
        job.update(message="暂停自动调节，保留板端当前实际参数")
        paused = self.camera("isp", {"auto": {"frozen": True}}, job)
        if paused.get("pixel_parameters_written") is not False:
            raise ValueError(
                "板端未确认仅暂停自动控制，不能保证保持原画面，尚未采图；需要支持无参数重写的暂停接口"
            )
        self.camera("readback", {}, job)
        deadline = time.monotonic() + 8
        while True:
            state = self.status()
            c = state["camera"]
            s = c["sensor"]
            e = c.get("automatic", {}).get("enhancement", {})
            if (
                not c.get("busy")
                and s.get("sensor_register_readback")
                and s.get("sensor_transition") is False
                and e
                and e.get("effective_revision") == e.get("revision")
                and not c["isp"].get("wb_auto")
            ):
                profile = self.profile(state)
                if profile["digest"] != expected["digest"]:
                    raise ValueError(
                        "暂停前后实际成像参数发生变化，可能有最后一笔自动调节或其他页面操作；尚未采图，请检查已暂停的画面后再固定"
                    )
                profile["freeze_evidence"] = dict(
                    policy="hold_effective_parameters_v2",
                    edited_fields={},
                    applied_fields={},
                    pixel_parameters_written=False,
                    before_digest=expected["digest"],
                    unchanged_fields_not_written=True,
                )
                return profile
            if time.monotonic() > deadline:
                raise ValueError("板端尚未确认参数冻结，请重试本步骤")
            time.sleep(0.2)

    @staticmethod
    def profile(state):
        c = state["camera"]
        s = c["sensor"]
        a = c.get("automatic", {})
        e = a.get("enhancement", {})
        value = dict(
            boot_id=state["boot_id"],
            sensor={k: s.get(k) for k in ("shr0", "gain_code", "vmax", "hmax")},
            isp={
                k: c["isp"]["config"].get(k)
                for k in (
                    "rg",
                    "bg",
                    "contrast",
                    "brightness",
                    "gaussian",
                    "saturation",
                    "he",
                )
            },
            enhancement={k: e.get(k) for k in ("gain_q8", "strength_q8", "knots")},
            wb_auto=c["isp"].get("wb_auto"),
            frozen=a.get("config", {}).get("frozen"),
            identity=c["identity"],
        )
        if c.get("image_processing") == IDENTITY:
            control = c["processing_controls"]
            if control["pending"] or control["fault"] or not control["source_valid"]:
                raise ValueError("公共处理尚未获得稳定源帧")
            value.update(
                image_processing=IDENTITY,
                processing_config=control["effective"],
                processing_requested_config=control["requested"],
                processing_revision=control["effective_revision"],
            )
        elif c.get("image_processing") == "shared_processed_rgb_v1":
            value.update(
                image_processing=c["image_processing"],
                processing_config=c["processing_status"],
            )
        rect = state.get("rect", {})
        if rect.get("web_capture_protocol") == "native-rectangle-v3":
            if rect.get("web_capture_source") != "rk_raw_preview_native_crop":
                raise ValueError("Web原生采集来源不匹配")
            value["acquisition"] = dict(
                protocol="native-rectangle-v3",
                capture_source=rect["web_capture_source"],
                source_channel=0,
                source_size=[1920, 1080],
            )
        value["digest"] = sha(
            json.dumps(
                {k: v for k, v in value.items() if k != "identity"}, sort_keys=True
            ).encode()
        )
        return value

    def ensure_profile(self, profile):
        state = self.status()
        current = self.profile(state)
        if not profile or current["digest"] != profile["digest"]:
            raise ValueError(
                "实际成像参数已改变；请回第一步重新冻结并开始新采集批次。旧图像不会删除"
            )
        if profile.get("processing_identity"):
            processing = state["camera"].get("processing_status", {})
            effective = dict(
                processing,
                enhance_revision=state["camera"]
                .get("automatic", {})
                .get("enhancement", {})
                .get("effective_revision"),
            )
            if any(
                effective.get(k) != v for k, v in profile["processing_identity"].items()
            ):
                raise ValueError("公共图像处理身份已改变，请回第一步重新固定参数")
        return current

    def capture(self, rect, count, dest, job):
        rect = rectangle(rect)
        result, remote = self.transaction(
            "rect_control",
            dict(operation="capture", rectangle=rect, frames=count, hz=2),
            job,
        )
        protocol = result.get("protocol")
        if (
            protocol
            not in ("native-rectangle-v1", "native-rectangle-v2", "native-rectangle-v3")
            or result.get("rectangle") != rect
        ):
            raise ValueError("采集接口或选区返回不匹配")
        if protocol == "native-rectangle-v2" and (
            result.get("processing") not in ("shared_processed_rgb_v1", IDENTITY)
            or result.get("capture_channel") != 2
        ):
            raise ValueError("采集图像未来自公共处理后的独立采集通道")
        if protocol == "native-rectangle-v3" and (
            result.get("processing") != IDENTITY
            or result.get("capture_source") != "rk_raw_preview_native_crop"
            or type(result.get("source_channel")) is not int
            or result["source_channel"] != 0
            or result.get("source_size") != [1920, 1080]
        ):
            raise ValueError("采集图像未来自完整RAW预览的原生像素裁剪")
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        frames = []
        seen = set()
        processing = None
        for i, f in enumerate(result.get("frames", [])):
            if (
                f.get("file") != "native_" + str(i) + ".rgb"
                or f.get("rectangle") != rect
                or type(f.get("source_frame_id")) is not int
                or f["source_frame_id"] in seen
            ):
                raise ValueError("采集文件名、区域或源帧无效")
            if protocol in ("native-rectangle-v2", "native-rectangle-v3"):
                if (
                    any(
                        type(f.get(k)) is not int or not 0 <= f[k] <= 0xFFFFFFFF
                        for k in (
                            "fpga_epoch",
                            "processing_revision",
                            "enhance_revision",
                        )
                    )
                    or not f["fpga_epoch"]
                ):
                    raise ValueError("采集通道或逐帧处理身份无效")
                if protocol == "native-rectangle-v2":
                    if (
                        f.get("channel") != 2
                        or type(f.get("request_id")) is not int
                        or not 0 < f["request_id"] <= 0xFFFFFFFF
                    ):
                        raise ValueError("独立采集通道或请求编号无效")
                else:
                    if (
                        any(
                            type(f.get(k)) is not int or f[k] != 0
                            for k in ("channel", "source_channel")
                        )
                        or type(f.get("source_request_id")) is not int
                        or not 0 <= f["source_request_id"] <= 0xFFFFFFFF
                    ):
                        raise ValueError("原生RAW源通道或源描述符编号无效")
                    if (
                        f.get("capture_transaction_id") != result["id"]
                        or f.get("capture_source") != result["capture_source"]
                        or f.get("source_size") != [1920, 1080]
                        or f.get("origin") != rect[:2]
                    ):
                        raise ValueError("Web采集事务、来源或原生裁剪几何不匹配")
                    if any(
                        type(f.get(k)) not in (int, float)
                        or not math.isfinite(f[k])
                        or f[k] <= 0
                        for k in (
                            "capture_received_monotonic",
                            "transport_dequeued_monotonic",
                            "python_copy_returned_monotonic",
                        )
                    ):
                        raise ValueError("原始源帧采集时间缺失或无效")
                    f = dict(
                        f,
                        capture_source=result["capture_source"],
                        capture_protocol=protocol,
                    )
                if result.get("processing") == IDENTITY and (
                    not f.get("processing_config")
                    or f.get("image_mode") != f["processing_config"].get("image_mode")
                ):
                    raise ValueError("逐帧处理模式缺失或不匹配")
                effective = tuple(
                    f[k]
                    for k in ("fpga_epoch", "processing_revision", "enhance_revision")
                )
                if processing is not None and effective != processing:
                    raise ValueError("采集期间实际图像处理版本变化")
                processing = effective
            raw = self.bytes(remote + "/" + f["file"])
            if len(raw) != rect[2] * rect[3] * 3 or sha(raw) != f["sha256"]:
                raise ValueError("采集图像完整性校验失败")
            (dest / f["file"]).write_bytes(raw)
            seen.add(f["source_frame_id"])
            frames.append(f)
        write(dest / "transaction.json", result)
        return frames, result

    def configure(self, values, job):
        rectangle(values["rectangle"])
        runtime = self.fresh("runtime_status.json")
        size = (
            runtime.get("inference", {})
            .get("async_inference", {})
            .get("model_input_size")
        )
        if (
            not isinstance(size, list)
            or len(size) != 2
            or any(type(n) is not int or n <= 0 for n in size)
        ):
            raise ValueError("尚未回读到当前模型输入尺寸，未修改现场参数")
        if values["rectangle"][2:] != size:
            raise ValueError(
                "ROI宽高固定为当前模型输入 "
                + str(size[0])
                + "×"
                + str(size[1])
                + "；可调整位置、置信度和姿态确认次数"
            )
        from native_windows import plan

        plan(values["rectangle"])
        confidence = float(values["confidence_percent"])
        hits = int(values["track_hits"])
        if not 0 < confidence <= 100 or not 1 <= hits <= 30:
            raise ValueError("置信度或追踪次数超出范围")
        return self.transaction(
            "rect_control", dict(operation="configure", values=values), job
        )[0]

    def screen(self):

        environment = dict(
            item.decode().split("=", 1)
            for item in self.bytes("/proc/" + str(self.pid) + "/environ").split(b"\0")
            if item.startswith((b"DISPLAY=", b"XAUTHORITY="))
        )
        xenv = "DISPLAY=" + shlex.quote(environment.get("DISPLAY", ":0"))
        if environment.get("XAUTHORITY"):
            xenv += " XAUTHORITY=" + shlex.quote(environment["XAUTHORITY"])
        listing = self.command(xenv + " xrandr --current").decode()
        rows = re.findall(
            r"^(\S+) connected(?: primary)? (\d+)x(\d+)\+(\d+)\+(\d+)", listing, re.M
        )
        item = next((r for r in rows if r[0].startswith("HDMI")), None)
        if item is None:
            raise ValueError("没有启用的HDMI显示端")
        monitor, w, h, x, y = item
        w, h, x, y = map(int, (w, h, x, y))
        raw = self.command(xenv + " xwd -root -silent", timeout=8)
        if len(raw) < 100:
            raise ValueError("HDMI快照为空")
        head = struct.unpack(">25I", raw[:100])
        header, version = head[:2]
        if version != 7 or head[2] != 2 or head[11] not in (24, 32):
            raise ValueError("不支持的HDMI像素存储格式")
        width, height = head[4:6]
        stride = head[12]
        bpp = head[11] // 8
        offset = header + head[19] * 12
        pixels = (
            np.frombuffer(raw, np.uint8, offset=offset, count=stride * height)
            .reshape(height, stride)[:, : width * bpp]
            .reshape(height, width, bpp)
        )
        packed = np.zeros((height, width), dtype=np.uint32)
        for k in range(bpp):
            packed |= pixels[:, :, k].astype(np.uint32) << (
                8 * (k if head[7] == 0 else bpp - 1 - k)
            )
        channels = []
        for mask in head[14:17]:
            shift = (mask & -mask).bit_length() - 1
            if (mask >> shift) != 255:
                raise ValueError("HDMI不是8位RGB，不自动转换色深")
            channels.append(((packed & mask) >> shift).astype(np.uint8))
        image = Image.fromarray(np.stack(channels, axis=-1)).crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        image.save(buf, format="BMP")
        perf = self.read("screen_performance_" + monitor + ".json")
        wnd = (perf.get("windows") or [{}])[0]
        area = wnd.get("camera_area")
        if area:
            area = [
                area[0] + wnd.get("x", x) - x,
                area[1] + wnd.get("y", y) - y,
                area[2],
                area[3],
            ]
        meta = dict(
            monitor=monitor,
            size=[w, h],
            camera_area=area,
            sensor_size=[1920, 1080],
            source_frame_id=perf.get("native", {}).get("last_completed_source"),
            source_id_note="最近显示遥测；X11截图未与源帧原子锁定",
            kind="hdmi_scanout",
            pixel_policy="uncompressed_bmp",
        )
        return buf.getvalue(), meta
