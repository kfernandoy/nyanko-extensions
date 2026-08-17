import pathlib

t = pathlib.Path('tools/generate.py').read_text(encoding='utf-8')
orig = '''            bundle_bytes = _manual_bundle(
                manual_path,
                madara_engine if engine_name in _MOTORES_SOBRE_MADARA else (
                    details_engine if engine_name in {"goda", "natsuid", "uzaymanga"} else base_engine
                ),
            )'''

nuevo = '''            bundle_bytes = _manual_bundle(
                manual_path,
                madara_engine if engine_name in _MOTORES_SOBRE_MADARA else (
                    details_engine if engine_name in {"goda", "natsuid", "uzaymanga"} else base_engine
                ),
                extension=extension,
            )'''

if orig in t:
    t = t.replace(orig, nuevo)
    pathlib.Path('tools/generate.py').write_text(t, encoding='utf-8')
    print("PATCH APLICADO!")
else:
    print("NO SE ENCONTRO BLOCK ORIGINAL PARA EL BUG")
