import unittest

from rulegrow.providers.openrouter import OpenRouterProvider
from rulegrow.validation.dialogue_quality import evaluate_reply


class QualityTests(unittest.TestCase):
    def test_good_reply(self):
        diag = evaluate_reply("你好", "你好！有什么我可以帮你的？")
        self.assertTrue(diag.ok, diag.render())

    def test_too_short(self):
        self.assertFalse(evaluate_reply("你好", "嗯").ok)

    def test_echo(self):
        self.assertFalse(evaluate_reply("你好", "你好").ok)

    def test_unrelated_short_reply(self):
        diag = evaluate_reply("难过", "今天天气不错哦")
        self.assertFalse(diag.ok)

    def test_long_related_reply_passes(self):
        diag = evaluate_reply(
            "我今天有点难过",
            "听起来你今天不太好受。如果你愿意，可以说说发生了什么，我会认真听。",
        )
        self.assertTrue(diag.ok, diag.render())

    def test_reviewer_invoked_when_provided(self):
        calls = []

        def reviewer(user_input, reply):
            calls.append(user_input)
            return True, "ok"

        diag = evaluate_reply("你好呀朋友", "你好呀朋友，很高兴见到你，有什么想聊的吗？", reviewer)
        self.assertTrue(diag.ok)
        self.assertEqual(calls, ["你好呀朋友"])


class JsonExtractionTests(unittest.TestCase):
    def test_plain(self):
        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        self.assertEqual(provider._extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        self.assertEqual(provider._extract_json('```json\n{"a": 2}\n```'), {"a": 2})

    def test_embedded(self):
        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        text = '好的，以下是结果：\n{"manifest": [], "files": []}\n希望有帮助'
        self.assertEqual(provider._extract_json(text), {"manifest": [], "files": []})

    def test_nested_braces_in_strings(self):
        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        text = '{"content": "def handle(text: str) -> str | bool:\\n    return False\\n"}'
        self.assertEqual(provider._extract_json(text)["content"], "def handle(text: str) -> str | bool:\n    return False\n")


if __name__ == "__main__":
    unittest.main()
