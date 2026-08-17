import ast
import re

source = """
class MantaSource(FuenteBaseSource):
    pass

SOURCE = MantaSource
"""

extension = {
    "id": "manta_es",
    "name": "Manta",
    "base_url": "https://manta.net/es",
    "language": "es",
    "rpm": 60,
    "content_warning": "MIXED"
}

def inject_config(source: str, extension: dict) -> str:
    match = re.search(r"^SOURCE = (\w+)\s*$", source, re.M)
    if not match:
        return source
    source_cls = match.group(1)
    
    config = (
        f"\n\nclass Generated{source_cls}({source_cls}):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
    )
    if "rpm" in extension:
        config += f"    requests_per_minute = {extension['rpm']!r}\n"
    if "content_warning" in extension:
        config += f"    content_warning = {extension['content_warning']!r}\n"
    config += f"\nSOURCE = Generated{source_cls}\n"
    
    # Remove old SOURCE
    source = re.sub(r"^SOURCE = " + source_cls + r"\s*$", "", source, flags=re.M)
    return source + config

print(inject_config(source, extension))
