
class FuenteBaseSource :
    pass 


class HentaienvySource (FuenteBaseSource ):
    manga_language =""
    profile =""

    def _series (self ,html :str ,base :str )->list [SourceSeries ]:
        root =_parse_html (html )
        classes ={"thumb","preview_item","gallery_item"}
        result :list [SourceSeries ]=[]
        for item in (
        node 
        for node in root .descendants ()
        if classes .intersection (node .attrs .get ("class","").split ())
        ):
            link =_first (item ,lambda node :node .tag =="a"and node .attrs .get ("href"))
            image =_first (item ,lambda node :node .tag =="img")
            caption =_first (
            item ,
            lambda node :any (
            name in node .attrs .get ("class","").split ()
            for name in ("caption","title","tag_name")
            )
            and node .text (),
            )
            title =caption .text ()if caption else link .text ()if link else ""
            if link and title :
                source_id =urljoin (base ,link .attrs ["href"])
                result .append (
                SourceSeries (
                source_id =source_id ,
                title =title ,
                source_name =self .name ,
                cover_url =_image_url (image ,base )if image else None ,
                web_url =source_id ,
                )
                )
        return list ({item .source_id :item for item in result }.values ())

    async def _catalog (self ,popular :bool ,page :int )->list [SourceSeries ]:
        path =self .base_url 
        if self .manga_language :
            path +=f"/language/{self .manga_language }"
        if popular :
            path +="/popular"
        response =await self ._request ("GET",path ,params ={"page":max (page ,1 )})
        response .raise_for_status ()
        return self ._series (response .text ,path )

    async def search (self ,query :str ,limit :int =20 )->list [SourceSeries ]:
        response =await self ._request (
        "GET",
        f"{self .base_url }/search/",
        params ={"q":query .strip (),"key":query .strip (),"page":1 },
        )
        response .raise_for_status ()
        return self ._series (response .text ,self .base_url )[:limit ]

    async def browse (self ,kind :str ,page :int =1 )->list [SourceSeries ]:
        if kind not in {"popular","latest"}:
            return []
        return await self ._catalog (kind =="popular",page )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else series 
        return [
        SourceChapter (
        source_id =series_id ,
        title ="Chapter",
        series_id =series_id ,
        source_name =self .name ,
        )
        ]

    @staticmethod 
    def _inputs (root )->dict [str ,str ]:
        return {
        node .attrs ["id"]:node .attrs .get ("value","")
        for node in root .descendants ("input")
        if node .attrs .get ("id")
        }

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else chapter 
        response =await self ._request ("GET",chapter_id )
        response .raise_for_status ()
        root =_parse_html (response .text )
        inputs =self ._inputs (root )
        scripts ="\n".join (node .text ()for node in root .descendants ("script"))
        encoded =re .search (r"\$\.parseJSON\('(.+?)'\)",scripts ,re .DOTALL )
        urls :list [str ]=[]
        if encoded :
            payload =json .loads (encoded .group (1 ).encode ().decode ("unicode_escape"))
            load_dir =inputs .get ("load_dir","").strip ("/")
            load_id =inputs .get ("load_id","")
            server_number =inputs .get ("load_server","")
            cover =_first (root ,lambda node :node .tag =="img"and any (name in node .attrs .get ("class","").split ()for name in ("cover","img-responsive")))
            server =(
            f"m{server_number }.{urlparse (self .base_url ).hostname }"
            if server_number 
            else urlparse (_image_url (cover ,chapter_id )).hostname if cover else urlparse (self .base_url ).hostname 
            )
            for key ,value in payload .items ():
                code =str (value ).split (",",1 )[0 ].strip ('"')
                extension ={"p":"png","b":"bmp","g":"gif","w":"webp"}.get (code ,"jpg")
                urls .append (f"https://{server }/{load_dir }/{load_id }/{key }.{extension }")
        else :
            for node in root .descendants ("img"):
                if node .parent and any (
                name in node .parent .attrs .get ("class","").split ()
                for name in ("gallery_thumb","preview_thumb")
                ):
                    url =_image_url (node ,chapter_id )
                    extension =url .rsplit (".",1 )[-1 ]
                    urls .append (url .replace (f"t.{extension }",f".{extension }"))
        return [
        SourcePage (
        source_id =url ,
        chapter_id =chapter_id ,
        index =index ,
        filename =url .rsplit ("/",1 )[-1 ].split ("?",1 )[0 ],
        source_name =self .name ,
        )
        for index ,url in enumerate (urls ,1 )
        ]

