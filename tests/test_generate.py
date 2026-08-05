from __future__ import annotations

import unittest

from tools.generate import _disambiguate_names, _expand_source


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


if __name__ == "__main__":
    unittest.main()
