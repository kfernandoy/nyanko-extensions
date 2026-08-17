import pathlib

t = pathlib.Path('tools/generate.py').read_text(encoding='utf-8')

t = t.replace('match = re.search(r"^SOURCE = (\\w+)\\s*$", source, re.M)', 'match = re.search(r"^SOURCE\\s*=\\s*(\\w+)", source, re.M)')
t = t.replace('source = re.sub(r"^SOURCE = " + source_cls + r"\\s*$", "", source, flags=re.M)', 'source = re.sub(r"^SOURCE\\s*=\\s*" + source_cls, "", source, flags=re.M)')

pathlib.Path('tools/generate.py').write_text(t, encoding='utf-8')
