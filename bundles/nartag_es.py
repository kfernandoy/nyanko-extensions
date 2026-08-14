from __future__ import annotations


class MadaraDetailsSource :
    pass 


def _nartag_last_child (node :_Node )->bool :
    parent =node .parent 
    if parent is None :
        return False 
    elements =[child for child in parent .children if isinstance (child ,_Node )]
    return bool (elements )and elements [-1 ]is node 


class NartagSource (MadaraDetailsSource ):
    """Los capitulos viven en /comics/<slug>/chapters y pagina por cabeceras."""

    max_chapter_pages =50 

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("sort","Ordenar por","select",list (_NARTAG_SORT ),"latest"),
        SourceFilter ("type","Tipo","select",[("","Todos")]+[
        (value ,value )for value in ("Manga","Manhwa","Manhua","Other")
        ],""),
        SourceFilter ("status","Estado","select",[("","Todos")]+[
        (value ,value )for value in ("Ongoing","Completed","Hiatus","Cancelled")
        ],""),
        SourceFilter ("genre","Géneros","select",[("","Todos")]+[
        (value ,value )for value in _NARTAG_GENRES 
        ],""),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        return await self ._library ([
        ("sort","views"if kind =="popular"else "updated"),("page",str (page )),
        ])

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        values =filters or {}
        params :list [tuple [str ,str ]]=[("page",str (page ))]
        if query .strip ():
            params .append (("q",query .strip ()))
        params .append (("sort",str (values .get ("sort")or "latest")))
        # "Todos" no viaja: el Kotlin solo manda el filtro cuando el indice es > 0.
        params .extend (
        (key ,str (values [key ]))
        for key in ("type","status","genre")
        if str (values .get (key )or "")
        )
        return await self ._library (params )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        badges =[
        text 
        for node in root .descendants ("span")
        if node .has_class ("inline-flex")and node .has_class ("items-center")and node .has_class ("rounded")
        and (text :=node .text ().strip ())
        ]
        lowered =[value .casefold ()for value in badges ]
        summary =next (
        (
        node .text ().strip ()
        for holder in root .descendants ("div")
        if holder .has_class ("comic-page-wrap")
        for node in holder .descendants ("p")
        if any (name .startswith ("text-")for name in node .attrs .get ("class","").split ())
        ),
        "",
        )
        group =_first (root ,lambda node :node .tag =="a"and node .attrs .get ("href","").startswith ("/groups/"))
        author =artist =group .text ().strip ()if group is not None else None 
        for label ,value in self ._rows (root ):
            if label =="Autor":
                author =value 
            elif label =="Arte":
                artist =value 
        known =series if isinstance (series ,SourceSeries )else None 
        return SourceSeries (
        source_id =series_id ,
        title =known .title if known else series_id .rstrip ("/").rsplit ("/",1 )[-1 ],
        source_name =self .name ,
        cover_url =known .cover_url if known else None ,
        description =summary or None ,
        author =author or None ,
        artist =artist or None ,
        status =self ._status (lowered ),
        content_tags =tuple (
        value for value in badges if value .casefold ()not in _NARTAG_STATUS_WORDS 
        ),
        web_url =urljoin (f"{self .base_url }/",series_id ),
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        slug =series_id .rstrip ("/").rsplit ("/",1 )[-1 ]
        result :list [SourceChapter ]=[]
        page =1 
        while page <=self .max_chapter_pages :
            response =await self ._request (
            "GET",f"{self .base_url }/comics/{slug }/chapters",params ={"page":str (page )},
            )
            response .raise_for_status ()
            root =_parse_html (response .text )
            base =str (response .url )or self .base_url 
            found =[node for node in root .descendants ("a")if node .attrs .get ("data-chapter-id")]
            if not found :
                break 
            for index ,anchor in enumerate (found ):
                number =self ._float (anchor .attrs .get ("data-chapter-num"),float (index ))
                label =anchor .attrs .get ("data-chapter-label","").strip ()
                moment =_first (
                anchor ,lambda node :"text-[0.65rem]"in node .attrs .get ("class","").split (),
                )
                result .append (
                SourceChapter (
                source_id =self ._path (anchor .attrs .get ("href",""),base ),
                title =label or f"Capítulo {int (number )}",
                series_id =series_id ,
                source_name =self .name ,
                number =number ,
                language =self .language ,
                uploaded_at =self ._date (moment .text ()if moment is not None else ""),
                )
                )
            headers =getattr (response ,"headers",None )or {}
            current =self ._int (headers .get ("x-page"))
            total =self ._int (headers .get ("x-pages"))
            if current is None or total is None or current >=total :
                break 
            page +=1 
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        found :list [_Node ]=[]
        for node in root .descendants ("img"):
            if node .has_class ("page-img")or self ._in_page_wrap (node ):
                found .append (node )
        urls =[
        urljoin (base ,node .attrs .get ("data-src")or node .attrs .get ("src")or "")
        for node in found 
        ]
        return [
        SourcePage (
        source_id =value ,
        chapter_id =chapter_id ,
        index =index ,
        filename =urlparse (value ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",
        source_name =self .name ,
        )
        for index ,value in enumerate (value for value in urls if value )
        ]

    async def _library (self ,params :list [tuple [str ,str ]])->dict :
        response =await self ._request ("GET",f"{self .base_url }/library",params =params )
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        items :list [SourceSeries ]=[]
        for grid in root .descendants ("div"):
            if not grid .has_class ("lib-grid"):
                continue 
            for card in grid .descendants ("a"):
                if not card .has_class ("comic-card"):
                    continue 
                    # Las novelas comparten grilla con los comics y no se soportan.
                badge =_first (
                card ,
                lambda node :node .tag =="span"
                and node .has_class ("absolute")and node .has_class ("top-2")and node .has_class ("left-2"),
                )
                if badge is not None and "novel"in badge .text ().casefold ():
                    continue 
                heading =_first (card ,lambda node :node .tag =="p"and node .has_class ("leading-snug"))
                if heading is None :
                    continue 
                image =_first (card ,lambda node :node .tag =="img")
                items .append (
                SourceSeries (
                source_id =self ._path (card .attrs .get ("href",""),base ),
                title =heading .text ().strip (),
                source_name =self .name ,
                cover_url =_image_url (image ,base )or None if image is not None else None ,
                web_url =urljoin (base ,card .attrs .get ("href","")),
                )
                )
        has_more =any (
        node .has_class ("lib-page-btn--nav")and _nartag_last_child (node )
        for node in root .descendants ("a")
        )
        return {"items":items ,"has_more":has_more }

    @staticmethod 
    def _in_page_wrap (node :_Node )->bool :
        parent =node .parent 
        while parent is not None :
            if parent .has_class ("page-wrap"):
                return True 
            parent =parent .parent 
        return False 

    @staticmethod 
    def _rows (root :_Node )->list [tuple [str ,str ]]:
        result :list [tuple [str ,str ]]=[]
        for row in root .descendants ("div"):
            classes =row .attrs .get ("class","").split ()
            if not {"flex","items-baseline","justify-between","gap-2"}<=set (classes ):
                continue 
            label =_first (row ,lambda node :"text-[var(--color-text3)]"in node .attrs .get ("class","").split ())
            value =_first (row ,lambda node :"text-[var(--color-text2)]"in node .attrs .get ("class","").split ())
            if label is not None and value is not None :
                result .append ((label .text ().strip (),value .text ().strip ()))
        return result 

    @staticmethod 
    def _status (badges :list [str ])->str |None :
        for words ,value in (
        (("emisión","curso","ongoing"),"ongoing"),
        (("completado","completed"),"completed"),
        (("pausa","hiatus"),"hiatus"),
        (("cancelado","cancelled"),"cancelled"),
        ):
            if any (word in badge for badge in badges for word in words ):
                return value 
        return None 

    @staticmethod 
    def _float (value :Any ,fallback :float )->float :
        try :
            return float (value )
        except (TypeError ,ValueError ):
            return fallback 

    @staticmethod 
    def _int (value :Any )->int |None :
        try :
            return int (value )
        except (TypeError ,ValueError ):
            return None 

    @staticmethod 
    def _path (href :str ,base :str )->str :
        return urlparse (urljoin (base ,href )).path .lstrip ("/")

    @staticmethod 
    def _date (value :str )->str |None :
        from datetime import datetime ,timedelta 

        cleaned =value .casefold ()
        for accented ,plain in (("í","i"),("á","a"),("é","e"),("ó","o"),("ú","u"),("ñ","n")):
            cleaned =cleaned .replace (accented ,plain )
        if not cleaned .startswith ("hace"):
            found =_NARTAG_ABSOLUTE .search (value )
            month =_NARTAG_MONTHS .get (found .group (1 ).casefold ())if found else None 
            if month is None :
                return None 
            return datetime (int (found .group (3 )),month ,int (found .group (2 ))).isoformat ()
        digits ="".join (_NARTAG_DIGITS .findall (cleaned ))
        if not digits :
            return None 
        amount ,now =int (digits ),datetime .now ().replace (microsecond =0 )
        if "hora"in cleaned :
            return (now -timedelta (hours =amount )).isoformat ()
        if "sem"in cleaned :
            return (now -timedelta (days =amount *7 )).isoformat ()
        if "dia"in cleaned :
            return (now -timedelta (days =amount )).isoformat ()
        if "mes"in cleaned :
            return (now -timedelta (days =amount *30 )).isoformat ()
        if "ano"in cleaned :
            return (now -timedelta (days =amount *365 )).isoformat ()
        return None 


class GeneratedRncalationSource (RncalationSource ):
    name ='nartag_es'
    display_name ='Rncalation'
    base_url ='https://rncalation.online'
    language ='es'
    requests_per_minute =120 
    content_warning ='mixed'
    image_headers ={'Referer':'https://rncalation.online/'}


SOURCE =NartagSource

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
