import pathlib

t = pathlib.Path('tools/generate.py').read_text(encoding='utf-8')
t = t.replace('if extension:', 'if extension:\n        if extension["id"] == "emperorscan_es": print("--- HIT EMPEROR ---")')
pathlib.Path('tools/generate.py').write_text(t, encoding='utf-8')
