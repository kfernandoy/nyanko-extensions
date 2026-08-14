
class MadaraDetailsSource :
    pass 


class PlatinumlilyscanSource (MadaraDetailsSource ):
    """El catalogo entero llega en /api/series y se ordena y filtra en el cliente."""

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("type","Tipo","select",[
        ("","Todos"),("MANGA","Manga"),("MANHWA","Manhwa"),("MANHUA","Manhua"),
        ("DOUJINSHI","Doujinshi"),("ONE_SHOT","One-Shot"),
        ],""),
        SourceFilter ("status","Estado","select",[
        ("","Todos"),("ONGOING","Publicándose"),
        ("COMPLETED","Finalizado"),("HIATUS","Hiatus"),
        ],""),
        SourceFilter ("contentRating","Clasificación de contenido","select",[
        ("","Todos"),("SAFE","Seguro"),("SUGGESTIVE","Sugestivo"),("NSFW","NSFW"),
        ],""),
        SourceFilter ("genre","Género","select",[("","Todos")]+[
        (value ,value )for value in _PLATINUM_GENRES 
        ],""),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        entries =await self ._catalog ()
        if kind =="popular":
            entries .sort (key =lambda item :int ((item .get ("_count")or {}).get ("bookmarks")or 0 ),reverse =True )
        else :
            entries .sort (key =lambda item :str (item .get ("updatedAt")or ""),reverse =True )
        return {"items":[self ._series (item )for item in entries ],"has_more":False }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        values =filters or {}
        needle =query .strip ().casefold ()
        genre =str (values .get ("genre")or "")
        entries =[
        item 
        for item in await self ._catalog ()
        if (not needle or self ._matches_query (item ,needle ))
        and self ._matches (item ,values ,genre )
        ]
        entries .sort (key =lambda item :str (item .get ("updatedAt")or ""),reverse =True )
        return {"items":[self ._series (item )for item in entries ],"has_more":False }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        return self ._series (await self ._series_payload (series_id ))

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        payload =await self ._series_payload (series_id )
        slug =str (payload .get ("slug")or series_id )
        result :list [SourceChapter ]=[]
        for item in payload .get ("chapters")or []:
            if not isinstance (item ,dict )or not str (item .get ("id")or ""):
                continue 
            number =float (item .get ("number")or -1 )
            label =str (number )
            label =label [:-2 ]if label .endswith (".0")else label 
            title =str (item .get ("title")or "").strip ()
            result .append (
            SourceChapter (
            source_id =f"{slug }#{item ['id']}",
            title =f"Capítulo {label }"+(f" - {title }"if title else ""),
            series_id =series_id ,
            source_name =self .name ,
            number =number ,
            language =self .language ,
            uploaded_at =self ._date (item .get ("publishedAt")),
            )
            )
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        slug ,_ ,identifier =chapter_id .partition ("#")
        payload =await self ._series_payload (slug )
        found =next (
        (
        item 
        for item in payload .get ("chapters")or []
        if isinstance (item ,dict )and str (item .get ("id"))==identifier 
        ),
        None ,
        )
        if found is None :
            raise SourceNotFoundError ("Capítulo no encontrado")
        return [
        SourcePage (
        source_id =f"{self .base_url }{image .get ('imageUrl')}",
        chapter_id =chapter_id ,
        index =index ,
        filename =str (image .get ("imageUrl")or "").rsplit ("/",1 )[-1 ]or f"{index }.jpg",
        source_name =self .name ,
        )
        for index ,image in enumerate (found .get ("pages")or [])
        if isinstance (image ,dict )and image .get ("imageUrl")
        ]

    async def _catalog (self )->list [dict ]:
        response =await self ._request ("GET",f"{self .base_url }/api/series")
        response .raise_for_status ()
        return [item for item in response .json ()or []if isinstance (item ,dict )]

    async def _series_payload (self ,slug :str )->dict :
        response =await self ._request ("GET",f"{self .base_url }/api/series/{slug }")
        response .raise_for_status ()
        return response .json ()or {}

    def _series (self ,item :dict )->SourceSeries :
        cover =item .get ("coverUrl")
        return SourceSeries (
        source_id =str (item .get ("slug")or ""),
        title =str (item .get ("title")or ""),
        source_name =self .name ,
        cover_url =f"{self .base_url }{cover }"if cover else None ,
        description =str (item .get ("description")or "")or None ,
        author =str (item .get ("author")or "")or None ,
        artist =str (item .get ("artist")or "")or None ,
        status =_PLATINUM_STATUS .get (str (item .get ("status")or "")),
        content_tags =tuple (self ._genres (item )),
        web_url =f"{self .base_url }/series/{item .get ('slug')}",
        )

    @staticmethod 
    def _genres (item :dict )->list [str ]:
        return [
        str ((entry .get ("genre")or {}).get ("name"))
        for entry in item .get ("genres")or []
        if isinstance (entry ,dict )and (entry .get ("genre")or {}).get ("name")
        ]

    @staticmethod 
    def _matches_query (item :dict ,needle :str )->bool :
        return (
        needle in str (item .get ("title")or "").casefold ()
        or needle in str (item .get ("altTitles")or "").casefold ()
        )

    @classmethod 
    def _matches (cls ,item :dict ,values :dict ,genre :str )->bool :
        for key in ("type","status","contentRating"):
            chosen =str (values .get (key )or "")
            if chosen and str (item .get (key )or "")!=chosen :
                return False 
        if genre and not any (
        name .casefold ()==genre .casefold ()for name in cls ._genres (item )
        ):
            return False 
        return True 

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


class GeneratedPlatinumLilyScanSource (PlatinumLilyScanSource ):
    name ='platinumlilyscan_es'
    display_name ='Platinum Lily Scan'
    base_url ='https://platinumlilyscan.com'
    language ='es'
    requests_per_minute =60 
    content_warning ='mixed'
    image_headers ={'Referer':'https://platinumlilyscan.com/'}


SOURCE =PlatinumlilyscanSource

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
