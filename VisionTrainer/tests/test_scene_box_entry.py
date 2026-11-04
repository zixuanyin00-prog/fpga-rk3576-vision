from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store as db, box_assistant, scene_boxes
from service import Service


class SceneEntry(unittest.TestCase):
    def test_only_new_family_listed_and_legacy_selection_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            db, "DATA", Path(tmp) / "data"
        ), patch.object(box_assistant, "HOME", Path(tmp) / "helper"):
            db.DATA.mkdir()
            p = db.create("fixture")
            p["frames"] = [
                dict(
                    id="f",
                    batch="batch",
                    role="train",
                    origin=[0, 0],
                    original_size=[512, 288],
                )
            ]
            db.save(p)
            scene = scene_boxes.scene_identity(p, "batch")
            digest = hashlib.sha256(b"fixture").hexdigest()
            for run, enabled in [("old", False), ("new", True)]:
                folder = box_assistant.HOME / "runs" / run
                folder.mkdir(parents=True)
                (folder / "boxes.pt").write_bytes(b"fixture")
                db.write(
                    folder / "result.json",
                    dict(
                        created="2026-10-25T00:00:00+08:00",
                        sha256=digest,
                        architecture="scratch-box-fpn-verifier-v2",
                        scene=scene,
                        workflow_generation="scene-native-v3" if enabled else None,
                    ),
                )
            service = Service()
            state = service.status()["box_assistant"]
            self.assertEqual([c["run"] for c in state["candidates"]], ["new"])
            self.assertNotIn("provider", state)
            for args in [dict(provider="huggingface"), dict(run="old")]:
                with self.assertRaisesRegex(ValueError, "旧画框模型已从03移除"):
                    service.select_box(dict(project=p["id"], **args))
            selected = service.select_box(
                dict(
                    project=p["id"],
                    batch="batch",
                    run="new",
                    trained_at="2026-10-25T00:00:00+08:00",
                    sha256=digest,
                )
            )
            self.assertEqual(selected["run"], "new")
            with self.assertRaises(ValueError):
                service.select_box(
                    dict(
                        project=p["id"],
                        batch="other",
                        run="new",
                        trained_at="2026-10-25T00:00:00+08:00",
                        sha256=digest,
                    )
                )
            self.assertTrue((box_assistant.HOME / "runs/old/boxes.pt").exists())


if __name__ == "__main__":
    unittest.main()
