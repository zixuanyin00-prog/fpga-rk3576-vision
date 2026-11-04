from pathlib import Path
import copy
import io
import json
import re
import shlex
import shutil
import threading
import time
from PIL import Image
import console_store as db
from console_board import Board
from console_jobs import Jobs
from core.gpu import worker_python

CAPTURE_METADATA = (
    "channel",
    "request_id",
    "source_channel",
    "source_request_id",
    "capture_transaction_id",
    "capture_source",
    "capture_protocol",
    "source_size",
    "identity",
    "fpga_epoch",
    "processing_revision",
    "enhance_revision",
    "processing_config",
    "image_mode",
    "capture_received_monotonic",
    "transport_dequeued_monotonic",
    "python_copy_returned_monotonic",
)


def verified_model_file(out, manifest):
    preferred = manifest.get("quantization", {}).get("preferred_model")
    for name in (
        (preferred,) if preferred else ("detector_int8.rknn", "detector_fp16.rknn")
    ):
        if name not in ("detector_int8.rknn", "detector_fp16.rknn"):
            return None
        path = out / name
        expected = manifest.get("output_hashes", {}).get(name)
        if expected and path.is_file() and db.sha(path.read_bytes()) == expected:
            return name
    return None


class Workflow:
    def __init__(self):
        from service import DEFAULTS

        self.settings = dict(DEFAULTS)
        for path in (db.ROOT / "settings.json", db.ROOT / "console_settings.json"):
            if path.exists():
                self.settings.update(db.read(path))
        self.jobs = Jobs()
        self.preview_lock = threading.Lock()
        self.preview_cache = None
        self.preview_at = 0
        self.preview_error = ""
        self.board_cache = None
        self.board_at = 0
        self.board_lock = threading.Lock()
        self.selections = {}
        self.stop_event = threading.Event()
        from console_event_collector import EventCollector

        self.event_collector = EventCollector(
            lambda: self.settings.copy(), self._event_log_pause_reason, self.stop_event
        ).start()
        from console_event_png import start as start_event_png

        start_event_png()

    def close(self):
        self.event_collector.close()

    def _event_log_pause_reason(self):
        if self.jobs.active.get("board") or self.jobs.waiting.get("board"):
            return "board_job"
        cached = self.board_cache
        if (
            not cached
            or not cached.get("connected")
            or time.monotonic() - self.board_at > 5
        ):
            return None
        runtime = cached["runtime"]
        inference = runtime.get("inference", {})
        metrics = inference.get("async_inference", {})
        latency = metrics.get("publication", {}).get("received_to_publish_ms", {})
        if (
            latency.get("samples", 0) > 0
            and metrics.get("metrics_age_ms", 0) < 5000
            and latency.get("p95", 0) > 100
        ):
            return "publication_latency"
        if (
            runtime.get("inference_enabled")
            and latency.get("samples", 0) >= 30
            and metrics.get("rate_window_seconds", 0) >= 0.4
        ):
            fps = inference.get("effective_result_fps")
            if fps is not None and fps < 59:
                return "inference_cadence"
        for display in cached.get("displays", {}).values():
            fps = display.get("fresh_fps")
            if fps is not None and fps < 59:
                return "display_cadence"
        return None

    def helper_candidates(self):
        paths = list((db.ROOT / "box_assistant" / "runs").glob("*/result.json")) + list(
            db.DATA.glob("*/helpers/*/result.json")
        )
        result = []
        for path in paths:
            try:
                meta = db.read(path)
                if (
                    meta.get("initialization") != "random"
                    or meta.get("pretrained_weights_used") is not False
                    or not (path.parent / "boxes.pt").is_file()
                ):
                    continue
                if meta.get("architecture") not in (
                    "scratch-box-cnn-v1",
                    "scratch-box-fpn-verifier-v2",
                ):
                    continue
                result.append(
                    dict(
                        key=str(path.parent.relative_to(db.ROOT)).replace("\\", "/"),
                        run=path.parent.name,
                        trained_at=meta.get("created"),
                        training_started_beijing=meta.get("training_started_beijing"),
                        sha256=meta["sha256"],
                        scene=meta.get("scene"),
                        architecture=meta["architecture"],
                        native_detector_only=True,
                    )
                )
            except (OSError, ValueError, KeyError):
                continue
        return sorted(result, key=lambda x: x.get("trained_at") or "", reverse=True)

    def state(self, identity=None):
        flow = db.load(identity) if identity else None
        if flow:
            flow = copy.deepcopy(flow)
            for f in flow["frames"]:
                f.pop("history", None)
            for run in flow["runs"]:
                path = db.folder(identity) / "runs" / run["id"]
                m = {}
                if (path / "training_manifest.json").exists():
                    m = db.read(path / "training_manifest.json")
                    run["manifest"] = {
                        k: m.get(k)
                        for k in (
                            "batch",
                            "scene",
                            "roi",
                            "original_images",
                            "training_started_beijing",
                            "training_completed_beijing",
                            "output_hashes",
                            "pixel_policy",
                            "quantization",
                        )
                    }
                run["model_file"] = verified_model_file(path, m)
                run["convert_available"] = run["model_file"] is not None
                run["int8_test_available"] = bool(
                    m.get("output_hashes", {}).get("detector_int8.rknn")
                    and (path / "detector_int8.rknn").is_file()
                )
        event_logs = self.event_collector.snapshot()
        producer = None
        if (
            self.board_cache
            and self.board_cache.get("connected")
            and time.monotonic() - self.board_at <= 5
        ):
            producer = (
                self.board_cache["runtime"].get("inference", {}).get("terminal_logging")
            )
        event_logs.update(
            producer=producer,
            producer_status="available" if producer else "unknown",
            queued=producer.get("queued") if producer is not None else None,
            dropped=producer.get("dropped_events") if producer is not None else None,
        )
        return dict(
            flow=flow,
            flows=db.listing(),
            jobs=self.jobs.state(identity),
            helpers=self.helper_candidates(),
            settings=self.settings,
            board=self.board_cache,
            board_error=self.preview_error,
            pixel_policy="native_crop_only_v1",
            server_time=db.now(),
            event_logs=event_logs,
        )

    def cached_board(self):
        if time.monotonic() - self.board_at < 1 and self.board_cache:
            return self.board_cache
        if not self.board_lock.acquire(False):
            return self.board_cache or dict(connected=False, message="正在读取板端")
        try:
            with Board(self.settings.copy()) as board:
                state = board.status()
            self.board_cache = state
            self.board_at = time.monotonic()
            return state
        except Exception as exc:
            self.board_cache = dict(connected=False, message=str(exc))
            self.board_at = time.monotonic()
            raise
        finally:
            self.board_lock.release()

    def preview(self):
        if int(time.monotonic() * 2) == int(self.preview_at * 2) and self.preview_cache:
            return self.preview_cache
        if not self.preview_lock.acquire(False):
            if self.preview_cache:
                return self.preview_cache
            raise ValueError("首张HDMI快照正在读取，稍后自动重试")
        try:
            started = time.monotonic()
            with Board(self.settings.copy()) as board:
                result = board.screen()
            self.preview_cache = result
            self.preview_at = started
            self.preview_error = ""
            return result
        except Exception as exc:
            self.preview_error = str(exc)
            raise
        finally:
            self.preview_lock.release()

    def save_settings(self, args):
        values = {
            k: args[k]
            for k in ("host", "username", "key", "remote", "steps")
            if k in args
        }
        if "steps" in values:
            values["steps"] = int(values["steps"])
            if not 50 <= values["steps"] <= 3000:
                raise ValueError("训练步数为50–3000")
        for k in ("host", "username", "key", "remote"):
            if k in values and (
                not isinstance(values[k], str) or not values[k].strip()
            ):
                raise ValueError("连接设置不能为空")
        self.settings.update(values)
        db.write(db.ROOT / "console_settings.json", self.settings)
        self.board_at = self.preview_at = 0
        self.board_cache = None
        self.preview_cache = None
        return self.settings

    def run_worker(self, mode, spec, out, job):
        spec = dict(spec, output=str(out))
        db.write(out / "job.json", spec)
        job.update(progress_file=str(out / "progress.json"), message="GPU任务执行中")
        job.run(
            [
                worker_python(),
                "-u",
                str(db.ROOT / "console_worker.py"),
                mode,
                str(out / "job.json"),
            ]
        )

    def freeze(self, identity, rect, job, edited=None):
        if edited:
            raise ValueError(
                "成像编辑尚未自动保存，请等待保存完成后再固定参数；本次未连接板卡"
            )
        with self.jobs.resource("board", job):
            with Board(self.settings.copy()) as board:
                profile = board.freeze(job, edited)

                dest = db.folder(identity) / "probes" / db.uid()
                frames, result = board.capture(rect, 1, dest, job)
                if not frames:
                    raise ValueError("未取得原生选区图像，请重试")
                board.ensure_profile(profile)
                job.check()
                if result.get("protocol") == "native-rectangle-v3" and profile.get(
                    "acquisition"
                ) != dict(
                    protocol=result["protocol"],
                    capture_source=result["capture_source"],
                    source_channel=result["source_channel"],
                    source_size=result["source_size"],
                ):
                    raise ValueError(
                        "原生采集来源尚未冻结或已经改变，请回第一步重新固定参数"
                    )
                if result.get("protocol") in (
                    "native-rectangle-v2",
                    "native-rectangle-v3",
                ):
                    profile["processing_identity"] = {
                        k: frames[0][k]
                        for k in (
                            "fpga_epoch",
                            "processing_revision",
                            "enhance_revision",
                        )
                    }
                    profile["image_processing"] = result.get(
                        "processing", "shared_processed_rgb_v1"
                    )
                    if frames[0].get("processing_config"):
                        profile["frame_processing_config"] = frames[0][
                            "processing_config"
                        ]
                        profile["image_mode"] = frames[0]["image_mode"]
            with db.LOCK:
                p = db.load(identity)
                p.update(
                    rectangle=rect,
                    profile=profile,
                    capture_batch=db.uid(),
                    stage=2,
                    helper_selection=None,
                )
                p["probe"] = dict(
                    file=str(
                        (dest / frames[0]["file"]).relative_to(db.folder(identity))
                    ),
                    size=rect[2:],
                    origin=rect[:2],
                    sha256=frames[0]["sha256"],
                    source_frame_id=frames[0]["source_frame_id"],
                    **{k: frames[0][k] for k in CAPTURE_METADATA if k in frames[0]},
                )
                db.save(p)
        return dict(profile=profile, rectangle=rect)

    def capture(self, identity, role, job, append=False, test_id=None):
        p = db.load(identity)
        rect = db.rectangle(p["rectangle"], training=True)
        profile = p.get("profile")
        if role == "train" and not profile:
            raise ValueError("请先在第一步冻结成像参数")
        if role == "train":
            batch = p.get("capture_batch") if append else db.uid()
            prior = [
                f
                for f in p["frames"]
                if f.get("batch") == batch and not f.get("excluded")
            ]
            count = 15 - len(prior)
            if count <= 0:
                return []
        else:
            batch = db.uid()
            prior = []
            count = 10
        dest = db.folder(identity) / "captures" / batch / db.uid()
        items = []
        with self.jobs.resource("board", job):
            with Board(self.settings.copy()) as board:
                if role == "train":
                    board.ensure_profile(profile)
                else:
                    profile = board.profile(board.status())
                job.update(message="采集" + str(count) + "张现场原生图像")
                frames, result = board.capture(rect, count, dest, job)
                if role == "train":
                    board.ensure_profile(profile)
                processing = profile.get("processing_identity")
                if (
                    role == "train"
                    and processing
                    and (
                        result.get("protocol")
                        not in ("native-rectangle-v2", "native-rectangle-v3")
                        or any(
                            any(f.get(k) != value for k, value in processing.items())
                            for f in frames
                        )
                    )
                ):
                    raise ValueError(
                        "公共图像处理版本已改变，请回第一步重新固定参数；已采原图保留"
                    )
                if result.get("protocol") == "native-rectangle-v3" and profile.get(
                    "acquisition"
                ) != dict(
                    protocol=result["protocol"],
                    capture_source=result["capture_source"],
                    source_channel=result["source_channel"],
                    source_size=result["source_size"],
                ):
                    raise ValueError(
                        "采集来源与冻结场景不一致，请回第一步重新固定参数；已采原图保留"
                    )
                for f in frames:

                    key = (board.boot, f.get("fpga_epoch"), f["source_frame_id"])
                    same = next(
                        (
                            q
                            for q in p["frames"]
                            if (
                                q.get("boot_id"),
                                q.get("fpga_epoch"),
                                q.get("source_frame_id"),
                            )
                            == key
                            or q["sha256"] == f["sha256"]
                        ),
                        None,
                    )
                    if same:
                        continue
                    items.append(
                        dict(
                            id=db.uid(),
                            batch=batch,
                            role=role,
                            base_batch=(
                                p.get("capture_batch") if role != "train" else batch
                            ),
                            file=str(
                                (dest / f["file"]).relative_to(db.folder(identity))
                            ),
                            size=rect[2:],
                            origin=rect[:2],
                            sha256=f["sha256"],
                            source_frame_id=f["source_frame_id"],
                            boot_id=board.boot,
                            profile_digest=profile["digest"],
                            captured_at=db.now(),
                            objects=[],
                            reviewed=False,
                            test_id=test_id,
                            revision=0,
                            excluded=False,
                            **{k: f[k] for k in CAPTURE_METADATA if k in f},
                        )
                    )

            with db.LOCK:
                current = db.load(identity)
                current["frames"].extend(items)
                if role != "train":
                    current.setdefault("capture_profiles", {})[batch] = profile
                if role == "train" and not job.cancel.is_set():
                    current["capture_batch"] = batch
                    current["helper_selection"] = None
                db.save(current)
        job.check()
        if len(items) < count:
            job.update(
                message=f"已保留{len(items)}/{count}张新图；请移动工件后补采",
                capture_partial=True,
            )
        return items

    def prepare_tiles(self, p, rows, out, helper=False):
        labels = [v for v in p["labels"] if v["active"]]
        ids = [v["id"] for v in labels]
        if not ids:
            raise ValueError("至少保留一种姿态")
        if not rows:
            raise ValueError("没有已确认的训练原图")
        if any(not f.get("reviewed") for f in rows):
            raise ValueError("请逐张确认全部目标后训练")
        source_keys = set()
        all_tiles = []
        split = {}
        class_counts = {k: 0 for k in ids}
        for f in rows:
            if f.get("profile_digest") != p["profile"]["digest"]:
                raise ValueError("训练图的成像参数与当前冻结场景不一致，请使用当前批次")
            key = (f["boot_id"], f.get("fpga_epoch"), f["source_frame_id"])
            if key in source_keys:
                raise ValueError("训练集合存在重复源帧")
            source_keys.add(key)
            for o in f["objects"]:
                if o["class_id"] in class_counts:
                    class_counts[o["class_id"]] += 1
                elif o["class_id"] != "ignore":
                    raise ValueError("标注引用未分类或已删除姿态")

        remaining = class_counts.copy()
        validation = set()
        if not helper:
            for f in sorted(rows, key=lambda f: db.sha(f["id"].encode())):
                counts = {k: sum(o["class_id"] == k for o in f["objects"]) for k in ids}
                if (
                    len(validation) < 3
                    and len(rows) - len(validation) > 3
                    and all(remaining[k] - counts[k] > 0 for k in ids)
                ):
                    validation.add(f["id"])
                    remaining = {k: remaining[k] - counts[k] for k in ids}
        for f in rows:
            im = db.image(p, f)
            role = "validation" if f["id"] in validation else "train"
            split[f["id"]] = role
            for index, (x, y, w, h) in enumerate(db.windows(*f["size"])):
                objects = []
                for o in f["objects"]:
                    a, b, c, d = o["bbox"]
                    clipped = [
                        max(a, x) - x,
                        max(b, y) - y,
                        min(c, x + w) - x,
                        min(d, y + h) - y,
                    ]
                    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
                        continue
                    label = ids.index(o["class_id"]) if o["class_id"] in ids else -1
                    if a < x or b < y or c > x + w or d > y + h:
                        label = -1
                    objects.append(dict(bbox=clipped, label=label))
                raw = im.crop((x, y, x + w, y + h)).tobytes()
                tile_id = f["id"] + "_" + str(index)
                path = out / "tiles" / (tile_id + ".rgb")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                all_tiles.append(
                    dict(
                        id=tile_id,
                        file=str(path.relative_to(db.folder(p["id"]))),
                        sha256=db.sha(raw),
                        objects=objects,
                        role=role,
                        original_id=f["id"],
                        parent_sha256=f["sha256"],
                        boot_id=f["boot_id"],
                        source_frame_id=f["source_frame_id"],
                        source_group=f["batch"],
                        capture_session=f["batch"],
                        native_roi=True,
                    )
                )
        train = [f for f in all_tiles if f["role"] == "train"]
        val = [f for f in all_tiles if f["role"] == "validation"]
        missing = [
            labels[i]["name"]
            for i in range(len(ids))
            if not any(o["label"] == i for f in train for o in f["objects"])
        ]
        if missing and not helper:
            raise ValueError(
                "训练原生窗口缺少完整目标："
                + ",".join(missing)
                + "；请补标、调整选区或删除未使用姿态"
            )
        if not any(o["label"] >= 0 for f in train for o in f["objects"]):
            raise ValueError("没有完整元件框可以训练")
        manifest = dict(
            batch=p["capture_batch"],
            scene=dict(name=p["name"], rectangle=p["rectangle"], profile=p["profile"]),
            roi=p["rectangle"],
            original_images=len(rows),
            unique_original_images=len(rows),
            split=split,
            source_image_hashes={f["id"]: f["sha256"] for f in rows},
            class_instances=class_counts,
            source_frames={
                f["id"]: dict(
                    source_frame_id=f["source_frame_id"],
                    origin=f["origin"],
                    size=f["size"],
                    sha256=f["sha256"],
                    **{k: f[k] for k in CAPTURE_METADATA if k in f},
                )
                for f in rows
            },
            initialization=dict(kind="random", historical_weights_used=False),
            pixel_policy="native_crop_only_v1",
            feedback_ids=[f["id"] for f in rows if f.get("feedback")],
            training_started_beijing=None,
            training_completed_beijing=None,
        )
        return dict(
            root=str(db.folder(p["id"])),
            train=train,
            validation=val,
            manifest=manifest,
            schema=dict(
                labels=ids,
                class_ids=ids,
                titles=[v["name"] for v in labels],
                numbers=[],
            ),
            initialization="random",
            initial=None,
            historical_replay=False,
            steps=int(self.settings["steps"]),
            seed=357626,
        )

    def train(self, identity, job, helper=False):
        p = db.load(identity)
        batch = p.get("capture_batch")
        base = [
            f
            for f in p["frames"]
            if f["role"] == "train"
            and f.get("batch") == batch
            and not f.get("excluded")
        ]
        feedback = [
            f
            for f in p["frames"]
            if f.get("feedback")
            and f.get("base_batch") == batch
            and not f.get("excluded")
        ]
        rows = (
            [f for f in base + feedback if f.get("reviewed")]
            if helper
            else base + feedback
        )
        if helper:
            if len(rows) < 3:
                raise ValueError("请先手工完整确认至少3张图，再训练独立画框助手")
        elif len(base) != 15:
            raise ValueError("当前批次需15张原图；请先采集或补齐")
        run = db.uid()
        out = db.folder(identity) / ("helpers" if helper else "runs") / run
        with self.jobs.resource("gpu", job):
            job.update(message="冻结标注并生成原生裁剪窗口")
            spec = self.prepare_tiles(p, rows, out, helper)
            job.check()
            db.write(out / "training_manifest.json", spec["manifest"])
            if not helper:
                with db.LOCK:
                    p = db.load(identity)
                    p["runs"].append(
                        dict(id=run, created_at=db.now(), batch=batch, state="training")
                    )
                    p["stage"] = 3
                    db.save(p)
            self.run_worker("helper_train" if helper else "train", spec, out, job)
            if helper:
                return dict(helper=run, selection_required=True)
            self.convert(out, job)
            with db.LOCK:
                p = db.load(identity)
                next(r for r in p["runs"] if r["id"] == run)["state"] = "trained"
                db.save(p)
        return dict(run=run)

    def convert(self, out, job):
        if not shutil.which("wsl"):
            raise ValueError("GPU模型已保存；RKNN转换需要本机WSL，可安装后点击重新转换")
        precision = self.settings.get("rknn_precision", "int8")
        if precision not in ("int8", "fp16", "both"):
            raise ValueError("RKNN精度请选择int8、fp16或both")
        spec = db.read(
            out
            / (
                "resolved_training_job.json"
                if (out / "resolved_training_job.json").exists()
                else "job.json"
            )
        )
        manifest = db.read(out / "training_manifest.json")
        source_root = Path(spec["root"]).resolve()
        stage = Path("C:/td_build/vision_console") / out.name
        stage.mkdir(parents=True, exist_ok=True)
        sources = ("detector.onnx", "locator.onnx", "config.json")
        hashes = {name: db.sha((out / name).read_bytes()) for name in sources}
        if any(
            manifest["output_hashes"].get(name) != digest
            for name, digest in hashes.items()
        ):
            raise ValueError("转换源文件与训练记录不一致")
        for name in sources:
            shutil.copy2(out / name, stage / name)
        db.write(
            stage / "conversion_request.json",
            dict(
                source_root="/mnt/"
                + source_root.drive[0].lower()
                + "/"
                + source_root.as_posix()[3:],
                train=spec["train"],
                validation_hashes=[row["sha256"] for row in spec["validation"]],
                split=spec["manifest"]["split"],
                source_hashes=hashes,
                training_started_beijing=manifest["training_started_beijing"],
                training_completed_beijing=manifest["training_completed_beijing"],
            ),
        )
        shutil.copy2(db.ROOT / "convert_rknn.py", stage / "convert_rknn.py")
        job.update(
            message="通过已有RKNN工具链转换"
            + precision.upper()
            + "；INT8仅用训练原生图校准"
        )
        job.run(
            [
                "wsl",
                "-d",
                "Ubuntu",
                "--",
                "/home/vision/rknn-venv/bin/python",
                "/mnt/c/td_build/vision_console/" + out.name + "/convert_rknn.py",
                "--precision",
                precision,
            ],
            timeout=300,
        )
        conversion = db.read(stage / "quantization_manifest.json")
        if not conversion["completed"]:
            raise ValueError("RKNN转换尚未完整完成")
        for name, artifact in conversion["artifacts"].items():
            if db.sha((stage / name).read_bytes()) != artifact["sha256"]:
                raise ValueError("转换产物哈希改变：" + name)
            shutil.copy2(stage / name, out / name)
            manifest["output_hashes"][name] = artifact["sha256"]
        shutil.copy2(
            stage / "quantization_manifest.json", out / "quantization_manifest.json"
        )
        config = db.read(out / "config.json")
        config["quantization"] = conversion["quantization"]
        db.write(out / "config.json", config)
        manifest.update(quantization=conversion["quantization"])
        manifest["output_hashes"].update(
            {
                "config.json": db.sha((out / "config.json").read_bytes()),
                "quantization_manifest.json": db.sha(
                    (out / "quantization_manifest.json").read_bytes()
                ),
            }
        )
        db.write(out / "training_manifest.json", manifest)

    def select_helper(self, identity, args):
        candidate = next(
            (c for c in self.helper_candidates() if c["key"] == args.get("key")), None
        )
        if (
            not candidate
            or args.get("trained_at") != candidate["trained_at"]
            or args.get("sha256") != candidate["sha256"]
            or args.get("confirmed") is not True
        ):
            raise ValueError("请明确选择画框模型训练时间、场景与哈希")
        if (
            db.sha((db.safe(db.ROOT, candidate["key"]) / "boxes.pt").read_bytes())
            != candidate["sha256"]
        ):
            raise ValueError("画框模型哈希改变")
        with db.LOCK:
            p = db.load(identity)
            p["helper_selection"] = dict(
                candidate, selected_at=db.now(), batch=p.get("capture_batch")
            )
            db.save(p)
        return candidate

    def assist(self, identity, job, frame=None):
        p = db.load(identity)
        selected = p.get("helper_selection")
        if not selected:
            raise ValueError("请先明确选择本次画框模型；也可手工标3张后训练新助手")
        rows = [
            f
            for f in p["frames"]
            if f.get("batch") == p["capture_batch"]
            and not f.get("excluded")
            and not f.get("reviewed")
            and (frame is None or f["id"] == frame)
        ]
        if not rows:
            return dict(frames=0)
        out = db.folder(identity) / "assists" / db.uid()
        with self.jobs.resource("gpu", job):
            self.run_worker(
                "assist",
                dict(
                    root=str(db.folder(identity)),
                    model=str(db.safe(db.ROOT, selected["key"]) / "boxes.pt"),
                    selected=selected,
                    frames=rows,
                ),
                out,
                job,
            )
            result = db.read(out / "predictions.json")
            with db.LOCK:
                p = db.load(identity)
                for row in result["rows"]:
                    f = next(f for f in p["frames"] if f["id"] == row["frame"])
                    if f.get("reviewed"):
                        continue
                    previous = next(old for old in rows if old["id"] == f["id"])
                    if f.get("revision", 0) != previous.get("revision", 0):
                        continue
                    human = [o for o in f.get("objects", []) if not o.get("proposed")]
                    from core.decoder import iou

                    proposals = [
                        dict(
                            id=db.uid(),
                            bbox=d["bbox"],
                            class_id=None,
                            proposed=True,
                            confidence=d["objectness"],
                        )
                        for d in row["detections"]
                        if all(iou(d["bbox"], o["bbox"]) < 0.35 for o in human)
                    ]
                    f["objects"] = human + proposals
                    f["revision"] = f.get("revision", 0) + 1
                db.save(p)
        return dict(frames=len(rows))

    def selected_run(self, identity, args):
        p = db.load(identity)
        run = str(args["run"])
        if run not in {r["id"] for r in p["runs"]}:
            raise ValueError("模型不属于当前流程")
        out = db.folder(identity) / "runs" / run
        manifest = db.read(out / "training_manifest.json")
        trained = manifest.get("training_completed_beijing")
        if (
            not trained
            or args.get("trained_at") != trained
            or args.get("confirmed") is not True
        ):
            raise ValueError("请按实际训练北京时间明确选择本次使用模型")
        digest = db.sha((out / "detector.pt").read_bytes())
        if digest != manifest["output_hashes"]["detector.pt"]:
            raise ValueError("模型与训练记录不一致")
        return p, out, manifest, dict(run=run, trained_at=trained, sha256=digest)

    def test(self, identity, args, job):
        p, out, manifest, selected = self.selected_run(identity, args)
        test_id = db.uid()
        from console_int8_test import int8_artifact, run as run_int8_test

        inference = int8_artifact(out, manifest)
        if args.get("int8_sha256") != inference["model_sha256"]:
            raise ValueError(
                "INT8文件与页面选择不一致，请刷新并按训练时间重新选择；未开始采图"
            )
        job.update(message="按当前现场参数采集测试图，保留所选模型的训练环境记录")
        rows = self.capture(identity, "test", job, test_id=test_id)
        if not rows:
            raise ValueError("未采到新的独立源帧；请移动工件后重试")
        dest = out / "tests" / test_id
        with self.jobs.resource("board", job):
            with Board(self.settings.copy()) as board:
                result = run_int8_test(
                    board,
                    out,
                    db.folder(identity),
                    rows,
                    selected,
                    inference,
                    dest,
                    job,
                )
        with db.LOCK:
            p = db.load(identity)
            p["tests"].append(
                dict(
                    id=test_id,
                    run=out.name,
                    created_at=db.now(),
                    frames=[f["id"] for f in rows],
                    selected=selected,
                    predictions=result["rows"],
                    used_for_training=False,
                    reviewed=False,
                    complete=len(rows) == 10,
                    inference=inference,
                    training_profile=manifest["scene"]["profile"],
                    captured_profile=p["capture_profiles"][rows[0]["batch"]],
                )
            )
            db.save(p)
        return dict(test=test_id, frames=len(rows), complete=len(rows) == 10)

    def feedback(self, identity, args):
        with db.LOCK:
            p = db.load(identity)
            test = next(t for t in p["tests"] if t["id"] == args["test"])
            manifest = db.read(
                db.folder(identity) / "runs" / test["run"] / "training_manifest.json"
            )
            if manifest["batch"] != p.get("capture_batch"):
                raise ValueError("该测试属于另一采集批次，不能混入当前场景")
            chosen = set(args.get("frames", []))
            if not chosen or not chosen <= set(test["frames"]):
                raise ValueError("请选择本次测试中的失败图像")
            if any(
                f.get("profile_digest") != (p.get("profile") or {}).get("digest")
                for f in p["frames"]
                if f["id"] in chosen
            ):
                raise ValueError(
                    "测试图与当前训练集的成像参数不同；请先在第一步固定新环境并采集新训练批次，不能直接混入当前训练集"
                )
            for f in p["frames"]:
                if f["id"] not in chosen:
                    continue
                row = next(r for r in test["predictions"] if r["frame"] == f["id"])
                if not f.get("feedback"):
                    f["objects"] = [
                        dict(
                            id=db.uid(),
                            bbox=d["bbox"],
                            class_id=(
                                d["label"]
                                if d["label"]
                                in {l["id"] for l in p["labels"] if l["active"]}
                                else None
                            ),
                            proposed=True,
                        )
                        for d in row["detections"]
                    ]
                    f.update(
                        reviewed=False,
                        feedback=True,
                        role="train",
                        base_batch=p["capture_batch"],
                        revision=f.get("revision", 0) + 1,
                    )
            test.update(used_for_training=True, reviewed=False, retired_at=db.now())
            p["stage"] = 2
            db.save(p)
        return dict(frames=list(chosen))

    def prepare_deploy(self, identity, args):
        p, out, manifest, selected = self.selected_run(identity, args)
        tests = [
            t
            for t in p["tests"]
            if t["run"] == out.name
            and t.get("complete")
            and t.get("reviewed")
            and not t.get("used_for_training")
            and len(t["frames"]) == 10
            and t["selected"] == selected
        ]
        if not tests:
            raise ValueError("请先完成并人工确认当前版本的10张独立新图测试")
        model_file = verified_model_file(out, manifest)
        if not model_file:
            raise ValueError("RKNN尚未转换成功或文件与训练记录不一致；请重新转换")
        hashes = {}
        for name in (model_file, "config.json"):
            raw = (out / name).read_bytes()
            hashes[name] = db.sha(raw)
            if manifest["output_hashes"].get(name) != hashes[name]:
                raise ValueError("部署文件与训练记录不一致：" + name)
        if not any(
            t.get("inference", {}).get("backend") == "RKNN-INT8-NPU"
            and t["inference"]["model_file"] == model_file
            and t["inference"]["model_sha256"] == hashes[model_file]
            and t["inference"]["config_sha256"] == hashes["config.json"]
            for t in tests
        ):
            raise ValueError(
                "请先完成并人工确认与本次部署文件哈希一致的INT8 NPU新图测试；历史PT测试不作为INT8验收"
            )
        selection = dict(
            project=identity,
            project_name=p["name"],
            run=out.name,
            batch=manifest["batch"],
            training_started_beijing=manifest["training_started_beijing"],
            training_completed_beijing=manifest["training_completed_beijing"],
            scene=manifest["scene"],
            hashes=hashes,
            model_file=model_file,
            fine_sha256=hashes[model_file],
            config_sha256=hashes["config.json"],
        )
        nonce = db.uid()
        self.selections[nonce] = dict(
            flow=identity, selection=selection, expires=time.monotonic() + 600
        )
        return dict(nonce=nonce, selection=selection)

    def deploy(self, identity, args, job):
        unexpected = set(args) - {"flow", "nonce", "confirmed"}
        if unexpected:
            raise ValueError(
                "模型切换不接收现场配置或运行时文件字段："
                + ",".join(sorted(unexpected))
                + "；请刷新控制台。未停止服务"
            )
        choice = self.selections.pop(args.get("nonce"), None)
        if (
            not choice
            or choice["flow"] != identity
            or time.monotonic() > choice["expires"]
            or args.get("confirmed") is not True
        ):
            raise ValueError("部署选择已过期，请重新查看训练时间并选择")
        selected = choice["selection"]
        out = db.folder(identity) / "runs" / selected["run"]
        bundle = db.folder(identity) / "deployments" / db.uid()
        bundle.mkdir(parents=True)
        job.update(
            deployment=bundle.name,
            stage="preflight",
            message="模型切换预检：锁定实际运行版本，保留现场配置和服务入口",
        )
        from console_model_deploy import deploy_model

        with self.jobs.resource("board", job):
            with Board(self.settings.copy()) as board:
                result = deploy_model(board, out, selected, bundle, job)
        db.write(bundle / "result.json", result)
        with db.LOCK:
            p = db.load(identity)
            p["deployments"].append(dict(id=bundle.name, created_at=db.now(), **result))
            p["stage"] = 4
            db.save(p)
        return result

    def dispatch(self, action, args, page):
        if action == "new":
            if args.get("flow"):
                self.jobs.stop(args["flow"])
            return db.create(args.get("name"))
        if action == "settings":
            return self.save_settings(args)
        identity = args.get("flow")
        flow = db.load(identity)
        if action == "stop":
            self.jobs.stop(identity, args.get("job"))
            return dict(stopping=True)
        if action == "annotate":
            with db.LOCK:
                return db.annotate(
                    db.load(identity),
                    args["frame"],
                    args["objects"],
                    args.get("reviewed", False),
                    page,
                )
        if action == "label":
            with db.LOCK:
                p = db.load(identity)
                if args["operation"] == "add":
                    if sum(l["active"] for l in p["labels"]) >= 30:
                        raise ValueError("最多30种姿态")
                    p["labels"].append(
                        dict(id=db.uid(), name=db.letter(len(p["labels"])), active=True)
                    )
                elif args["operation"] == "delete":
                    active = [l for l in p["labels"] if l["active"]]
                    if len(active) <= 1:
                        raise ValueError("至少保留一种姿态")
                    next(l for l in p["labels"] if l["id"] == args["id"])[
                        "active"
                    ] = False
                    for f in p["frames"]:
                        for o in f["objects"]:
                            if o["class_id"] == args["id"]:
                                o["class_id"] = None
                                f["reviewed"] = False
                                f["revision"] = f.get("revision", 0) + 1
                else:
                    raise ValueError("姿态操作无效")
                db.save(p)
                return p["labels"]
        if action == "exclude":
            with db.LOCK:
                p = db.load(identity)
                f = next(f for f in p["frames"] if f["id"] == args["frame"])
                f["excluded"] = bool(args["excluded"])
                db.save(p)
                return f
        if action == "helper_select":
            return self.select_helper(identity, args)
        if action == "feedback":
            return self.feedback(identity, args)
        if action == "test_review":
            with db.LOCK:
                p = db.load(identity)
                test = next(t for t in p["tests"] if t["id"] == args["test"])
                if test["used_for_training"]:
                    raise ValueError("该测试组已用于纠错，请重新采10张新图")
                if not test.get("complete") or len(test["frames"]) != 10:
                    raise ValueError("本组不足10张，已保留供查看；请重新采集独立10张")
                test.update(
                    reviewed=args.get("confirmed") is True, reviewed_at=db.now()
                )
                db.save(p)
                return test
        if action == "prepare_deploy":
            return self.prepare_deploy(identity, args)
        if action == "freeze":
            if args.get("edited"):
                raise ValueError("请刷新页面，等待成像参数自动保存完成后再固定。")
            rect = db.rectangle(args["rectangle"], training=True)
            fn = lambda j: self.freeze(identity, rect, j)
        elif action == "capture":
            db.rectangle(flow["rectangle"], training=True)
            fn = lambda j: self.capture(identity, "train", j, args.get("append", False))
        elif action == "train":
            fn = lambda j: self.train(identity, j)
        elif action == "helper_train":
            fn = lambda j: self.train(identity, j, True)
        elif action == "assist":
            fn = lambda j: self.assist(identity, j, args.get("frame"))
        elif action == "test":
            db.rectangle(flow["rectangle"], training=True)
            fn = lambda j: self.test(identity, args, j)
        elif action == "deploy":
            fn = lambda j: self.deploy(identity, args, j)
        elif action == "convert":
            if args["run"] not in {r["id"] for r in db.load(identity)["runs"]}:
                raise ValueError("训练版本无效")

            def fn(j):
                with self.jobs.resource("gpu", j):
                    return self.convert(db.folder(identity) / "runs" / args["run"], j)

        elif action == "action_output":

            def fn(j):
                from console_action import apply

                with self.jobs.resource("board", j):
                    with Board(self.settings.copy()) as board:
                        result = apply(board, args.get("values"), j)
                        self.board_cache = board.status()
                        self.board_at = time.monotonic()
                        return result

        elif action in ("camera", "legal", "readback"):

            def fn(j):
                if action == "camera":
                    j.update(camera_operation=args["operation"])
                with self.jobs.resource("board", j):
                    with Board(self.settings.copy()) as board:
                        if action == "legal":
                            state = board.status()
                            if (
                                state["runtime"]
                                .get("inference", {})
                                .get("async_inference", {})
                                .get("input_transform")
                                != "native_tiles_v1"
                            ):
                                raise ValueError(
                                    "请先通过第四步部署原生裁剪模型；不会把合法区传入旧拉伸模型"
                                )
                            return board.configure(args["values"], j)
                        result = board.camera(
                            "readback" if action == "readback" else args["operation"],
                            {} if action == "readback" else args["values"],
                            j,
                        )
                        current = board.status()
                        self.board_cache = current
                        self.board_at = time.monotonic()
                        if action == "camera":
                            with db.LOCK:
                                p = db.load(identity)
                                p["camera_settings"] = dict(
                                    saved_at=db.now(),
                                    requested=args["values"],
                                    actual=current["camera"],
                                )
                                db.save(p)
                            result = dict(
                                result,
                                saved_at=p["camera_settings"]["saved_at"],
                                camera=current["camera"],
                            )
                        return result

        else:
            raise ValueError("未知操作：" + action)
        return self.jobs.submit(identity, page, action, fn)
