try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass



class SamuraiScanSource(MadaraSource):
    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("follow_redirects", True)
        return await super()._request(method, url, **kwargs)
class GeneratedMadaraSource(SamuraiScanSource):
    name = 'manhuaonline_es'
    display_name = 'SamuraiScan'
    base_url = 'https://samurai.j5z.xyz'
    language = 'es'
    manga_substring = 'leer'
    load_more = 'never'
    use_new_chapter_endpoint = True
    chapter_url_suffix = '?style=list'
    supports_latest = True
    requests_per_minute = 180
    pages_profile = 'default'
    extra_headers = {}
    image_headers = {}
    date_format = 'dd MMMM, yyyy'
    date_locale = 'es'
    details_profile = 'default'
    content_warning = 'safe'

SOURCE = GeneratedMadaraSource
