from __future__ import annotations


class MadaraDetailsSource :
    pass 


class ShadowmangaSource (MadaraDetailsSource ):
    """Si un CDN falla se prueba el otro y, en ultima instancia, /api/media del sitio."""

    def __init__ (self ,fetcher :SourceFetcher |None =None )->None :
        super ().__init__ (fetcher )
        self ._genres :list [str ]|None =None 
        self ._genre_attempts =0 

    async def get_filters (self )->list [SourceFilter ]:
        if self ._genres is None and self ._genre_attempts <3 :
            self ._genre_attempts +=1 
            try :
                response =await self ._request ("GET",f"{self .base_url }/api/series-locales/tags")
                response .raise_for_status ()
                self ._genres =[str (value )for value in response .json ()or []]
            except Exception :
                pass 
        if not self ._genres :
            return []
        return [
        SourceFilter (
        "tags","Géneros","tri_state",[(value ,value )for value in self ._genres ],[],
        )
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind =="popular":
            payload =await self ._get (f"{self .base_url }/api/series-locales/popular",{})
        elif kind =="latest":
            payload =await self ._get (f"{self .base_url }/api/series-locales/novedades",{})
        else :
            return {"items":[],"has_more":False }
        entries :list [dict ]=[]
        for wrapper in payload or []:
            if isinstance (wrapper ,dict ):
                entries .extend (
                item for item in wrapper .get ("series")or []if isinstance (item ,dict )
                )
        unique =list ({str (item .get ("id")):item for item in entries }.values ())
        return {"items":[self ._series (item )for item in unique ],"has_more":False }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        chosen =(filters or {}).get ("tags")
        chosen =chosen if isinstance (chosen ,dict )else {}
        params :list [tuple [str ,str ]]=[
        ("q",query ),
        ("includeAdult","true"),
        ("showSinPortada","false"),
        ("take",str (_SHADOW_MAX_RESULTS )),
        ]
        params .extend (
        ("tags",tag )for tag ,state in chosen .items ()if state =="include"
        )
        excluded ={tag for tag ,state in chosen .items ()if state =="exclude"}
        payload =await self ._get (
        f"{self .base_url }/api/series-locales/search-candidates",params ,
        )
        entries =[
        item 
        for item in payload or []
        if isinstance (item ,dict )
        and not (excluded and excluded &set (self ._genre_list (item )))
        ]
        entries .sort (key =lambda item :str (item .get ("titulo")or ""))
        return {"items":[self ._series (item )for item in entries ],"has_more":False }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        item =await self ._get (f"{self .base_url }/api/series-locales/{series_id }",{})
        return SourceSeries (
        source_id =series_id ,
        title =str (item .get ("titulo")or ""),
        source_name =self .name ,
        cover_url =item .get ("portadaUrl")or None ,
        description =str (item .get ("descripcion")or "")or None ,
        author =str (item .get ("autor")or "")or None ,
        status =_SHADOW_STATUS .get (str (item .get ("estado")or "").casefold ()),
        content_tags =tuple (self ._genre_list (item )),
        web_url =f"{self .base_url }/serie/local/{series_id }",
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        item =await self ._get (f"{self .base_url }/api/series-locales/{series_id }",{})
        identifier =item .get ("id",series_id )
        entries =[
        value for value in item .get ("capitulos")or []if isinstance (value ,dict )
        ]
        entries .sort (key =lambda value :float (value .get ("numeroCapitulo")or 0 ),reverse =True )
        result :list [SourceChapter ]=[]
        for value in entries :
            number =float (value .get ("numeroCapitulo")or 0 )
            label =str (number )
            label =label [:-2 ]if label .endswith (".0")else label 
            title =value .get ("titulo")
            result .append (
            SourceChapter (
            source_id =f"{identifier }/{value .get ('id')}",
            title =f"Cap. {label }"+(f" - {title }"if title else ""),
            series_id =series_id ,
            source_name =self .name ,
            number =number ,
            language =self .language ,
            uploaded_at =self ._date (value .get ("fechaSubida")),
            )
            )
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        series_id ,_ ,identifier =chapter_id .partition ("/")
        payload =await self ._get (
        f"{self .base_url }/api/series-locales/{series_id }/capitulos/{identifier }/paginas",{},
        )
        urls =[str (value )for value in (payload or {}).get ("paginas")or []]
        return [
        SourcePage (
        source_id =value ,
        chapter_id =chapter_id ,
        index =index ,
        filename =urlparse (value ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",
        source_name =self .name ,
        )
        for index ,value in enumerate (urls )
        ]

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else str (page )
        parsed =urlparse (url )
        if parsed .hostname not in _SHADOW_CDN_HOSTS :
            return await super ().page_bytes (page )
        for candidate in self ._mirrors (parsed ):
            try :
                return await super ().page_bytes (candidate )
            except Exception :
                continue 
        raise SourceNotFoundError (f"{self .display_name }: la imagen no responde en ningun CDN")

    def _mirrors (self ,parsed :Any )->list [str ]:
        result =[urlunparse (parsed )]
        result .extend (
        urlunparse (parsed ._replace (netloc =host ))
        for host in _SHADOW_CDN_HOSTS 
        if host !=parsed .hostname 
        )
        path =parsed .path 
        key =(
        path [len (_SHADOW_FALLBACK_PREFIX ):]
        if path .startswith (_SHADOW_FALLBACK_PREFIX )
        else path .lstrip ("/")
        )
        if key :
            host =urlparse (self .base_url ).netloc 
            result .append (
            urlunparse (
            parsed ._replace (
            scheme ="https",netloc =host ,path =f"{_SHADOW_FALLBACK_PREFIX }{key }",
            ),
            )
            )
        return result 

    async def _get (self ,url :str ,params :Any )->Any :
        response =await self ._request ("GET",url ,params =params )
        response .raise_for_status ()
        return response .json ()

    def _series (self ,item :dict )->SourceSeries :
        return SourceSeries (
        source_id =str (item .get ("id")),
        title =str (item .get ("titulo")or ""),
        source_name =self .name ,
        cover_url =item .get ("portadaUrl")or None ,
        web_url =f"{self .base_url }/serie/local/{item .get ('id')}",
        )

    @staticmethod 
    def _genre_list (item :dict )->list [str ]:
        return [
        value 
        for part in str (item .get ("generos")or "").split (",")
        if (value :=part .strip ())
        ]

    @staticmethod 
    def _date (value :Any )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        try :
            return datetime .strptime (str (value ),"%Y-%m-%dT%H:%M:%S.%f").replace (
            microsecond =0 ,
            ).isoformat ()
        except ValueError :
            return None 




SOURCE =ShadowmangaSource

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
