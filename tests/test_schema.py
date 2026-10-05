import unittest

from rulegrow.config import Config
from rulegrow.lint.schema import validate_candidate


def make_candidate(name="greetings.py", manifest=None, content="def handle(text: str) -> str | bool:\n    return False\n"):
    return {
        "manifest": manifest if manifest is not None else [name],
        "files": [{"name": name, "content": content}],
    }


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def test_valid(self):
        diag, files = validate_candidate(make_candidate(), self.cfg)
        self.assertTrue(diag.ok, diag.render())
        self.assertIn("greetings.py", files)

    def test_not_dict(self):
        diag, _ = validate_candidate("nope", self.cfg)
        self.assertFalse(diag.ok)

    def test_bad_filename(self):
        diag, _ = validate_candidate(make_candidate(name="../escape.py"), self.cfg)
        self.assertFalse(diag.ok)
        self.assertIn("CODE015", {i.code for i in diag.issues})

    def test_missing_manifest_ref(self):
        diag, _ = validate_candidate(make_candidate(manifest=["other.py"]), self.cfg)
        self.assertIn("SCHEMA006", {i.code for i in diag.issues})

    def test_unregistered_file(self):
        candidate = make_candidate()
        candidate["files"].append({"name": "extra.py", "content": "x"})
        diag, _ = validate_candidate(candidate, self.cfg)
        self.assertIn("SCHEMA007", {i.code for i in diag.issues})

    def test_duplicate_manifest(self):
        diag, _ = validate_candidate(make_candidate(manifest=["greetings.py", "greetings.py"]), self.cfg)
        self.assertIn("SCHEMA005", {i.code for i in diag.issues})


if __name__ == "__main__":
    unittest.main()
