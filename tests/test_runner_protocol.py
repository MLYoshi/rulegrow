"""Tests for the in-container executor's protocol logic (run on the host;
the same fixed file ships inside the runner image)."""

import json
import sys
import unittest
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parents[1] / "runner"
sys.path.insert(0, str(RUNNER_DIR))

import entrypoint  # noqa: E402


class CheckReturnTests(unittest.TestCase):
    def test_hit(self):
        status, reply = entrypoint.check_return("你好！")
        self.assertEqual((status, reply), ("hit", "你好！"))

    def test_miss_only_exact_false(self):
        self.assertEqual(entrypoint.check_return(False), ("miss", False))
        self.assertEqual(entrypoint.check_return(0)[0], "violation")

    def test_violations(self):
        self.assertEqual(entrypoint.check_return(True)[0], "violation")
        self.assertEqual(entrypoint.check_return(None)[0], "violation")
        self.assertEqual(entrypoint.check_return("")[0], "violation")
        self.assertEqual(entrypoint.check_return("   ")[0], "violation")
        self.assertEqual(entrypoint.check_return(123)[0], "violation")
        self.assertEqual(entrypoint.check_return(["x"])[0], "violation")

    def test_oversize_reply(self):
        status, _ = entrypoint.check_return("x" * 5000)
        self.assertEqual(status, "violation")


GOOD_RULES = {
    "greetings.py": (
        "def handle(text: str) -> str | bool:\n"
        "    if text in (\"你好\",):\n"
        "        return \"你好！有什么我可以帮你的？\"\n"
        "    return False\n"
    ),
    "emotions.py": (
        "def handle(text: str) -> str | bool:\n"
        "    if text.startswith(\"难过\"):\n"
        "        return \"听起来你不太好受。\"\n"
        "    return False\n"
    ),
}


class DispatchTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        rules_dir = Path(self.tmp.name) / "rules"
        rules_dir.mkdir()
        for name, src in GOOD_RULES.items():
            (rules_dir / name).write_text(src, encoding="utf-8")
        (rules_dir / "manifest.json").write_text(
            json.dumps({"modules": list(GOOD_RULES)}), encoding="utf-8")
        self.modules = entrypoint.load_modules(rules_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dispatch_hit(self):
        outcome = entrypoint.dispatch(self.modules, "你好")
        self.assertEqual(outcome["status"], "hit")
        self.assertEqual(outcome["reply"], "你好！有什么我可以帮你的？")

    def test_dispatch_miss(self):
        self.assertEqual(entrypoint.dispatch(self.modules, "再见")["status"], "miss")

    def test_dispatch_order(self):
        self.assertEqual(entrypoint.dispatch(self.modules, "难过的一天")["module"], "emotions.py")

    def test_load_only_latest_module(self):
        # latest-module policy: only the specified module is loaded/executed
        modules = entrypoint.load_modules(Path(self.tmp.name) / "rules", only="emotions.py")
        self.assertEqual([name for name, _ in modules], ["emotions.py"])
        self.assertEqual(entrypoint.dispatch(modules, "你好")["status"], "miss")

    def test_load_only_missing_module_raises(self):
        with self.assertRaises(RuntimeError):
            entrypoint.load_modules(Path(self.tmp.name) / "rules", only="nope.py")

    def test_string_false_is_a_reply(self):
        rules_dir = Path(self.tmp.name) / "rules"
        (rules_dir / "literal.py").write_text(
            "def handle(text: str) -> str | bool:\n"
            "    if text == \"magic\":\n"
            "        return \"false\"\n"
            "    return False\n", encoding="utf-8")
        (rules_dir / "manifest.json").write_text(
            json.dumps({"modules": ["literal.py"]}), encoding="utf-8")
        modules = entrypoint.load_modules(rules_dir)
        outcome = entrypoint.dispatch(modules, "magic")
        self.assertEqual(outcome["status"], "hit")      # "false" string is a normal reply
        self.assertEqual(outcome["reply"], "false")


if __name__ == "__main__":
    unittest.main()
