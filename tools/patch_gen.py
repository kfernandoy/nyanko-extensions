import pathlib
import sys

t = pathlib.Path('tools/generate.py').read_text(encoding='utf-8')

original = """    replacements = {"true": "True", "false": "False", "null": "None"}
    source = tokenize.untokenize(
        (token.type, replacements.get(token.string, token.string))
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
    )
    return source.encode()"""

iny = """    replacements = {"true": "True", "false": "False", "null": "None"}
    source = tokenize.untokenize(
        (token.type, replacements.get(token.string, token.string))
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
    )

    if extension:
        import re
        match = re.search(r"^SOURCE = (\\w+)\\s*$", source, re.M)
        if match:
            source_cls = match.group(1)
            config = (
                f"\\n\\nclass Generated{source_cls}({source_cls}):\\n"
                f"    name = {extension['id']!r}\\n"
                f"    display_name = {extension['name']!r}\\n"
                f"    base_url = {extension['base_url']!r}\\n"
                f"    language = {extension['language']!r}\\n"
            )
            if "rpm" in extension:
                config += f"    requests_per_minute = {extension['rpm']!r}\\n"
            if "content_warning" in extension:
                config += f"    content_warning = {extension['content_warning']!r}\\n"
            config += f"\\nSOURCE = Generated{source_cls}\\n"
            source = re.sub(r"^SOURCE = " + source_cls + r"\\s*$", "", source, flags=re.M)
            source += config

    return source.encode("utf-8")"""

if original in t:
    t = t.replace(original, iny)
    pathlib.Path('tools/generate.py').write_text(t, encoding='utf-8')
    print("PATCH APLICADO!")
else:
    print("NO SE ENCONTRO EL BLOQUE ORIGINAL")
