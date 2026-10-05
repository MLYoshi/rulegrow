import unittest

from rulegrow.lint.ast_rules import lint_rule_source

GOOD = '''def handle(text: str) -> str | bool:
    """Greeting rules."""
    lowered = text.strip().lower()
    if lowered in ("你好", "hello"):
        return "你好！有什么我可以帮你的？"
    if text.startswith("早上好"):
        return "早上好！新的一天加油。"
    return False
'''


def codes(source: str) -> set[str]:
    diag = lint_rule_source(source, "x.py", 200)
    return {i.code for i in diag.issues}


class LintGoodTest(unittest.TestCase):
    def test_good_passes(self):
        diag = lint_rule_source(GOOD, "greetings.py", 200)
        self.assertTrue(diag.ok, diag.render())


class LintViolationTests(unittest.TestCase):
    def test_two_functions(self):
        src = GOOD + "\n\ndef helper(x):\n    return x\n"
        self.assertIn("CODE001", codes(src))

    def test_wrong_signature(self):
        src = GOOD.replace("def handle(text: str) -> str | bool:", "def handle(msg: str) -> str:")
        self.assertIn("CODE002", codes(src))

    def test_helper_function(self):
        src = GOOD.replace("    lowered = text.strip().lower()",
                           "    def inner():\n        return 1\n    lowered = text.strip().lower()")
        self.assertIn("CODE003", codes(src))

    def test_lambda(self):
        src = GOOD.replace("lowered = text.strip().lower()",
                           "f = lambda t: t\n    lowered = text.strip().lower()")
        self.assertIn("CODE003", codes(src))

    def test_class(self):
        self.assertIn("CODE003", codes(GOOD + "\n\nclass A:\n    pass\n"))

    def test_too_many_lines(self):
        src = GOOD + "\n" + "\n".join(f"# line {i}" for i in range(200))
        diag = lint_rule_source(src, "x.py", 200)
        self.assertFalse(diag.ok)
        self.assertIn("CODE004", {i.code for i in diag.issues})

    def test_missing_final_return_false(self):
        src = GOOD.replace("    return False", "    return \"默认\"").replace(
            '        return "默认"', "        return False")
        # remove last return entirely
        lines = GOOD.splitlines()
        src = "\n".join(lines[:-1]) + "\n"
        self.assertIn("CODE005", codes(src))

    def test_return_true(self):
        src = GOOD.replace("    return False", "    return True")
        self.assertIn("CODE006", codes(src))

    def test_return_none(self):
        src = GOOD.replace("    return False", "    return")
        self.assertIn("CODE006", codes(src))

    def test_return_empty_string(self):
        src = GOOD.replace('        return "你好！有什么我可以帮你的？"', '        return "   "')
        self.assertIn("CODE007", codes(src))

    def test_unconditional_fallback(self):
        src = GOOD.replace("    return False", '    return "我是万能回复"\n    return False')
        self.assertIn("CODE008", codes(src))

    def test_loop(self):
        src = GOOD.replace("    return False", "    for i in range(3):\n        pass\n    return False")
        self.assertIn("CODE010", codes(src))

    def test_comprehension(self):
        src = GOOD.replace("lowered = text.strip().lower()", "xs = [c for c in text]")
        self.assertIn("CODE010", codes(src))

    def test_try(self):
        src = GOOD.replace("    return False", "    try:\n        pass\n    except Exception:\n        pass\n    return False")
        self.assertIn("CODE010", codes(src))

    def test_with(self):
        src = GOOD.replace("    return False", "    with open('f') as f:\n        pass\n    return False")
        self.assertIn("CODE010", codes(src))
        self.assertIn("CODE011", codes(src))

    def test_import(self):
        self.assertIn("CODE013", codes("import os\n" + GOOD))

    def test_fstring(self):
        src = GOOD.replace('        return "你好！有什么我可以帮你的？"', "        return f\"你好 {text}\"")
        self.assertIn("CODE009", codes(src))

    def test_open_call(self):
        src = GOOD.replace("    return False", "    open('/etc/passwd')\n    return False")
        self.assertIn("CODE011", codes(src))

    def test_eval(self):
        src = GOOD.replace("    return False", "    eval(text)\n    return False")
        self.assertIn("CODE012", codes(src))

    def test_unapproved_method(self):
        src = GOOD.replace("text.strip().lower()", "text.encode()")
        self.assertIn("CODE014", codes(src))

    def test_number_literal_return(self):
        src = GOOD.replace('        return "你好！有什么我可以帮你的？"', "        return 123")
        self.assertIn("CODE006", codes(src))  # int literal is allowed syntax, but not a valid return

    def test_subscript_allowed(self):
        src = GOOD.replace("lowered = text.strip().lower()", "lowered = text[0]")
        self.assertEqual(codes(src), set())

    def test_syntax_error(self):
        self.assertIn("CODE009", codes("def handle(:\n    pass"))


LIST_OPS = '''def handle(text: str) -> str | bool:
    """List operation rules."""
    keywords = ["你好", "hello", "hi"]
    parts = text.split()
    if len(parts) == 2 and parts[0] in keywords:
        return parts[1]
    if text in keywords:
        return "你好！有什么我可以帮你的？"
    if parts[-1] == "再见":
        return "再见，祝你有美好的一天。"
    joined = "、".join(parts)
    if joined == "早上好":
        return "早上好！新的一天加油。"
    keywords.append("早安")
    if text in keywords:
        return "早！"
    first = parts[0:1]
    if first and first[0] == "在吗":
        return "我在。"
    return False
'''


class LintListOpsTests(unittest.TestCase):
    def test_list_operations_pass(self):
        diag = lint_rule_source(LIST_OPS, "lists.py", 200)
        self.assertTrue(diag.ok, diag.render())

    def test_list_variable_membership_pass(self):
        src = GOOD.replace('if lowered in ("你好", "hello"):', 'if lowered in keywords:')
        src = src.replace('    lowered = text.strip().lower()',
                          '    lowered = text.strip().lower()\n    keywords = ["你好", "hello"]')
        self.assertEqual(codes(src), set())

    def test_loops_still_forbidden(self):
        src = LIST_OPS.replace("    return False", "    for k in keywords:\n        pass\n    return False")
        self.assertIn("CODE010", codes(src))

    def test_float_literal_forbidden(self):
        src = GOOD.replace("lowered = text.strip().lower()", "lowered = 1.5")
        self.assertIn("CODE009", codes(src))

    def test_dict_literal_forbidden(self):
        src = GOOD.replace("lowered = text.strip().lower()", "lowered = {\"a\": \"b\"}")
        self.assertIn("CODE009", codes(src))

    def test_unapproved_function_forbidden(self):
        src = GOOD.replace("lowered = text.strip().lower()", "lowered = sorted(text)")
        self.assertIn("CODE009", codes(src))


if __name__ == "__main__":
    unittest.main()
