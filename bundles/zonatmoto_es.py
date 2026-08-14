from __future__ import annotations


class MadaraDetailsSource :
    pass 


class ZonatmotoSource (MadaraDetailsSource ):
    """No hay recientes; pegar la URL de una serie abre su ficha directamente."""

    supports_latest =False 

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("genres","Géneros","multi_select",list (_ZONATMO_GENRES ),[]),
        SourceFilter ("type","Tipo","multi_select",list (_ZONATMO_TYPES ),[]),
        SourceFilter ("status","Estado","multi_select",list (_ZONATMO_STATUSES ),[]),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind !="popular":
            return {"items":[],"has_more":False }
        payload =await self ._get (f"{_ZONATMO_API }/tops/views/month",[
        ("postType","any"),("postsPerPage","50"),
        ])
        items =((payload .get ("data")or {}).get ("items"))or []
        return {
        "items":[entry for item in items if (entry :=self ._series (item ))is not None ],
        "has_more":False ,
        }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        query =query .strip ()
        slug =self ._deeplink (query )
        if slug :
            payload =await self ._get (f"{_ZONATMO_API }/single/manga/{slug }",[])
            entry =self ._series (payload .get ("data")or {})
            return {"items":[entry ]if entry is not None else [],"has_more":False }
        values =filters or {}
        params :list [tuple [str ,str ]]=[("page",str (page ))]
        if query :
            params .append (("search",query ))
        for key ,parameter in (("genres","genres[]"),("type","type[]"),("status","status[]")):
            chosen =values .get (key )
            if isinstance (chosen ,list ):
                params .extend ((parameter ,str (value ))for value in chosen )
        payload =await self ._get (f"{_ZONATMO_API }/listing/manga",params )
        data =payload .get ("data")or {}
        return {
        "items":[
        entry for item in data .get ("items")or []if (entry :=self ._series (item ))is not None 
        ],
        "has_more":bool ((data .get ("pagination")or {}).get ("has_next")),
        }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        payload =await self ._get (f"{_ZONATMO_API }/single/manga/{series_id }",[])
        entry =self ._series (payload .get ("data")or {})
        if entry is None :
            raise SourceNotFoundError (f"{self .display_name }: ficha no encontrada")
        return entry 

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        entries :list [dict ]=[]
        total =1 
        page =1 
        while page <=total :
            payload =await self ._get (
            f"{_ZONATMO_API }/single/manga/{series_id }/chapters",
            [
            ("page",str (page )),
            ("postsPerPage",str (_ZONATMO_CHAPTERS_PER_PAGE )),
            ("order","asc"),
            ],
            )
            data =payload .get ("data")or {}
            entries .extend (item for item in data .get ("items")or []if isinstance (item ,dict ))
            if page ==1 :
                total =int ((data .get ("pagination")or {}).get ("total_pages")or 1 )
            page +=1 
        unique =list ({str (item .get ("id")):item for item in entries }.values ())
        unique .sort (key =lambda item :self ._float (item .get ("chapter_number")),reverse =True )
        return [
        SourceChapter (
        source_id =f"{series_id }/{item .get ('slug')}#{item .get ('id')}",
        title =f"#{item .get ('chapter_number')}"
        +(f" - {title }"if (title :=str (item .get ('title')or '').strip ())else ""),
        series_id =series_id ,
        source_name =self .name ,
        number =self ._float (item .get ("chapter_number")),
        language =self .language ,
        uploaded_at =self ._date (item .get ("release_date")),
        )
        for item in unique 
        ]

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        path =chapter_id .partition ("#")[0 ].strip ("/").split ("/")
        if len (path )<2 :
            raise SourceNotFoundError (f"{self .display_name }: capitulo sin ruta valida")
        payload =await self ._get (f"{_ZONATMO_API }/single/manga/{path [-2 ]}/{path [-1 ]}",[])
        data =((payload .get ("data")or {}).get ("chapter"))or {}
        images =[item for item in data .get ("images")or []if isinstance (item ,dict )]
        images .sort (key =lambda item :int (item .get ("page_number")or 0 ))
        jit =str (data .get ("jit")or "").strip ("/")
        return [
        SourcePage (
        source_id =f"{_ZONATMO_CDN }/manga/{jit }/{item .get ('image_url')}",
        chapter_id =chapter_id ,
        index =index ,
        filename =str (item .get ("image_url")or f"{index }.jpg"),
        source_name =self .name ,
        )
        for index ,item in enumerate (images )
        ]

        # -------------------------------------------------------------- internals
    async def _get (self ,url :str ,params :list [tuple [str ,str ]])->dict :
        response =await self ._request (
        "GET",url ,params =params ,headers ={"Referer":f"{self .base_url }/"},
        )
        response .raise_for_status ()
        return response .json ()or {}

    @staticmethod 
    def _deeplink (query :str )->str |None :
        parsed =urlparse (query )
        if parsed .netloc not in {_ZONATMO_HOST ,f"www.{_ZONATMO_HOST }"}:
            return None 
        parts =[part for part in parsed .path .split ("/")if part ]
        if len (parts )<2 or parts [0 ]!="manga":
            return None 
        return parts [1 ]

    def _series (self ,item :Any )->SourceSeries |None :
        if not isinstance (item ,dict ):
            return None 
        slug =str (item .get ("slug")or "").strip ()
        title =str (item .get ("title")or "").strip ()
        if not slug or not title :
            return None 
        authors =list (dict .fromkeys (
        name 
        for author in item .get ("author")or []
        if isinstance (author ,dict )and (name :=str (author .get ("name")or "").strip ())
        ))
        return SourceSeries (
        source_id =slug ,
        title =title ,
        source_name =self .name ,
        cover_url =self ._cover (item .get ("cover")),
        description =str (item .get ("overview")or "").strip ()or None ,
        author =", ".join (authors )or None ,
        status =self ._status (item .get ("status")),
        content_tags =tuple (
        name 
        for identifier in item .get ("genres")or []
        if (name :=_ZONATMO_GENRE_NAMES .get (str (identifier )))
        ),
        web_url =f"{self .base_url }/manga/{slug }",
        )

    @staticmethod 
    def _cover (value :Any )->str |None :
        path =str (value or "").strip ()
        if not path :
            return None 
        if path .casefold ().startswith ("http"):
            return path 
        return f"{_ZONATMO_UPLOADS }/{path .lstrip ('/')}"

    @staticmethod 
    def _status (value :Any )->str |None :
        if not isinstance (value ,list ):
            return None 
        for identifier ,status in _ZONATMO_STATUS :
            if identifier in value :
                return status 
        return None 

    @staticmethod 
    def _float (value :Any )->float :
        try :
            return float (value )
        except (TypeError ,ValueError ):
            return -1.0 

    @staticmethod 
    def _date (value :Any )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        try :
            return datetime .strptime (str (value ),"%Y-%m-%d %H:%M:%S").isoformat ()
        except ValueError :
            return None 


class GeneratedZonatmoToSource (ZonatmoToSource ):
    name ='zonatmoto_es'
    display_name ='Zonatmo.to (unoriginal)'
    base_url ='https://zonatmo.to'
    language ='es'
    requests_per_minute =60 
    content_warning ='mixed'
    image_headers ={'Referer':'https://zonatmo.to/'}


SOURCE =ZonatmotoSource

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
