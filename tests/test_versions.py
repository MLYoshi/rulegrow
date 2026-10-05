import json
import unittest
from pathlib import Path

from rulegrow.config import Config
from rulegrow.storage.versions import VersionStore

RULES = {
    "greetings.py": "def handle(text: str) -> str | bool:\n"
                    "    if text.strip() in (\"你好\",):\n"
                    "        return \"你好！\"\n"
                    "    return False\n",
}


class VersionStoreTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(project_root=Path(self.tmp.name))
        self.store = VersionStore(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_and_current(self):
        version = self.store.publish(dict(RULES), ["greetings.py"], {"model": "test"})
        self.assertEqual(version, 1)
        self.assertEqual(self.store.current_version(), 1)
        self.assertEqual(self.store.load_rules(1), RULES)
        self.assertEqual(self.store.load_manifest(1), ["greetings.py"])

    def test_second_publish(self):
        self.store.publish(dict(RULES), ["greetings.py"], {})
        rules2 = dict(RULES)
        rules2["greetings.py"] += '# extra\n'
        v2 = self.store.publish(rules2, ["greetings.py"], {})
        self.assertEqual(v2, 2)
        self.assertEqual(self.store.current_version(), 2)

    def test_rollback(self):
        self.store.publish(dict(RULES), ["greetings.py"], {})
        self.store.publish(dict(RULES), ["greetings.py"], {})
        self.store.rollback(1)
        self.assertEqual(self.store.current_version(), 1)

    def test_diff(self):
        self.store.publish(dict(RULES), ["greetings.py"], {})
        rules2 = dict(RULES)
        rules2["emotions.py"] = "def handle(text: str) -> str | bool:\n    return False\n"
        self.store.publish(rules2, ["greetings.py", "emotions.py"], {})
        diff = self.store.diff_versions(1, 2)
        self.assertTrue(any("emotions.py" in line for line in diff))

    def test_regressions_and_guards(self):
        self.assertEqual(self.store.load_guards(), self.store.load_guards())  # seeds on first read
        self.assertTrue(self.store.load_guards())
        self.store.add_regression("你好", "你好！")
        self.assertEqual(self.store.load_regressions()[0]["input"], "你好")
        self.store.add_regression("你好", "重复不添加")
        self.assertEqual(len(self.store.load_regressions()), 1)

    def test_jobs(self):
        job = self.store.create_job("在吗")
        self.assertEqual(job["status"], "running")
        job["status"] = "paused"
        self.store.save_job(job)
        loaded = self.store.load_job(job["id"])
        self.assertEqual(loaded["status"], "paused")
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_path_containment_on_write(self):
        with self.assertRaises(ValueError):
            self.store.stage_snapshot(Path(self.tmp.name) / "snap", {"../evil.py": "x"}, [])


if __name__ == "__main__":
    unittest.main()
