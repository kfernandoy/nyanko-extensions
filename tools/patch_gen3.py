import pathlib

t = pathlib.Path('tools/generate.py').read_text(encoding='utf-8')

original_calls = [
    'bundle_bytes = _manual_bundle(manual_path, mangadex_engine)',
    'bundle_bytes = _manual_bundle(manual_path, _combined)',
    'bundle_bytes = _manual_bundle(manual_path, madara_engine)',
    'bundle_bytes = _manual_bundle(manual_path, mangathemesia_engine)',
    'bundle_bytes = _manual_bundle(manual_path, base_engine)',
    'bundle_bytes = _manual_bundle(\n                        manual_path, base_engine, mmrcms_engine,\n                    )',
    'bundle_bytes = _manual_bundle(\n                        manual_path, base_engine, galleryadults_engine,\n                    )',
    'bundle_bytes = _manual_bundle(\n                        manual_path, base_engine, scanreader_engine,\n                    )',
]

for orig in original_calls:
    if '\n' in orig:
        parts = orig.split(',')
        new_call = parts[0] + ',' + parts[1] + ',' + parts[2].rstrip() + ', extension=extension\n                    )'
        t = t.replace(orig, new_call)
    else:
        new_call = orig.replace(')', ', extension=extension)')
        t = t.replace(orig, new_call)

pathlib.Path('tools/generate.py').write_text(t, encoding='utf-8')
