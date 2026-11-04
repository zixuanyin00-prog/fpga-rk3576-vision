from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import numpy as np
from PIL import Image
from core import store as db
from core import capture as camera_capture
from core.proposals import supplemental
from core import box_assistant
from core import batch15
from core import feedback
from core import changes
from core import scene_boxes
from core.gpu import worker_python

DEFAULTS = dict(
    host="192.0.2.1",
    username="root",
    key=str(Path.home() / ".ssh/vision_ed25519"),
    remote="/root/free_rect_20261025/runtime",
    frames=15,
    hz=2,
    stride=1,
    steps=600,
    auto_capture=False,
    numbers=[],
)


class Service:
    def __init__(self):
        self.lock = threading.RLock()
        self.job = None
        self.process = None
        self.cancelled = False
        self.settings = (
            db.read(db.ROOT / "settings.json")
            if (db.ROOT / "settings.json").exists()
            else DEFAULTS.copy()
        )
        batch15.settings(self.settings["frames"], self.settings["stride"])
        self.activations = set()
        self.box_selection = None

    def busy(self):
        return self.job is not None and self.job["state"] == "running"

    def status(self):
        with self.lock:
            job = dict(self.job) if self.job else None
            if job and job.get("progress_file") and Path(job["progress_file"]).exists():
                job["progress"] = db.read(job["progress_file"])
            candidates = scene_boxes.candidates()
            selected = (
                self.box_selection
                if self.box_selection
                and any(c["run"] == self.box_selection["run"] for c in candidates)
                else None
            )
            return dict(
                projects=db.listing(),
                job=job,
                settings=self.settings,
                box_assistant=dict(
                    ready=bool(candidates),
                    workflow_generation="scene-native-v3",
                    selected=selected,
                    candidates=candidates,
                ),
                compute=dict(required_device="cuda:0", cpu_fallback=False),
            )

    def launch(self, kind, id, action):
        if self.busy():
            raise ValueError("已有任务正在执行，请等待或停止")
        self.cancelled = False
        self.job = dict(
            id=db.uid(),
            kind=kind,
            project=id,
            state="running",
            message="准备中",
            started=db.now(),
        )

        def work():
            try:
                action()
                with self.lock:
                    self.job.update(
                        state="cancelled" if self.cancelled else "complete",
                        message=(
                            "已停止"
                            if self.cancelled
                            else self.job.get("capture_warning", "完成")
                        ),
                        finished=db.now(),
                    )
            except Exception as e:
                with self.lock:
                    self.job.update(
                        state="cancelled" if self.cancelled else "error",
                        message=str(e),
                        finished=db.now(),
                    )
                log = db.ROOT / "logs"
                log.mkdir(exist_ok=True)
                (log / (self.job["id"] + ".txt")).write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
            finally:
                self.process = None
                changes.publish()

        threading.Thread(target=work, daemon=True).start()
        return self.job

    def message(self, text):
        with self.lock:
            self.job["message"] = text

    def check_cancel(self):
        if self.cancelled:
            raise ValueError(
                "任务已停止；已保存的数据保留。板端已提交的有界采样可能继续到本批结束。"
            )

    def stop(self):
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def subprocess(self, mode, path, script="worker.py", python=None, on_progress=None):
        self.check_cancel()
        log = Path(path).parent / (mode + ".log")
        if script in (
            "worker.py",
            "box_worker.py",
            "foundation_worker.py",
            "pc_test_worker.py",
            "scene_template_worker.py",
        ):
            python = worker_python()
        with log.open("w", encoding="utf-8") as stream:
            self.process = subprocess.Popen(
                [
                    python or sys.executable,
                    "-u",
                    str(db.ROOT / script),
                    mode,
                    str(path),
                ],
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=db.ROOT,
            )
            if on_progress:
                while self.process.poll() is None:
                    on_progress()
                    time.sleep(0.4)
                on_progress()
                result = self.process.returncode
            else:
                result = self.process.wait()
        self.check_cancel()
        if result:
            raise RuntimeError(
                "任务失败：" + log.read_text(encoding="utf-8", errors="replace")[-1600:]
            )

    def new(self, args):
        with self.lock:
            p = db.create(
                args.get("name", "新环境"),
                args.get("numbers", []),
                args.get("pose_count", 3),
            )
            p["target_prompt"] = str(
                args.get("target_prompt") or "an industrial workpiece."
            ).strip()[:300]
            db.save(p)
            self.box_selection = None
            self.settings["numbers"] = p["schema"]["numbers"]
            db.write(db.ROOT / "settings.json", self.settings)
            return p

    def startup(self, enabled, activation="default"):
        with self.lock:
            if activation in self.activations:
                return
            self.activations.add(activation)
            if enabled and self.settings["auto_capture"]:
                if self.busy():
                    return self.job["project"]
                p = self.new(
                    dict(name="环境 " + db.now()[:19], numbers=self.settings["numbers"])
                )
                self.capture(p["id"], "train")
                return p["id"]

    def configure(self, args):
        with self.lock:
            if self.busy():
                raise ValueError("请等待当前任务结束再修改设置")
            s = {**self.settings, **{k: v for k, v in args.items() if k in DEFAULTS}}
            s["frames"], s["stride"] = batch15.settings(s["frames"], s["stride"])
            s["hz"] = float(s["hz"])
            s["steps"] = int(s["steps"])
            if not (
                1 <= s["frames"] <= 240
                and 1 <= s["stride"] <= 30
                and 0.2 <= s["hz"] <= 2
                and 50 <= s["steps"] <= 3000
            ):
                raise ValueError("有效目标15张、逐张标注、频率0.2–2Hz、训练50–3000步")
            s["numbers"] = db.schema(s["numbers"])["numbers"]
            self.settings = s
            db.write(db.ROOT / "settings.json", s)

    def import_folder(self, id, path, role):
        self.validate_role(role)
        with self.lock:
            self.launch("import", id, lambda: self.import_action(id, Path(path), role))

    def validate_role(self, role):
        if role not in ("train", "validation", "test"):
            raise ValueError("用途必须为训练、开发验证或独立验收")

    def import_action(self, id, source, role):
        p = db.load(id)
        root = db.project_path(id)
        batch = db.uid()
        dest = root / "captures" / batch
        dest.mkdir(parents=True)
        paths = (sorted(source.glob("*.rgb")) or sorted(source.glob("*.png")))[:240]
        if not paths:
            raise ValueError("目录中没有原生.rgb或.png图像")
        items = []
        for i, path in enumerate(paths[:: self.settings["stride"]]):
            self.check_cancel()
            if path.suffix == ".rgb":
                raw = path.read_bytes()
            else:
                im = Image.open(path).convert("RGB")
                if im.size != (512, 288):
                    raise ValueError("仅接受512×288原生ROI；不能放大缩略图作为训练原图")
                raw = im.tobytes()
            if len(raw) != 512 * 288 * 3:
                raise ValueError("原生ROI尺寸不匹配：" + path.name)
            target = dest / f"{i:04}.rgb"
            target.write_bytes(raw)
            items.append(
                dict(
                    id=batch + "_" + str(i),
                    batch=batch,
                    source_group=str(source.resolve()).casefold(),
                    role=role,
                    file=str(target.relative_to(root)),
                    sha256=hashlib.sha256(raw).hexdigest(),
                    objects=[],
                    reviewed=False,
                    independent_test_ineligible=True,
                    independence_reason="import_without_verifiable_source_lineage",
                    source_frame_id=None,
                    imported_from=str(path.resolve()),
                )
            )
        with self.lock:
            p = db.load(id)
            db.add_frames(p, items)
        db.write(
            dest / "provenance.json",
            dict(
                source=str(source),
                role=role,
                imported=db.now(),
                fresh_board_capture=False,
            ),
        )

        if (
            items
            and role == "train"
            and self.box_selection
            and self.box_selection.get("project") == id
        ):
            self.suggest_action(id, items, dest / "boxes")
        return items

    def connect(self):
        import paramiko

        s = self.settings
        c = paramiko.SSHClient()
        c.load_system_host_keys()
        c.connect(
            s["host"],
            username=s["username"],
            key_filename=s["key"],
            timeout=8,
            auth_timeout=8,
            banner_timeout=8,
            allow_agent=False,
            look_for_keys=False,
        )
        return c

    def capture(self, id, role, batch=None):
        self.validate_role(role)
        p = db.load(id)
        if not self.calibrated(p):
            return self.probe(id)
        if not p.get("environment", {}).get("approved"):
            raise ValueError("请先确认当前固定成像配置，再采集15张新图")
        with self.lock:
            self.launch("capture", id, lambda: self.capture_action(id, role, batch))
            self.job["capture_role"] = role

    def calibrated(self, p):
        cal = p.get("calibration", {})
        return (
            cal.get("confirmed")
            and cal.get("host") == self.settings["host"]
            and cal.get("remote") == self.settings["remote"].rstrip("/")
        )

    def probe(self, id, point=None, rectangle=None):
        if rectangle is not None:
            from core.rectangles import rectangle as validate_rectangle
            from core.rect_capture import probe

            rectangle = validate_rectangle(rectangle)
            with self.lock:
                return self.launch("probe", id, lambda: probe(self, id, rectangle))
        if point is not None:
            point = camera_capture.origin(point)
        with self.lock:
            self.launch("probe", id, lambda: self.probe_action(id, point))

    def probe_action(self, id, point):
        self.message("读取全景定位图与3张原生试拍；尚未开始批量采集")
        c = self.connect()
        s = c.open_sftp()
        remote = self.settings["remote"].rstrip("/")
        root = db.project_path(id)
        try:
            before = json.loads(s.open(remote + "/runtime_status.json").read())
            p = db.load(id)
            point = (
                point
                if point is not None
                else p.get("calibration", {}).get(
                    "origin", before["inference"]["native_roi"]["origin"]
                )
            )
            dest = root / "probes" / db.uid()
            result = camera_capture.probe(
                s, remote, dest, point, "probe", self.check_cancel
            )
            result.update(
                folder=str(dest.relative_to(root)),
                host=self.settings["host"],
                remote=remote,
                pid=before["pid"],
            )
            boot = s.open("/proc/sys/kernel/random/boot_id").read().decode().strip()
            result["boot_id"] = boot
            try:
                profile = json.loads(
                    s.open(remote + "/training_camera_profile.json").read()
                )
            except FileNotFoundError:
                profile = {}
            result["exposure_record"] = dict(
                same_boot=profile.get("boot_id") == boot,
                requested_exposure_ms=profile.get("requested_exposure_ms"),
                sensor_register_readback=False,
                note="SRAM only. Power loss clears this configuration; same RK boot is a continuity check, not sensor register readback.",
            )
            with self.lock:
                p = db.load(id)
                p["probe"] = result

                p.pop("calibration", None)
                db.save(p)
        finally:
            s.close()
            c.close()

    def confirm_region(self, id, probe_id):
        with self.lock:
            if self.busy():
                raise ValueError("请等待试拍结束")
            p = db.load(id)
            trial = p.get("probe", {})
            if trial.get("id") != probe_id or not trial.get("ok"):
                raise ValueError("试拍已更新，请检查当前原图后确认")
            if trial["host"] != self.settings["host"] or trial[
                "remote"
            ] != self.settings["remote"].rstrip("/"):
                raise ValueError("连接已改变，请重新试拍")
            p["calibration"] = dict(
                origin=(
                    trial["origin"]
                    if trial.get("rectangle")
                    else camera_capture.origin(trial["origin"])
                ),
                size=trial.get("size", [512, 288]),
                confirmed=True,
                confirmed_at=db.now(),
                probe_id=probe_id,
                host=trial["host"],
                remote=trial["remote"],
                boot_id=trial.get("boot_id"),
                focus_confirmation="user_selected_native_region",
            )
            if trial.get("rectangle"):
                p["calibration"]["rectangle"] = trial["rectangle"]
            db.save(p)

    def approve_environment(self, id, confirmed, internal=False):
        if confirmed is not True:
            raise ValueError("请明确确认使用当前固定成像配置")
        if self.busy() and not internal:
            raise ValueError("请等待当前任务结束")
        p = db.load(id)
        if not self.calibrated(p):
            raise ValueError("请先确认原生采样区域与清晰度")
        c = self.connect()
        s = c.open_sftp()
        remote = self.settings["remote"].rstrip("/")
        try:
            from core.environment15 import approved_profile

            runtime = json.loads(s.open(remote + "/runtime_status.json").read())
            status = json.loads(s.open(remote + "/camera_console/status.json").read())
            now = float(s.open("/proc/uptime").read().split()[0])
            if p["calibration"].get("rectangle"):
                profile = approved_profile(
                    status, runtime, now, status["identity"]["origin"]
                )
                profile["training_rectangle"] = p["calibration"]["rectangle"]
                profile["approved_at_beijing"] = batch15.beijing_now()
                p.setdefault("environment", {})["approved"] = profile
                db.save(p)
                return dict(profile_digest=profile["profile_digest"], approved=True)

            approved_profile(status, runtime, now, status["identity"]["origin"])
            desired = p["calibration"]["origin"]
            if desired != runtime["environment_identity"]["origin"]:
                camera_capture.probe(
                    s,
                    remote,
                    db.project_path(id) / "profiles" / db.uid(),
                    desired,
                    "region",
                    self.check_cancel,
                )
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    runtime = json.loads(s.open(remote + "/runtime_status.json").read())
                    status = json.loads(
                        s.open(remote + "/camera_console/status.json").read()
                    )
                    if (
                        runtime.get("environment_identity", {}).get("origin") == desired
                        and status["identity"].get("origin") == desired
                    ):
                        break
                    time.sleep(0.1)
                now = float(s.open("/proc/uptime").read().split()[0])
            profile = approved_profile(status, runtime, now, p["calibration"]["origin"])
            profile["approved_at_beijing"] = batch15.beijing_now()
            p.setdefault("environment", {})["approved"] = profile
            db.save(p)
            return dict(profile_digest=profile["profile_digest"], approved=True)
        finally:
            s.close()
            c.close()

    def capture_action(self, id, role, selected_batch=None):
        p = db.load(id)
        if not self.calibrated(p):
            raise ValueError(
                "请先选择采样区域，检查原生试拍，并确认完整工件和文字清晰度"
            )
        if p.get("calibration", {}).get("rectangle"):
            from core.rect_capture import capture

            return capture(self, id, role, selected_batch)
        point = camera_capture.origin(p["calibration"]["origin"])
        self.message("连接开发板并检查原生ROI；请保持送料和焦距稳定")
        try:
            c = self.connect()
        except Exception as e:
            raise RuntimeError(
                "无法连接开发板。请检查供电、网线和SSH设置，已有标注不会丢失。 "
                + str(e)
            ) from e
        s = c.open_sftp()
        settings = self.settings.copy()
        remote = settings["remote"].rstrip("/")
        transaction = "vt_" + db.uid()
        batch = selected_batch or transaction
        root = db.project_path(id)
        if selected_batch:
            if role != "train" or selected_batch not in {
                f.get("batch") for f in p["frames"]
            }:
                raise ValueError("补采必须显式选择已有训练批次")
            prior = [
                f
                for f in p["frames"]
                if f.get("batch") == batch and f.get("role") == "train"
            ]
            if any(
                f.get("quality_review") == "pending" or not f.get("reviewed")
                for f in prior
                if not f.get("excluded")
            ):
                raise ValueError("请先逐张完成已有图片质量复核，再按缺额补采")
            accepted = batch15.accepted_frames(prior, p["schema"]["labels"])
            target = 15 - len(accepted)
            if target <= 0:
                raise ValueError("本批已足15张有效原图，不扩大批次")
        else:
            prior = []
            target = 10 if role == "test" else 15
        dest = root / "captures" / batch / transaction
        dest.mkdir(parents=True)
        try:
            try:
                protocol = json.loads(
                    s.open(remote + "/dynamic_audit/protocol.json").read()
                )
            except FileNotFoundError:
                raise ValueError(
                    "需先授权安装配套v15采样后端；旧written协议不支持有效张数停止"
                )
            if protocol.get("protocol") != "native-effective15-v1":
                raise ValueError("采样协议不匹配")
            try:
                active = json.loads(
                    s.open(remote + "/dynamic_audit/active.json").read()
                )
            except FileNotFoundError:
                active = {}
            if active.get("state") == "running" or active.get("pending", 0) > 0:
                raise ValueError("其他采样事务仍在执行；不覆盖")
            before = json.loads(s.open(remote + "/runtime_status.json").read())
            current_boot = (
                s.open("/proc/sys/kernel/random/boot_id").read().decode().strip()
            )
            imaging_identity = (
                p.get("environment", {}).get("approved", {}).get("profile_digest")
            )
            if p["calibration"].get("boot_id") != current_boot:
                with self.lock:
                    latest = db.load(id)
                    latest.pop("calibration", None)
                    if latest.get("probe", {}).get("exposure_record"):
                        latest["probe"]["exposure_record"]["same_boot"] = False
                    db.save(latest)
                raise ValueError(
                    "开发板启动已改变或缺少启动记录，请点“读取全景 / 更新试拍”，重新检查运动清晰度后采集"
                )
            camera_capture.probe(
                s, remote, dest / "region_check", point, "region", self.check_cancel
            )
            from core.environment15 import capture_admission

            before = json.loads(s.open(remote + "/runtime_status.json").read())
            scene_spec = capture_admission(p, before, s, remote, point)
            if prior and any(
                f.get("boot_id") != current_boot
                or f.get("capture_session") != p["calibration"]["probe_id"]
                or f.get("imaging_identity") != imaging_identity
                or f.get("origin") != point
                or f.get("scene_spec") != scene_spec
                for f in prior
            ):
                raise ValueError("补采场景身份与原批次不同，请新建场景批次")
            telemetry_before = camera_capture.fps_point(s, remote)
            db.write(dest / "display_before.json", telemetry_before)
            db.write(dest / "before.json", before)
            try:
                s.stat(remote + "/dynamic_audit/request.json")
            except FileNotFoundError:
                pass
            else:
                raise ValueError("开发板已有等待采集请求，请稍后重试")
            request = dict(
                id=transaction,
                protocol="native-effective15-v1",
                frames=target,
                max_attempts=min(45, target * 3),
                exclude_source_frame_ids=[f["source_frame_id"] for f in prior],
                exclude_image_sha256=[f["sha256"] for f in prior],
                hz=settings["hz"],
                speed_setting=1.0,
                native_roi=point + [512, 288],
                role=(
                    "dynamic_training"
                    if role == "train"
                    else "held_out_dynamic_validation"
                ),
                no_training=role != "train",
                no_pixel_transform=True,
            )
            request.update(scene_spec)
            temp = remote + "/dynamic_audit/" + transaction + ".tmp"
            with s.open(temp, "w") as f:
                f.write(json.dumps(request))
            s.rename(temp, remote + "/dynamic_audit/request.json")
            deadline = time.monotonic() + request["max_attempts"] / request["hz"] + 60
            while time.monotonic() < deadline:
                self.check_cancel()
                try:
                    status = json.loads(
                        s.open(
                            remote + "/dynamic_audit/" + transaction + "/status.json"
                        ).read()
                    )
                except FileNotFoundError:
                    status = {"written": 0}
                self.message(
                    f"本事务原生准入 {status['written']}/{target}；尝试{status.get('attempts',0)}/{request['max_attempts']}，仍需人工质量复核"
                )
                if status.get("state") in ("complete", "exhausted", "cancelled"):
                    break
                time.sleep(1)
            else:
                raise ValueError("采样超时；请检查相机、原生ROI推理是否运行")
            db.write(dest / "capture_status.json", status)
            items = []
            seen = {f["source_frame_id"] for f in prior}
            if status.get("protocol") != "native-effective15-v1":
                raise ValueError("返回采样协议不匹配")
            for i, stem in enumerate(status.get("files", [])):
                if not stem.isdigit():
                    raise ValueError("无效原图文件名")
                self.check_cancel()
                base = f"{remote}/dynamic_audit/{transaction}/{stem}"
                for ext in ("rgb", "json"):
                    s.get(base + "." + ext, str(dest / f"{i:04}.{ext}"))
                meta = db.read(dest / f"{i:04}.json")
                raw = (dest / f"{i:04}.rgb").read_bytes()
                if (
                    len(raw) != 512 * 288 * 3
                    or meta["origin"] != point
                    or meta["source_frame_id"] in seen
                ):
                    raise ValueError("发现错误尺寸、重复源帧或采集区域变化")
                seen.add(meta["source_frame_id"])
                if i % settings["stride"] == 0:
                    items.append(
                        dict(
                            id=transaction + "_" + str(i),
                            batch=batch,
                            source_group=settings["host"] + "/" + batch,
                            role=role,
                            file=str((dest / f"{i:04}.rgb").relative_to(root)),
                            sha256=hashlib.sha256(raw).hexdigest(),
                            source_frame_id=meta["source_frame_id"],
                            origin=point,
                            native_roi=True,
                            boot_id=current_boot,
                            capture_session=p["calibration"]["probe_id"],
                            imaging_identity=imaging_identity,
                            scene_spec=scene_spec,
                            calibration=p["calibration"],
                            quality_review="pending",
                            all_objects_reviewed=False,
                            objects=[],
                            reviewed=False,
                        )
                    )
            after = json.loads(s.open(remote + "/runtime_status.json").read())
            db.write(dest / "after.json", after)
            if capture_admission(p, after, s, remote, point) != scene_spec:
                raise ValueError("采样期间环境代际已变化，本批保留诊断，禁止训练")
            if after["pid"] != before["pid"]:
                raise ValueError("采集中服务重启，请重新采集；本批原始数据已保留")
            telemetry_after = camera_capture.fps_point(s, remote)
            db.write(dest / "display_after.json", telemetry_after)
            fps = camera_capture.fps_summary(telemetry_before, telemetry_after)
            db.write(
                dest / "capture_evidence.json",
                dict(
                    source_frames=len(items),
                    attempts=status.get("attempts"),
                    target=target,
                    accepted_originals_pending_manual_review=len(items),
                    origin=point,
                    size=[512, 288],
                    native_roi=True,
                    calibration=p["calibration"],
                    drops=status.get("dropped"),
                    display_60fps_verified=fps["verified"],
                    display=fps,
                ),
            )
            with self.lock:
                latest = db.load(id)
                latest["last_capture"] = dict(
                    batch=batch,
                    created=db.now(),
                    frames=len(items),
                    source_frames=len(items),
                    display=fps,
                )
                latest.setdefault("capture_profiles", {})[batch] = p["environment"][
                    "approved"
                ]
                db.save(latest)
            with self.lock:
                p = db.load(id)
                db.add_frames(p, items)
        finally:
            if self.cancelled:
                try:
                    with s.open(
                        remote + "/dynamic_audit/cancel_" + transaction + ".json", "w"
                    ) as marker:
                        marker.write("{}")
                except Exception:
                    pass
            s.close()
            c.close()

        if (
            items
            and role == "train"
            and self.box_selection
            and self.box_selection.get("project") == id
            and self.box_selection.get("scene", {}).get("batch") == batch
        ):
            self.suggest_action(id, items, dest / "boxes")
        return items

    def select_box(self, args):
        project = args.get("project")
        if not project:
            raise ValueError("请先选择本次场景项目")
        db.project_path(project)
        if args.get("provider") == "huggingface":
            raise ValueError("旧画框模型已从03移除，请使用本场景新画框助手")
        import re

        run = args.get("run", "")
        if not re.fullmatch(r"[a-zA-Z0-9_]+", run):
            raise ValueError("无效画框模型版本")
        model = box_assistant.HOME / "runs" / run / "boxes.pt"
        meta = db.read(model.parent / "result.json")
        if meta.get("workflow_generation") != "scene-native-v3" or not meta.get(
            "scene"
        ):
            raise ValueError("旧画框模型已从03移除，请使用本场景新画框助手")
        if meta.get("scene"):
            scene = scene_boxes.scene_identity(db.load(project), args.get("batch"))
            if scene != meta["scene"]:
                raise ValueError("画框助手仅适用于其训练项目、采样批次与成像配置")
        if args.get("trained_at") != meta.get("created") or args.get(
            "sha256"
        ) != meta.get("sha256"):
            raise ValueError("请选择实际训练时间与哈希一致的画框模型")
        if hashlib.sha256(model.read_bytes()).hexdigest() != meta["sha256"]:
            raise ValueError("画框模型文件哈希不符")
        if self.busy():
            raise ValueError("请等待当前任务完成再选择画框模型")
        self.box_selection = dict(
            project=project,
            run=run,
            trained_at=meta["created"],
            sha256=meta["sha256"],
            architecture=meta["architecture"],
            selected_at=db.now(),
        )
        if meta.get("scene"):
            self.box_selection["scene"] = meta["scene"]
        return self.box_selection

    def run_suggestions(self, id, frames, dest):
        selected = dict(self.box_selection or {})
        if not selected or selected.get("project") != id:
            raise ValueError("请先为当前场景明确选择本次使用的画框模型训练时间")
        if selected.get("scene"):
            if (
                scene_boxes.scene_identity(db.load(id), selected["scene"]["batch"])
                != selected["scene"]
            ):
                raise ValueError("场景已变化，请重新训练和选型")
            if any(f.get("batch") != selected["scene"]["batch"] for f in frames):
                raise ValueError("此助手仅补齐所选场景批次，不能自动应用到其他批次")
        if selected.get("provider") == "huggingface":
            raise ValueError("旧画框模型已从03移除")
        model = box_assistant.HOME / "runs" / selected["run"] / "boxes.pt"
        if hashlib.sha256(model.read_bytes()).hexdigest() != selected["sha256"]:
            raise ValueError("已选画框模型发生变化，请重新选型")
        self.message("新场景画框助手正在原生裁剪图上生成元件小框")
        job = dict(
            root=str(db.project_path(id)),
            output=str(dest),
            model=str(model),
            frames=frames,
            selected_model_sha256=selected["sha256"],
            selected_trained_at=selected["trained_at"],
            selection=selected,
            annotation_assistance_only=True,
            recognition_model_used=False,
        )
        p = db.load(id)
        job["prompt"] = p.get("target_prompt") or "an industrial workpiece."
        from core.native_frames import load_native

        for frame in frames:
            load_native(db.project_path(id), frame)
        job["helper_input"] = "native_1080p_source_crop"
        job["annotation_coordinate_size"] = [512, 288]
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "suggest_job.json"
        db.write(path, job)
        self.subprocess("suggest", path, "box_worker.py")
        return db.read(dest / "proposals.json")

    def publish_proposals(self, id, proposed):
        with self.lock:
            p = db.load(id)
            changed = False
            for f in p["frames"]:
                if (
                    f["id"] in proposed
                    and not f.get("reviewed")
                    and not f.get("annotated_at")
                    and f.get("proposals") != proposed[f["id"]]
                ):
                    f.setdefault(
                        "proposal_baseline", json.loads(json.dumps(proposed[f["id"]]))
                    )
                    f["proposals"] = proposed[f["id"]]
                    f["proposal_model_selection"] = dict(self.box_selection)
                    changed = True
            if changed:
                db.save(p)

    def test_model(self, id, run, trained_at):
        from core.pc_testing import start_test

        return start_test(self, id, run, trained_at)

    def train_box(self, id, batch=None):
        p = db.load(id)
        scene, frames = scene_boxes.snapshot(p, batch)
        with self.lock:
            return self.launch(
                "box_train", id, lambda: self.train_box_action(id, scene, frames)
            )

    def train_box_action(self, id=None, scene=None, frames=None):
        if not scene:
            raise ValueError("不训练或应用历史画框助手；请明确选择当前场景批次")
        run = db.uid()
        out = box_assistant.HOME / "runs" / run
        path = out / "train_job.json"
        job = dict(
            root=str(db.project_path(id)),
            output=str(out),
            scene=scene,
            frames=frames,
            seed=20261025,
            steps=1600,
            verifier_steps=500,
            helper_input="native_1080p_source_crop",
            initialization="random",
            recognition_model_used=False,
        )
        db.write(path, job)
        self.job["progress_file"] = str(out / "progress.json")
        self.message("仅用本批已确认小框，从随机权重训练场景助手；训练后等待选型")
        self.subprocess("train", path, "box_worker.py")
        self.job["candidate"] = dict(run=run, **db.read(out / "result.json"))

    def point_box(self, args):
        from core.native_frames import load_native
        from core.point_box import propose

        p = db.load(args["project"])
        f = db.frame(p, args["frame"])
        if f.get("excluded"):
            raise ValueError("隔离图片不可补框")
        if args.get("expected_revision") != f.get("annotation_revision", 0):
            raise ValueError("标注已更新，请刷新后点击补框")
        image, source = load_native(db.project_path(p["id"]), f)
        return dict(
            proposal=propose(image, args["point"], f.get("objects", [])), source=source
        )

    def assist_remaining(self, id, batch):
        p = db.load(id)
        scene_boxes.scene_identity(p, batch)
        frames = [
            f
            for f in p["frames"]
            if f.get("batch") == batch
            and f["role"] == "train"
            and not f.get("excluded")
            and not f.get("reviewed")
        ]
        if not frames:
            raise ValueError("本批没有待补齐图片")

        def action():
            total = 0
            for f in frames:
                self.check_cancel()
                self.assist_action(id, f["id"])
                total += self.job.get("added_boxes", 0)
            self.job["added_boxes"] = total

        with self.lock:
            return self.launch("assist", id, action)

    def suggest_action(self, id, frames, dest):
        proposed = self.run_suggestions(id, frames, dest)
        with self.lock:
            p = db.load(id)
            for f in p["frames"]:
                if (
                    f["id"] in proposed
                    and not f.get("reviewed")
                    and not f.get("annotated_at")
                ):
                    f.setdefault(
                        "proposal_baseline",
                        json.loads(json.dumps(f.get("proposals", proposed[f["id"]]))),
                    )
                    f["proposals"] = proposed[f["id"]]
                    f["proposal_source"] = self.box_selection["architecture"]
                    f["proposal_model_selection"] = dict(self.box_selection)
            db.save(p)

    def assist(self, id, frame_id):
        p = db.load(id)
        f = db.frame(p, frame_id)
        if f.get("excluded"):
            raise ValueError("已隔离图片不生成候选框")
        with self.lock:
            self.launch("assist", id, lambda: self.assist_action(id, frame_id))

    def assist_action(self, id, frame_id):
        p = db.load(id)
        f = db.frame(p, frame_id)
        out = db.project_path(id) / "assists" / db.uid()
        if f.get("reviewed"):
            raise ValueError("已确认整图保持不变；如需修正请先手动编辑")
        db.write(out / "before.json", f)
        proposed = self.run_suggestions(id, [f], out)[frame_id]
        with self.lock:
            p = db.load(id)
            f = db.frame(p, frame_id)
            existing = (
                f.get("objects", [])
                if f.get("annotated_at") or f.get("reviewed")
                else f.get("proposals", f.get("objects", []))
            )
            if f.get("reviewed"):
                return
            f.setdefault("proposal_baseline", json.loads(json.dumps(existing)))
            from core.box_geometry import duplicate

            proposed = [
                o
                for o in proposed
                if not any(duplicate(o["bbox"], b) for b in f.get("rejected_boxes", []))
            ]

            protected = (
                [
                    o
                    for o in existing
                    if not o.get("proposed")
                    or o.get("proposal_source") == "click_native_segmentation"
                ]
                if f.get("annotated_at")
                else []
            )
            merged, added = supplemental(protected, proposed)
            if merged != existing:
                db.annotate(p, frame_id, merged, False)
            db.write(
                out / "result.json",
                dict(
                    frame=frame_id,
                    added=added,
                    preserved_existing=len(protected),
                    replaced_unconfirmed=len(existing) - len(protected),
                    annotation_assistance_only=True,
                ),
            )
            self.job["added_boxes"] = added

    def annotate(self, args):
        with self.lock:
            if self.busy() and not self.job.get("annotation_editable"):
                raise ValueError("当前任务运行中，请完成后再修改标注")
            p = db.load(args["project"])
            f = db.frame(p, args["frame"])
            before = (
                f.get("objects", [])
                if f.get("annotated_at") or f.get("reviewed")
                else f.get("proposals", [])
            )
            from core.box_geometry import duplicate

            rejected = [
                o["bbox"]
                for o in before
                if not any(duplicate(o["bbox"], n["bbox"]) for n in args["objects"])
            ]
            f.setdefault(
                "proposal_baseline", json.loads(json.dumps(f.get("proposals", before)))
            )
            db.annotate(
                p,
                args["frame"],
                args["objects"],
                args.get("reviewed", False),
                args.get("expected_revision"),
            )
            f = db.frame(p, args["frame"])
            for b in rejected:
                if b not in f.setdefault("rejected_boxes", []):
                    f["rejected_boxes"].append(b)
            f["quality_review"] = "pass" if args.get("reviewed") is True else "pending"
            f["quality_assessment_mode"] = "annotation_commit_or_skip"
            f["all_objects_reviewed"] = args.get("reviewed") is True
            db.save(p)
            if args.get("feedback"):
                feedback.transfer(p, args["frame"])
            return f
            ready, _ = db.training_ready(p)

    def annotation_event(self, args):
        allowed = {
            "point",
            "draw",
            "move",
            "resize",
            "label",
            "delete",
            "confirm",
            "undo",
        }
        if args.get("kind") not in allowed:
            raise ValueError("无效标注操作")
        p = db.load(args["project"])
        f = db.frame(p, args["frame"])
        events = f.setdefault("annotation_events", [])
        if not any(e["id"] == args["id"] for e in events):
            events.append(
                dict(id=args["id"], kind=args["kind"], at_beijing=batch15.beijing_now())
            )
            db.save(p)
        return dict(recorded=True)

    def train(
        self,
        id,
        steps=None,
        batch=None,
        initialization=None,
        scene=None,
        use_feedback=False,
        scene_confirmation=False,
    ):
        with self.lock:
            if self.busy():
                raise ValueError("已有任务运行中")
            p = db.load(id)
            root = db.project_path(id)
            if scene:
                if scene.get("fixed_scene_confirmed") is not True:
                    raise ValueError("请确认整批机位和成像配置固定")
                scene = dict(scene, confirmed_at_beijing=batch15.beijing_now())
                p.setdefault("batch_scene", {})[batch] = scene
            if use_feedback:
                if initialization != "random":
                    raise ValueError("反馈训练必须明确选择随机初始化")
                if scene_confirmation is not True:
                    raise ValueError("请明确核对并确认本轮反馈与所选批次为同一场景")
                feedback.confirm_scene(p, batch)
                train, val, manifest = feedback.prepare(p, batch, root)
            else:
                train, val, manifest = batch15.prepare(p, batch, initialization, root)
            run = db.uid()
            out = root / "runs" / run
            out.mkdir(parents=True)
            job = dict(
                root=str(root),
                output=str(out),
                schema=p["schema"],
                train=train,
                validation=val,
                steps=int(steps or self.settings["steps"]),
                seed=357625,
                initial=None,
                initial_labels=None,
                initialization="random",
                historical_replay=False,
                native_roi=manifest["roi"],
                created=db.now(),
                manifest=manifest,
                test_never_used_for_selection_or_gradient=True,
            )
            db.write(out / "training_manifest.json", manifest)
            db.write(out / "job.json", job)
            p["runs"].append(run)
            db.save(p)
            for f in p["frames"]:
                if f["id"] in {r["id"] for r in train + val}:
                    f.setdefault("training_usage", []).append(
                        dict(
                            run=run,
                            annotation_revision=f.get("annotation_revision", 0),
                            submitted_at=db.now(),
                        )
                    )
            db.save(p)
            self.launch("train", id, lambda: self.train_action(out))
            self.job["progress_file"] = str(out / "progress.json")

    def train_action(self, out):
        db.write(out / "run_status.json", dict(state="running", started=db.now()))
        try:
            self.message("训练中；原图与标注已冻结，完成后导出候选模型")
            self.subprocess("train", out / "job.json")
            self.message("模型已生成，尝试本机RKNN转换")
            self.convert(out)
            job = db.read(out / "job.json")
            with self.lock:
                p = db.load(self.job["project"])
                frozen = {
                    f["id"]: f.get("annotation_revision", 0) for f in job["train"]
                }
                for f in p["frames"]:
                    for usage in f.get("training_usage", []):
                        if usage["run"] == out.name:
                            usage.update(state="complete", finished_at=db.now())
                    if f["id"] in job["manifest"].get("feedback_ids", []) and f.get(
                        "annotation_revision", 0
                    ) == frozen.get(f["id"]):
                        f["feedback_pending"] = False
                        f["feedback_trained_run"] = out.name
                db.save(p)
            db.write(
                out / "run_status.json",
                dict(state="complete", finished=db.now(), accepted=False),
            )
        except Exception as e:
            db.write(
                out / "run_status.json",
                dict(
                    state="cancelled" if self.cancelled else "error",
                    message=str(e),
                    finished=db.now(),
                ),
            )
            raise

    def convert(self, out):
        if not shutil.which("wsl"):
            db.write(
                out / "conversion.json",
                dict(available=False, message="未安装WSL；ONNX已生成，RKNN待转换"),
            )
            return
        stage = Path("C:/td_build/vision_trainer") / out.name
        stage.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out / "detector.onnx", stage / "detector.onnx")
        shutil.copy2(out / "locator.onnx", stage / "locator.onnx")
        shutil.copy2(db.ROOT / "convert_rknn.py", stage / "convert_rknn.py")
        command = [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "/home/vision/rknn-venv/bin/python",
            "/mnt/c/td_build/vision_trainer/" + out.name + "/convert_rknn.py",
        ]
        with (out / "conversion.log").open("w", encoding="utf-8") as log:
            self.process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                code = self.process.wait(timeout=240)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                code = -1
        self.check_cancel()
        if code or not all(
            (stage / (stem + "_fp16.rknn")).exists() for stem in ("detector", "locator")
        ):
            db.write(
                out / "conversion.json",
                dict(
                    available=False,
                    message="RKNN转换未完成，ONNX和训练结果已保留；见conversion.log",
                ),
            )
            return
        shutil.copy2(stage / "detector_fp16.rknn", out / "detector_fp16.rknn")
        shutil.copy2(stage / "locator_fp16.rknn", out / "locator_fp16.rknn")
        manifest = db.read(out / "training_manifest.json")
        contract = db.read(out / "output_contract.json")
        hashes = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in out.iterdir()
            if p.suffix in (".pt", ".onnx", ".rknn")
            or p.name in ("config.json", "locator_config.json")
        }
        manifest["output_hashes"] = hashes
        contract["hashes"] = hashes
        contract["rknn_pair_generated"] = True
        db.write(out / "training_manifest.json", manifest)
        db.write(out / "output_contract.json", contract)
        db.write(
            out / "conversion.json",
            dict(
                available=True,
                sha256=hashlib.sha256(
                    (out / "detector_fp16.rknn").read_bytes()
                ).hexdigest(),
                board_tested=False,
            ),
        )

    def evaluate(self, id, run, role, trained_at=None):
        self.validate_role(role)
        if role == "train":
            raise ValueError("不能把训练集作为验收集")
        p = db.load(id)
        if run not in p["runs"]:
            raise ValueError("模型版本不存在")
        root = db.project_path(id)
        out = root / "runs" / run
        manifest = (
            db.read(out / "training_manifest.json")
            if (out / "training_manifest.json").exists()
            else {}
        )
        recorded = manifest.get("training_completed_beijing") or db.read(
            out / "result.json"
        ).get("created")
        if not recorded or trained_at != recorded:
            raise ValueError("请按真实训练时间选择本次验证模型")
        frames = db.snapshot(p, role)
        config = db.read(out / "config.json")
        current_ids = feedback.class_ids(p["schema"])
        model_ids = config.get("class_ids") or feedback.class_ids(config)
        for f in frames:
            for o in f["objects"]:
                if o["label"] >= 0:
                    identity = o.get("class_id") or current_ids[o["label"]]
                    if identity not in model_ids:
                        raise ValueError(
                            "此图包含该旧模型未学习的姿态，请使用匹配模型和另拍的新测试图"
                        )
                    o["label"] = model_ids.index(identity)
        if not frames:
            raise ValueError("请先采集并标注独立验证/验收批次")
        job = dict(
            root=str(root),
            output=str(out),
            config=str(out / "config.json"),
            model=str(out / "detector.pt"),
            frames=frames,
            role=role,
        )
        path = out / ("evaluate_" + role + "_job.json")
        db.write(path, job)
        with self.lock:
            self.launch("evaluate", id, lambda: self.subprocess("evaluate", path))

    def results(self, id):
        p = db.load(id)
        root = db.project_path(id)
        rows = []
        for run in reversed(p["runs"]):
            out = root / "runs" / run
            row = dict(id=run, folder=str(out))
            for name in (
                "result",
                "conversion",
                "evaluation_validation",
                "evaluation_test",
                "run_status",
                "training_manifest",
            ):
                path = out / (name + ".json")
                if path.exists():
                    row[name] = db.read(path)
            row["tests"] = [
                db.read(x)
                for x in sorted((out / "tests").glob("*/result.json"), reverse=True)
            ]
            for test in row["tests"]:
                for r in test["rows"]:
                    f = db.frame(p, r["frame_id"])
                    r["feedback_transferred"] = bool(f.get("feedback_transferred_at"))
                    r["independent_eligible"] = not f.get(
                        "independent_test_ineligible"
                    ) and not feedback.contaminated(p, f)
                test["independent_eligible_frames"] = sum(
                    r["independent_eligible"] for r in test["rows"]
                )
            config = out / "config.json"
            if config.exists():
                row["class_mapping"] = db.read(config)
            rows.append(row)
        return rows
