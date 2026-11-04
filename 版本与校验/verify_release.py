from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parents[1]
manifest = root / "版本与校验" / "文件清单.json"
records = json.loads(manifest.read_text(encoding="utf-8"))["files"]
failures = []
for record in records:
    path = root / record["path"]
    if (
        not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
    ):
        failures.append(record["path"])
if failures:
    raise SystemExit("SHA256 verification failed: " + ", ".join(failures))
print(f"SHA256 verified: {len(records)} files")
