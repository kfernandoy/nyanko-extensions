from __future__ import annotations

import unittest

from tools.generate import (
    _disambiguate_names,
    _expand_source,
    _refrescar_motor_en_manual,
)


class GenerateTest(unittest.TestCase):
    def test_source_expansion_resolves_kotlin_it(self):
        source = 'lang = it\nbaseUrl = "https://example.com/$it/${it}"'

        self.assertEqual(
            _expand_source(source, "fr"),
            'lang = "fr"\nbaseUrl = "https://example.com/fr/fr"',
        )

    def test_repeated_names_include_language(self):
        extensions = [
            {"id": "demo_en", "name": "Demo"},
            {"id": "demo_es", "name": "Demo"},
            {"id": "unique_fr", "name": "Unique"},
        ]
        _disambiguate_names(
            extensions,
            {"demo_en": "en", "demo_es": "es", "unique_fr": "fr"},
        )
        self.assertEqual(
            [item["name"] for item in extensions],
            ["Demo (en)", "Demo (es)", "Unique"],
        )

    def test_manual_refresh_keeps_helpers_constants_and_imports(self):
        engine = '''\
import hashlib

class _Node:
    pass

class MadaraSource:
    async def chapters(self):
        return hashlib.md5(b"x").digest()
'''
        manual = '''\
import re

class _Node:
    pass

class MadaraSource:
    async def chapters(self):
        return []

import math
_CATEGORIES = {"a": 1}

def _helper(value):
    return math.ceil(value)

class SiteSource(MadaraSource):
    categories = _CATEGORIES

SOURCE = SiteSource
'''

        refreshed = _refrescar_motor_en_manual(manual, engine)

        self.assertIn("import hashlib", refreshed)
        self.assertIn("import math", refreshed)
        self.assertIn('_CATEGORIES = {"a": 1}', refreshed)
        self.assertIn("def _helper(value):", refreshed)
        self.assertIn("class SiteSource(MadaraSource):", refreshed)
        self.assertEqual(refreshed.count("class MadaraSource:"), 1)
        compile(refreshed, "manual.py", "exec")

    def test_manual_refresh_keeps_suffix_after_madara_import_marker(self):
        engine = '''\
class _Node:
    pass

class MadaraSource:
    fixed = True
'''
        manual = '''\
class _Node:
    pass

class MadaraSource:
    fixed = False

import time

try:
    from .madara import MadaraSource
except ImportError:
    pass

class GenericSource(MadaraSource):
    now = time.time

SOURCE = GenericSource
'''

        refreshed = _refrescar_motor_en_manual(manual, engine)

        self.assertIn("fixed = True", refreshed)
        self.assertNotIn("fixed = False", refreshed)
        self.assertIn("import time", refreshed)
        self.assertIn("class GenericSource(MadaraSource):", refreshed)
        self.assertNotIn("from .madara import", refreshed)
        compile(refreshed, "manual.py", "exec")


if __name__ == "__main__":
    unittest.main()
