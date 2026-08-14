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


SOURCE = SamuraiScanSource
