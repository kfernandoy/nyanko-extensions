from __future__ import annotations

import unittest
from pathlib import Path

from tools.validate import _assert_v4, _local_file


class ValidateTest(unittest.TestCase):
    def test_raw_github_url_resolves_inside_repository(self):
        repo = Path("C:/repo")
        result = _local_file(
            repo,
            "https://raw.githubusercontent.com/user/repo/main/bundles/demo.py",
        )
        self.assertEqual(result, (repo / "bundles" / "demo.py").resolve())

    def test_v4_signature_is_checked(self):
        class InvalidSource:
            async def search(self, query, limit=20): ...

        with self.assertRaisesRegex(AssertionError, "firma search"):
            _assert_v4(InvalidSource(), "invalid")


if __name__ == "__main__":
    unittest.main()
