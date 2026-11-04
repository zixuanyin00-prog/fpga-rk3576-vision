from pathlib import Path
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.metadata
import json
import struct
import cv2
import numpy as np
from rknn.api import RKNN


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision", choices=("int8", "fp16", "both"), default="int8")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    request = json.loads((root / "conversion_request.json").read_text(encoding="utf-8"))
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    if config["input_transform"] != "native_tiles_v1" or config["input_size"] != [
        512,
        288,
    ]:
        raise ValueError("RKNN conversion requires native 512x288 RGB tiles")
    for name, expected in request["source_hashes"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Conversion source hash differs: " + name)
    settings = dict(
        target_platform="rk3576",
        mean_values=[config["mean"]],
        std_values=[config["std"]],
        optimization_level=3,
    )
    rows = []
    if args.precision != "fp16":
        settings.update(
            quantized_dtype="w8a8",
            quantized_method="channel",
            quantized_algorithm="normal",
            quant_img_RGB2BGR=False,
        )
        calibration = root / "calibration"
        calibration.mkdir(exist_ok=True)
        withheld = set(request["validation_hashes"])
        used = set()
        for row in request["train"]:
            if (
                row["role"] != "train"
                or row["sha256"] in withheld
                or request["split"][row["original_id"]] != "train"
            ):
                raise ValueError("INT8 calibration crosses the declared training split")
            if row["sha256"] in used:
                continue
            source = Path(request["source_root"]) / row["file"].replace("\\", "/")
            raw = source.read_bytes()
            if (
                len(raw) != 512 * 288 * 3
                or hashlib.sha256(raw).hexdigest() != row["sha256"]
            ):
                raise ValueError(
                    "Native calibration RGB hash/geometry differs: " + row["id"]
                )
            image = np.frombuffer(raw, np.uint8).reshape(288, 512, 3)
            target = calibration / (row["id"] + ".bmp")
            if not cv2.imwrite(str(target), np.ascontiguousarray(image[:, :, ::-1])):
                raise RuntimeError("Cannot write calibration BMP")
            encoded = target.read_bytes()
            if (
                encoded[:2] != b"BM"
                or struct.unpack_from("<H", encoded, 28)[0] != 24
                or struct.unpack_from("<I", encoded, 30)[0] != 0
            ):
                raise ValueError("Calibration requires uncompressed BMP24")
            if cv2.imread(str(target), cv2.IMREAD_COLOR)[:, :, ::-1].tobytes() != raw:
                raise ValueError("Calibration RGB pixels/channel order changed")
            rows.append(
                dict(
                    id=row["id"],
                    source=row["file"],
                    rgb_sha256=row["sha256"],
                    source_frame_id=row["source_frame_id"],
                    source_group=row["source_group"],
                    bitmap=str(target),
                    bitmap_sha256=hashlib.sha256(encoded).hexdigest(),
                )
            )
            used.add(row["sha256"])
        if not rows:
            raise ValueError("No verified training crops for INT8 calibration")
        (root / "calibration_dataset.txt").write_text(
            "".join(row["bitmap"] + "\n" for row in rows), encoding="utf-8"
        )
    stamp = lambda: datetime.now(timezone(timedelta(hours=8))).isoformat()
    record = dict(
        training_started_beijing=request["training_started_beijing"],
        training_completed_beijing=request["training_completed_beijing"],
        source_hashes=request["source_hashes"],
        precision=args.precision,
        toolkit_version=importlib.metadata.version("rknn-toolkit2"),
        settings=settings,
        conversion_started_beijing=stamp(),
        calibration=rows,
        calibration_images=len(rows),
        calibration_split="train_only" if rows else "none",
        pixel_policy="native RGB, uncompressed BMP24 verified byte-for-byte; no resize/augmentation",
        board_contacted=False,
        completed=False,
        artifacts={},
    )
    manifest = root / "quantization_manifest.json"
    manifest.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    targets = (
        [("detector", "int8")] if args.precision == "int8" else [("detector", "fp16")]
    )
    if args.precision in ("fp16", "both") and (root / "locator.onnx").exists():
        targets.append(("locator", "fp16"))
    if args.precision == "both":
        targets.append(("detector", "int8"))
    for stem, precision in targets:
        engine = RKNN(verbose=False)
        try:
            if engine.config(**settings) or engine.load_onnx(
                model=str(root / (stem + ".onnx"))
            ):
                raise RuntimeError("RKNN import failed: " + stem)
            build = dict(do_quantization=precision == "int8")
            if precision == "int8":
                build.update(
                    dataset=str(root / "calibration_dataset.txt"), auto_hybrid=False
                )
            if engine.build(**build):
                raise RuntimeError("RKNN build failed: " + stem + " " + precision)
            target = root / (stem + "_" + precision + ".rknn")
            if engine.export_rknn(str(target)):
                raise RuntimeError("RKNN export failed: " + target.name)
        finally:
            engine.release()
        raw = target.read_bytes()
        if raw[:4] != b"RKNN":
            raise ValueError("Export is not an RKNN artifact")
        record["artifacts"][target.name] = dict(
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            source_onnx_sha256=request["source_hashes"][stem + ".onnx"],
        )
    record.update(
        completed=True,
        conversion_completed_beijing=stamp(),
        quantization=dict(
            format="fp16" if args.precision == "fp16" else "w8a8",
            preferred_model=(
                "detector_fp16.rknn"
                if args.precision == "fp16"
                else "detector_int8.rknn"
            ),
            calibration_split=record["calibration_split"],
            calibration_images=len(rows),
            manifest="quantization_manifest.json",
        ),
    )
    manifest.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("RKNN_CONVERSION_COMPLETE " + json.dumps(record["quantization"]), flush=True)


if __name__ == "__main__":
    main()