class GeneratedGalleryAdultsSource (GalleryAdultsSource ):

    def get_preferences (self )->list [SourcePreference ]:
    # Autogenerated via heuristic port
        data =[]
        return [SourcePreference (**item )for item in data ]

    def get_filters (self )->list [SourceFilter ]:
    # Autogenerated via heuristic port
        data =[]
        return [SourceFilter (**item )for item in data ]

    name ='hentaienvy_es'
    display_name ='HentaiEnvy (es)'
    base_url ='https://hentaienvy.com'
    language ='es'
    manga_language ='spanish'
    profile ='hentaienvy'


SOURCE =HentaienvySource

"""Puente de contrato para adaptadores que conservan metodos v3."""

import inspect
from collections.abc import Mapping
from typing import Any

from nyanko_api.sources.contract import Paginated, SourceFilter, SourcePreference

_PAGE_SIZE = 20


def _parameters(method: Any) -> Mapping[str, Any]:
    return inspect.signature(method).parameters


def _arguments(method: Any, page: int, filters: Mapping[str, Any] | None) -> dict[str, Any]:
    parameters = _parameters(method)
    arguments: dict[str, Any] = {}
    if "page" in parameters:
        arguments["page"] = page
    if "filters" in parameters:
        arguments["filters"] = filters
    if "limit" in parameters:
        # Un metodo v3 sin `page` solo se controla por `limit`: se pide el
        # acumulado hasta la pagina solicitada y luego se recorta el tramo. El
        # elemento extra es el sondeo que distingue "no hay mas" de "justo cabia".
        arguments["limit"] = _PAGE_SIZE if "page" in parameters else page * _PAGE_SIZE + 1
    return arguments


def _unwrap(value: Any) -> tuple[list[Any], bool | None]:
    """Normaliza un retorno v3 a ``(items, has_more)``; ``None`` si no lo declara."""
    if isinstance(value, Paginated):
        return list(value.items), value.has_more
    if isinstance(value, dict):
        declared = value.get("has_more", value.get("has_next_page"))
        items = value.get("items", value.get("results", []))
        return list(items or []), None if declared is None else bool(declared)
    return list(value or []), None


def _paginated(value: Any, has_more: bool) -> Paginated:
    items, declared = _unwrap(value)
    if declared is not None:
        has_more = declared
    return Paginated(items=items, has_more=has_more and bool(items))


def _window(value: Any, page: int) -> Paginated:
    """Pagina en el cliente un metodo v3 que devuelve el acumulado de una vez."""
    items, declared = _unwrap(value)
    start = (page - 1) * _PAGE_SIZE
    window = items[start : start + _PAGE_SIZE]
    has_more = len(items) > start + _PAGE_SIZE if declared is None else declared
    return Paginated(items=window, has_more=has_more and bool(window))


def _consumes_filters(legacy_source: type) -> bool:
    return any(
        "filters" in _parameters(method)
        for name in ("search", "browse")
        if callable(method := getattr(legacy_source, name, None))
    )


def _options(options: Any) -> list[tuple[str, str]] | None:
    if options is None:
        return None
    return [
        (str(option.get("value", "")), str(option.get("name", "")))
        if isinstance(option, dict)
        else (str(option[0]), str(option[1]))
        for option in options
    ]


def _filters(values: Any) -> list[SourceFilter]:
    return [
        SourceFilter(
            id=value.id,
            name=value.name,
            type="multi_select" if value.type == "group" else value.type,
            options=_options(value.options),
            default=[] if value.type == "group" and not isinstance(value.default, list) else value.default,
        )
        for value in values
    ]


def _preferences(values: Any) -> list[SourcePreference]:
    return [
        SourcePreference(
            id=value.id,
            name=value.name,
            type=value.type,
            options=_options(value.options),
            default=value.default,
        )
        for value in values
    ]


def adapt_source(legacy_source: type) -> type:
    # Un filtro que ningun metodo v3 acepta no se anuncia: la UI mostraria
    # controles que el adaptador descarta en silencio.
    publishes_filters = _consumes_filters(legacy_source)

    class SourceV4(legacy_source):
        async def get_filters(self) -> list[SourceFilter]:
            getter = getattr(super(), "get_filters", None)
            if not getter or not publishes_filters:
                return []
            values = getter()
            if inspect.isawaitable(values):
                values = await values
            return _filters(values)

        def get_preferences(self) -> list[SourcePreference]:
            getter = getattr(super(), "get_preferences", None)
            return _preferences(getter()) if getter else []

        async def search(
            self,
            query: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().search
            result = await method(query, **_arguments(method, page, filters))
            if "page" in _parameters(method):
                return _paginated(result, True)
            return _window(result, page)

        async def browse(
            self,
            kind: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().browse
            return _paginated(await method(kind, **_arguments(method, page, filters)), True)

    SourceV4.__name__ = legacy_source.__name__
    SourceV4.__qualname__ = legacy_source.__qualname__
    return SourceV4

SOURCE = adapt_source(SOURCE)
