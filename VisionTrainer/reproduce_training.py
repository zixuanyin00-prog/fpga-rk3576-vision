from pathlib import Path
import argparse
import json
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("output")
    args = parser.parse_args()
    source = Path(args.job).resolve()
    out = Path(args.output).resolve()
    if out.exists():
        raise ValueError(
            "Reproduction output must be a new directory; originals are never overwritten"
        )
    job = json.loads(source.read_text(encoding="utf-8"))
    if (
        job.get("initialization") != "random"
        or job.get("initial") is not None
        or job.get("historical_replay") is not False
    ):
        raise ValueError(
            "This reproducer permits only the explicitly recorded random initialization"
        )
    out.mkdir(parents=True)
    job["output"] = str(out)
    job["reproduces_job"] = str(source)
    path = out / "job.json"
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    archived = source.parent / "training_source/worker.py"
    worker = archived if archived.exists() else Path(__file__).with_name("worker.py")
    subprocess.run(
        [sys.executable, "-X", "utf8", str(worker), "train", str(path)], check=True
    )


if __name__ == "__main__":
    main()
