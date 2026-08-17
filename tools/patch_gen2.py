import pathlib
import sys

t = pathlib.Path('tools/generate.py').read_text(encoding='utf-8')

original = """    if extension:
        import re
        match = re.search(r"^SOURCE = (\\w+)\\s*$", source, re.M)"""

iny = """    if extension:
        match = re.search(r"^SOURCE = (\\w+)\\s*$", source, re.M)"""

t = t.replace(original, iny)
pathlib.Path('tools/generate.py').write_text(t, encoding='utf-8')
