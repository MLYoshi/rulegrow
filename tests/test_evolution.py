"""Evolution pipeline tests with a stub provider. Uses the real Docker sandbox
when available; otherwise skipped (CI without Docker)."""

import unittest
from pathlib import Path

from rulegrow.config import Config
from rulegrow.evolution import EvolutionEngine
from rulegrow.providers.openrouter import ProviderError
from rulegrow.sandbox.docker_runner import DockerSandbox
from rulegrow.sandbox.protocol import SandboxError
from rulegrow.storage.versions import VersionStore

GOOD_CANDIDATE = {
    "manifest": ["greetings.py"],
    "files": [{
        "name": "greetings.py",
        "content": 'def handle(text: str) -> str | bool:\n'
                   '    if text.strip() in ("你好", "hi"):\n'
                   '        return "你好！有什么我可以帮你的？"\n'
                   '    return False\n',
    }],
}

BAD_CANDIDATE = {
    "manifest": ["broken.py"],
    "files": [{
        "name": "broken.py",
        "content": 'def handle(text: str) -> str | bool:\n'
                   '    return "我什么都能回答"\n\n'
                   'def helper():\n    return 1\n',
    }],
}


class FakeProvider:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.calls = []

    @property
    def ready(self):
        return True

    def generate_or_repair(self, files, manifest, user_input, diagnostics=None, latest_module=""):
        self.calls.append({"input": user_input, "diagnostics": diagnostics, "latest_module": latest_module})
        if not self.candidates:
            raise ProviderError("模型额度用尽（测试模拟）")
        return self.candidates.pop(0)


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(project_root=Path(self.tmp.name))
        self.cfg.max_auto_attempts = 4
        self.store = VersionStore(self.cfg)
        self.sandbox = DockerSandbox(self.cfg)
        try:
            self.sandbox.check_available()
        except SandboxError:
            self.skipTest("Docker not available")
        # build the runner image from the repo root if missing (temp dirs have no Dockerfile)
        DockerSandbox(Config(project_root=Path(__file__).resolve().parents[1])).ensure_image()

    def tearDown(self):
        self.tmp.cleanup()

    def test_repair_then_publish(self):
        provider = FakeProvider([BAD_CANDIDATE, GOOD_CANDIDATE])
        engine = EvolutionEngine(self.cfg, self.store, self.sandbox, provider)
        messages = []
        outcome = engine.evolve("你好", progress=messages.append)

        self.assertEqual(outcome.status, "published")
        self.assertEqual(outcome.version, 1)
        self.assertEqual(outcome.reply, "你好！有什么我可以帮你的？")
        # repair call received structured diagnostics from the lint failure
        self.assertIsNone(provider.calls[0]["diagnostics"])
        self.assertIsNotNone(provider.calls[1]["diagnostics"])
        self.assertIn("CODE", provider.calls[1]["diagnostics"][0])
        # regression case recorded for the accepted reply
        self.assertEqual(self.store.load_regressions()[0]["input"], "你好")
        # job completed
        job = self.store.load_job(outcome.job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["published_version"], 1)

    def test_budget_pause(self):
        provider = FakeProvider([])  # always raises ProviderError
        engine = EvolutionEngine(self.cfg, self.store, self.sandbox, provider)
        outcome = engine.evolve("在吗")
        self.assertEqual(outcome.status, "paused")
        self.assertIn("上限", outcome.message)
        # old version (none) untouched; job saved as paused and resumable
        job = self.store.load_job(outcome.job_id)
        self.assertEqual(job["status"], "paused")
        self.assertIn("pending_diagnostics", job)

    def test_guard_failure_blocks_publish(self):
        # candidate that answers everything, including guard inputs
        greedy = {
            "manifest": ["greedy.py"],
            "files": [{
                "name": "greedy.py",
                "content": 'def handle(text: str) -> str | bool:\n'
                           '    if text.strip() != "":\n'
                           '        return "我在听，请继续说。"\n'
                           '    return False\n',
            }],
        }
        chat_candidate = {
            "manifest": ["chat.py"],
            "files": [{
                "name": "chat.py",
                "content": 'def handle(text: str) -> str | bool:\n'
                           '    if "陪我聊" in text:\n'
                           '        return "好呀，你想聊点什么？"\n'
                           '    return False\n',
            }],
        }
        provider = FakeProvider([greedy, chat_candidate])
        engine = EvolutionEngine(self.cfg, self.store, self.sandbox, provider)
        outcome = engine.evolve("陪我聊聊天")
        self.assertEqual(outcome.status, "published")
        # the repair call must have received structured guard diagnostics
        repair_diags = provider.calls[1]["diagnostics"]
        self.assertTrue(repair_diags)
        self.assertIn("守卫", repair_diags[0])


if __name__ == "__main__":
    unittest.main()
