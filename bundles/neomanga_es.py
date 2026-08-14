from __future__ import annotations


class MadaraDetailsSource :
    pass 


def _neomanga_object (text :str ,key :str )->dict |None :
    """Busca el objeto JSON mas cercano que contenga `key`."""
    needle =f'"{key }"'
    decoder =json .JSONDecoder ()
    index =text .find (needle )
    while index !=-1 :
        start ,attempts =text .rfind ("{",0 ,index ),0 
        while start !=-1 and attempts <200 :
            try :
                value ,_ =decoder .raw_decode (text ,start )
            except ValueError :
                pass 
            else :
                if isinstance (value ,dict )and key in value :
                    return value 
            start ,attempts =text .rfind ("{",0 ,start ),attempts +1 
        index =text .find (needle ,index +1 )
    return None 


def _neomanga_payload (text :str ,key :str )->dict |None :
# El flight de Next.js a veces llega escapado dentro de una cadena.
    for candidate in (text ,text .replace ('\\"','"')):
        found =_neomanga_object (candidate ,key )
        if found is not None :
            return found 
    return None 


class NeomangaSource (MadaraDetailsSource ):
    """No hay endpoint de busqueda: el catalogo entero se filtra en el cliente."""

    supports_latest =False 

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("status","Estado","select",[
        ("all","Todos"),("en_emision","En emisión"),
        ("finalizado","Finalizado"),("pausado","Pausado"),
        ],"all"),
        SourceFilter ("genre","Género","select",[("","Todos")]+[
        (value ,value )for value in _NEOMANGA_GENRES 
        ],""),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind !="popular":
            return {"items":[],"has_more":False }
        return {"items":[self ._series (item )for item in await self ._catalog ()],"has_more":False }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        values =filters or {}
        items =await self ._catalog ()
        needle =query .strip ().casefold ()
        if needle :
            items =[item for item in items if needle in str (item .get ("title")or "").casefold ()]
        status =str (values .get ("status")or "all")
        if status !="all":
            items =[item for item in items if item .get ("status")==status ]
        genre =str (values .get ("genre")or "")
        if genre :
            items =[item for item in items if genre in (item .get ("genres")or [])]
        return {"items":[self ._series (item )for item in items ],"has_more":False }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",f"{self .base_url }/manga/{series_id }")
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        heading =_first (root ,lambda node :node .tag =="h1")
        if heading is None :
            raise SourceNotFoundError (f"{self .display_name }: ficha sin titulo")
        summary =_first (root ,lambda node :node .has_class ("whitespace-pre-line"))
        cover =next (
        (
        node 
        for holder in root .descendants ("div")
        if "aspect-[3/4]"in holder .attrs .get ("class","").split ()
        for node in holder .descendants ("img")
        ),
        None ,
        )
        badge =_first (
        root ,
        lambda node :node .tag =="span"
        and (node .has_class ("bg-success")or node .has_class ("bg-danger")or node .has_class ("bg-secondary")),
        )
        text =badge .text ().casefold ()if badge is not None else ""
        return SourceSeries (
        source_id =series_id ,
        title =heading .text ().strip (),
        source_name =self .name ,
        cover_url =urljoin (base ,cover .attrs .get ("src",""))if cover is not None else None ,
        description =(summary .text ().strip ()if summary is not None else None )or None ,
        status =next (
        (
        value 
        for word ,value in (
        ("emisión","ongoing"),("finalizado","completed"),("pausado","hiatus"),
        )
        if word in text 
        ),
        None ,
        ),
        content_tags =tuple (
        value 
        for node in root .descendants ("span")
        if node .has_class ("bg-accent-soft")and (value :=node .text ().strip ())
        ),
        web_url =f"{self .base_url }/manga/{series_id }",
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request (
        "GET",f"{self .base_url }/manga/{series_id }",headers ={"RSC":"1"},
        )
        response .raise_for_status ()
        payload =_neomanga_payload (response .text ,"chapters")or {}
        result :list [SourceChapter ]=[]
        for item in payload .get ("chapters")or []:
            if not isinstance (item ,dict ):
                continue 
            number =float (item .get ("chapter_number")or 0 )
            label =str (number )
            label =label [:-2 ]if label .endswith (".0")else label 
            result .append (
            SourceChapter (
            source_id =f"manga/{series_id }/capitulo/{label }",
            title =str (item .get ("title")or f"Capítulo {label }"),
            series_id =series_id ,
            source_name =self .name ,
            number =number ,
            language =self .language ,
            uploaded_at =self ._date (item .get ("published_at")),
            )
            )
        result .sort (key =lambda chapter :chapter .number or 0.0 ,reverse =True )
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._request (
        "GET",urljoin (f"{self .base_url }/",chapter_id .lstrip ("/")),headers ={"RSC":"1"},
        )
        response .raise_for_status ()
        payload =_neomanga_payload (response .text ,"chapter")or {}
        sources =((payload .get ("chapter")or {}).get ("pages_urls"))or []
        if not sources :
            raise SourceNotFoundError ("No se encontraron páginas")
        urls :list [str ]=[]
        for value in sources :
            text =str (value )
            if not text .startswith ("MANGADEX:"):
                urls .append (text )
                continue 
                # Las paginas de MangaDex se sirven a traves de un proxy propio.
            identifier =text [len ("MANGADEX:"):]
            proxied =await self ._request (
            "GET",f"{self .base_url }/api/mangadex-pages/{identifier }",
            )
            proxied .raise_for_status ()
            count =len ((proxied .json ()or {}).get ("pages")or [])
            urls .extend (
            f"{self .base_url }/api/manga-page/{identifier }/{index }"for index in range (count )
            )
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

    async def _catalog (self )->list [dict ]:
        response =await self ._request (
        "GET",f"{self .base_url }/series",headers ={"RSC":"1"},
        )
        response .raise_for_status ()
        payload =_neomanga_payload (response .text ,"initialMangas")or {}
        return [item for item in payload .get ("initialMangas")or []if isinstance (item ,dict )]

    def _series (self ,item :dict )->SourceSeries :
        return SourceSeries (
        source_id =str (item .get ("slug")or ""),
        title =str (item .get ("title")or ""),
        source_name =self .name ,
        cover_url =self ._cover (item .get ("cover_image_url")),
        description =str (item .get ("synopsis")or "")or None ,
        status =_NEOMANGA_STATUS .get (str (item .get ("status")or "")),
        content_tags =tuple (str (value )for value in item .get ("genres")or []),
        web_url =f"{self .base_url }/manga/{item .get ('slug')}",
        )

    def _cover (self ,value :Any )->str |None :
        text =str (value or "")
        if not text :
            return None 
        if "/_next/image"in text or text .startswith ("/"):
            return text 
        from urllib .parse import quote 

        return f"{self .base_url }/_next/image?url={quote (text ,safe ='')}&w=640&q=75"

    @staticmethod 
    def _date (value :Any )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        text =str (value ).split (".")[0 ].split ("+")[0 ].rstrip ("Z")
        try :
            return datetime .strptime (text ,"%Y-%m-%dT%H:%M:%S").isoformat ()
        except ValueError :
            return None 


class GeneratedNeoMangaSource (NeoMangaSource ):
    name ='neomanga_es'
    display_name ='NeoManga'
    base_url ='https://www.neomanga.online'
    language ='es'
    requests_per_minute =60 
    content_warning ='safe'
    image_headers ={'Referer':'https://www.neomanga.online/'}


SOURCE =NeomangaSource

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
