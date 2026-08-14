from __future__ import annotations


class MadaraDetailsSource :
    pass 


class OlympusscanlationSource (MadaraDetailsSource ):
    """El slug de cada serie no viaja en la ficha: se aprende del listado."""

    fetch_domain =True 

    def __init__ (self ,fetcher :SourceFetcher |None =None )->None :
        super ().__init__ (fetcher )
        self ._series_cache :list [dict ]=[]
        self ._series_at =0.0 
        self ._slugs :dict [int ,str ]={}
        self ._domain_checked =False 

    @property 
    def api_url (self )->str :
        return self .base_url .replace ("https://","https://panel.")

    def get_preferences (self )->list [SourcePreference ]:
        return [
        SourcePreference (
        "fetchDomain","Buscar dominio automáticamente","checkbox",default =True ,
        )
        ]

    def get_filters (self )->list [SourceFilter ]:
        return []

    async def browse (self ,kind :str ,page :int =1 ):
        await self ._ensure_series ()
        if kind =="popular":
            payload =await self ._get (f"{self .base_url }/api/rankings",{
            "page":str (page ),"period":"total_ranking",
            })
        elif kind =="latest":
            payload =await self ._get (f"{self .base_url }/api/new-chapters",{"page":str (page )})
        else :
            return {"items":[],"has_more":False }
        items =[
        self ._series (item )
        for item in payload .get ("data")or []
        if isinstance (item ,dict )and item .get ("type")=="comic"
        ]
        return {
        "items":items ,
        "has_more":int (payload .get ("current_page")or 0 )<int (payload .get ("last_page")or 0 ),
        }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
    # No hay endpoint de busqueda: se filtra el listado completo cacheado.
        await self ._ensure_series ()
        needle =query .strip ().casefold ()
        matches =[
        item for item in self ._series_cache 
        if needle in str (item .get ("name")or "").casefold ()
        ]
        start =(page -1 )*_OLYMPUS_PAGE 
        return {
        "items":[self ._series (item )for item in matches [start :start +_OLYMPUS_PAGE ]],
        "has_more":page *_OLYMPUS_PAGE <len (matches ),
        }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        slug =await self ._slug (series_id )
        payload =await self ._get (f"{self .base_url }/api/series/{slug }",{"type":"comic"})
        data =payload .get ("data")or {}
        return SourceSeries (
        source_id =str (data .get ("id")or series_id ),
        title =str (data .get ("name")or ""),
        source_name =self .name ,
        cover_url =data .get ("cover")or None ,
        description =str (data .get ("summary")or "")or None ,
        status =_OLYMPUS_STATUS .get (int ((data .get ("status")or {}).get ("id")or 0 )),
        content_tags =tuple (
        str (genre .get ("name")or "").strip ()
        for genre in data .get ("genres")or []
        if isinstance (genre ,dict )
        ),
        web_url =f"{self .base_url }/series/comic-{slug }",
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        slug =await self ._slug (series_id )
        entries :list [dict ]=[]
        total ,page =None ,1 
        while True :
            payload =await self ._get (f"{self .api_url }/api/series/{slug }/chapters",{
            "page":str (page ),"direction":"desc","type":"comic",
            })
            batch =[item for item in payload .get ("data")or []if isinstance (item ,dict )]
            entries .extend (batch )
            total =int ((payload .get ("meta")or {}).get ("total")or 0 )if total is None else total 
            if not batch or len (entries )>=total :
                break 
            page +=1 
        return [
        SourceChapter (
        source_id =f"{series_id }/{item .get ('id')}",
        title =f"Capitulo {item .get ('name')}",
        series_id =series_id ,
        source_name =self .name ,
        number =self ._float (item .get ("name")),
        language =self .language ,
        uploaded_at =self ._date (item .get ("published_at")),
        )
        for item in entries 
        ]

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        series_id ,_ ,identifier =chapter_id .partition ("/")
        slug =await self ._slug (series_id )
        payload =await self ._get (f"{self .base_url }/api/capitulo/comic-{slug }/{identifier }",{})
        urls =[str (value )for value in (payload .get ("chapter")or {}).get ("pages")or []]
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

        # -------------------------------------------------------------- internals
    async def _ensure_domain (self )->None :
        if self ._domain_checked or not self .fetch_domain :
            return 
        self ._domain_checked =True 
        try :
            response =await self ._request ("GET",_OLYMPUS_DIRECTORY )
            response .raise_for_status ()
            root =_parse_html (response .text )
            meta =_first (
            root ,
            lambda node :node .tag =="meta"and node .attrs .get ("property")=="og:url",
            )
            target =meta .attrs .get ("content","")if meta is not None else ""
            if not target :
                return 
            resolved =await self ._request ("GET",target )
            host =urlparse (str (resolved .url )or target ).netloc 
            if host :
            # El dominio cambia a menudo; la app no persiste, se usa por sesion.
                self .base_url =f"https://{host }"
        except Exception :
            return 

    async def _ensure_series (self )->None :
        import time 

        await self ._ensure_domain ()
        now =time .time ()
        if self ._series_cache and now -self ._series_at <_OLYMPUS_CACHE_SECONDS :
            return 
        payload =await self ._get (f"{self .base_url }/api/series/list",{})
        comics =[
        item 
        for item in payload .get ("data")or []
        if isinstance (item ,dict )and item .get ("type")=="comic"
        ]
        self ._series_cache =comics 
        self ._series_at =now 
        self ._slugs .update (
        {int (item ["id"]):str (item .get ("slug")or "")for item in comics if item .get ("id")is not None }
        )

    async def _slug (self ,series_id :str )->str :
        await self ._ensure_series ()
        try :
            key =int (series_id )
        except (TypeError ,ValueError ):
            return series_id 
        slug =self ._slugs .get (key )
        if not slug :
            raise SourceNotFoundError (f"{self .display_name }: serie {series_id } sin slug conocido")
        return slug 

    async def _get (self ,url :str ,params :dict )->dict :
        response =await self ._request ("GET",url ,params =params )
        response .raise_for_status ()
        return response .json ()or {}

    def _series (self ,item :dict )->SourceSeries :
        identifier =item .get ("id")
        if identifier is not None and item .get ("slug"):
            self ._slugs [int (identifier )]=str (item ["slug"])
        return SourceSeries (
        source_id =str (identifier ),
        title =str (item .get ("name")or ""),
        source_name =self .name ,
        cover_url =item .get ("cover")or None ,
        web_url =f"{self .base_url }/series/comic-{item .get ('slug')}",
        )

    @staticmethod 
    def _float (value :Any )->float |None :
        try :
            return float (value )
        except (TypeError ,ValueError ):
            return None 

    @staticmethod 
    def _date (value :Any )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        try :
            return datetime .strptime (str (value ),"%Y-%m-%dT%H:%M:%S.%fZ").replace (
            microsecond =0 ,
            ).isoformat ()
        except ValueError :
            return None 


class GeneratedOlympusScanlationSource (OlympusScanlationSource ):
    name ='olympusscanlation_es'
    display_name ='Olympus Scanlation'
    base_url ='https://olympusxyz.com'
    language ='es'
    requests_per_minute =30 
    content_warning ='safe'
    image_headers ={'Referer':'https://olympusxyz.com/'}


SOURCE =OlympusscanlationSource

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
