from __future__ import annotations


class MadaraDetailsSource :
    pass 


def _plot_image (node :_Node ,base :str )->str :
    for attribute in ("data-src","data-lazy-src"):
        value =node .attrs .get (attribute ,"").strip ()
        if value :
            return urljoin (base ,value )
    srcset =node .attrs .get ("srcset","").strip ()
    if srcset :
        return urljoin (base ,srcset .split (" ",1 )[0 ])
    return urljoin (base ,node .attrs .get ("src","").strip ())


class PlottwistnofansubSource (MadaraDetailsSource ):
    """La ficha trae la primera tanda de capitulos y el resto llega por AJAX."""

    def get_filters (self )->list [SourceFilter ]:
        return []

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        return await self ._library (
        page ,"trending"if kind =="popular"else "latest3",
        )

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        query =query .strip ()
        if not query :
            return await self ._library (page ,"views3")
        suffix =f"page/{page }/"if page >1 else ""
        response =await self ._request (
        "GET",
        urljoin (f"{self .base_url }/",suffix ),
        params ={"s":query ,"post_type":"wp-manga"},
        )
        response .raise_for_status ()
        return self ._grid (response )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        heading =_first (
        root ,lambda node :node .tag =="h1"and node .has_class ("mn-detail-title"),
        )or next (
        (
        node 
        for holder in root .descendants ()
        if holder .has_class ("post-title")
        for node in holder .descendants ("h1")
        ),
        None ,
        )
        if heading is None :
            raise SourceNotFoundError (f"{self .display_name }: ficha sin titulo")
        cover =self ._image_in (root ,"mn-detail-cover-frame",base )or self ._image_in (
        root ,"summary_image",base ,
        )
        summary =_first (root ,lambda node :node .has_class ("mn-detail-synopsis"))or _first (
        root ,lambda node :node .has_class ("summary__content"),
        )
        tags =self ._links_in (root ,"mn-detail-genres-desktop")or self ._links_in (
        root ,"genres-content",
        )
        pill =_first (root ,lambda node :node .has_class ("mn-detail-pill-value"))
        classes =set (pill .attrs .get ("class","").split ())if pill is not None else set ()
        text =pill .text ().casefold ()if pill is not None else ""
        return SourceSeries (
        source_id =series_id ,
        title =heading .text ().strip (),
        source_name =self .name ,
        cover_url =cover ,
        description =(summary .text ().strip ()if summary is not None else None )or None ,
        author =self ._author (root ),
        status =self ._status (classes ,text ),
        content_tags =tuple (tags ),
        web_url =urljoin (f"{self .base_url }/",series_id ),
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        identifier =self ._manga_id (root ,response .text )
        if not identifier :
            raise SourceNotFoundError ("No se pudo encontrar el ID del manga")
        seen :set [str ]=set ()
        result :list [SourceChapter ]=[]
        # La primera tanda ya viene renderizada; el AJAX la repite en la pagina 1.
        self ._collect (root ,base ,series_id ,seen ,result )
        page =1 
        while page <=_PLOT_MAX_PAGES :
            ajax =await self ._request (
            "POST",
            f"{self .base_url }/wp-admin/admin-ajax.php",
            data ={"action":"plot_load_chapters","manga_id":identifier ,"page":str (page )},
            )
            try :
                payload =ajax .json ()or {}
            except Exception :
                break 
            html =((payload .get ("data")or {}).get ("html"))or ""
            if not html :
                break 
            before =len (result )
            self ._collect (_parse_html (html ),base ,series_id ,seen ,result )
            if len (result )==before and not ((payload .get ("data")or {}).get ("has_more")):
                break 
            if not ((payload .get ("data")or {}).get ("has_more")):
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
        for finder in (
        lambda :[
        node 
        for holder in root .descendants ("div")
        if holder .has_class ("reading-content")
        for node in holder .descendants ("img")
        ],
        lambda :[
        node for node in root .descendants ("img")if node .has_class ("wp-manga-chapter-img")
        ],
        lambda :[
        node 
        for holder in root .descendants ()
        if holder .has_class ("chapter-content")
        for node in holder .descendants ("img")
        ],
        lambda :[
        node for node in root .descendants ("img")if node .has_class ("attachment-full")
        ],
        lambda :[
        node 
        for holder in root .descendants ("div")
        if holder .has_class ("pg-box")or holder .has_class ("page-break")
        for node in holder .descendants ("img")
        ],
        ):
            found =finder ()
            if found :
                break 
        urls =[value for node in found if (value :=_plot_image (node ,base ))]
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
    async def _library (self ,page :int ,order :str )->dict :
        suffix =f"biblioteca3/page/{page }/"if page >1 else "biblioteca3"
        response =await self ._request (
        "GET",urljoin (f"{self .base_url }/",suffix ),params ={"m_orderby":order },
        )
        response .raise_for_status ()
        return self ._grid (response )

    def _grid (self ,response :Any )->dict :
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        items :list [SourceSeries ]=[]
        for grid in root .descendants ("div"):
            if not grid .has_class ("manga-grid-v2"):
                continue 
            for figure in grid .descendants ("figure"):
                anchor =_first (
                figure ,lambda node :node .tag =="a"and node .attrs .get ("href"),
                )
                if anchor is None :
                    continue 
                caption =_first (figure ,lambda node :node .tag =="figcaption")
                title =anchor .attrs .get ("title","").strip ()or (
                caption .text ().strip ()if caption is not None else ""
                )
                if not title :
                    continue 
                image =_first (figure ,lambda node :node .tag =="img")
                href =anchor .attrs .get ("href","")
                items .append (
                SourceSeries (
                source_id =urlparse (urljoin (base ,href )).path .lstrip ("/"),
                title =title ,
                source_name =self .name ,
                cover_url =_plot_image (image ,base )if image is not None else None ,
                web_url =urljoin (base ,href ),
                )
                )
        has_more =any (
        node .has_class ("next")or "siguiente"in node .text ().casefold ()
        for node in root .descendants ("a")
        )
        return {"items":items ,"has_more":has_more }

    def _collect (
    self ,root :_Node ,base :str ,series_id :str ,seen :set [str ],result :list ,
    )->None :
        for anchor in root .descendants ("a"):
            if not anchor .has_class ("mn-detail-chapter-item"):
                continue 
            href =anchor .attrs .get ("href","")
            if not href :
                continue 
            target =urljoin (base ,href )
            if target in seen :
                continue 
            seen .add (target )
            number =_first (anchor ,lambda node :node .has_class ("mn-detail-chapter-name"))
            extend =_first (anchor ,lambda node :node .has_class ("mn-detail-chapter-extend"))
            moment =_first (anchor ,lambda node :node .has_class ("mn-detail-chapter-date"))
            label =number .text ().strip ()if number is not None else ""
            extra =extend .text ().strip ()if extend is not None else ""
            found =_PLOT_NUMBER .search (label )
            result .append (
            SourceChapter (
            source_id =urlparse (target ).path .lstrip ("/"),
            title =f"Capítulo {label }"+(f" - {extra }"if extra else ""),
            series_id =series_id ,
            source_name =self .name ,
            number =float (found .group ())if found else None ,
            language =self .language ,
            uploaded_at =self ._date (
            _PLOT_HTML_TAG .sub ("",moment .text ())if moment is not None else "",
            ),
            )
            )

    @staticmethod 
    def _manga_id (root :_Node ,text :str )->str :
        holder =_first (root ,lambda node :node .attrs .get ("id")=="mn-detail-load-more")
        if holder is not None and holder .attrs .get ("data-manga"):
            return holder .attrs ["data-manga"]
        for pattern in (_PLOT_MANGA_ID ,_PLOT_OLD_MANGA_ID ):
            found =pattern .search (text or "")
            if found :
                return found .group (1 )
        return ""

    @staticmethod 
    def _image_in (root :_Node ,class_name :str ,base :str )->str |None :
        for holder in root .descendants ():
            if not holder .has_class (class_name ):
                continue 
            for node in holder .descendants ("img"):
                return _plot_image (node ,base )or None 
        return None 

    @staticmethod 
    def _links_in (root :_Node ,class_name :str )->list [str ]:
        return [
        value 
        for holder in root .descendants ()
        if holder .has_class (class_name )
        for node in holder .descendants ("a")
        if (value :=node .text ().strip ())
        ]

    @staticmethod 
    def _author (root :_Node )->str |None :
        for holder in root .descendants ():
            if not holder .has_class ("mn-detail-pill-label")or "autor"not in holder .text ().casefold ():
                continue 
            parent =holder .parent 
            elements =[
            child for child in (parent .children if parent is not None else [])
            if isinstance (child ,_Node )
            ]
            if holder in elements :
                index =elements .index (holder )
                if index +1 <len (elements )and elements [index +1 ].has_class ("mn-detail-pill-value"):
                    return elements [index +1 ].text ().strip ()or None 
        for holder in root .descendants ():
            if holder .has_class ("author-content"):
                for node in holder .descendants ("a"):
                    return node .text ().strip ()or None 
        return None 

    @staticmethod 
    def _status (classes :set [str ],text :str )->str |None :
        for marker ,words ,value in (
        ("mn-st-emit",("en emisión","en curso"),"ongoing"),
        ("mn-st-comp",("finalizado","completado"),"completed"),
        ("mn-st-cancel",("cancelado",),"cancelled"),
        ("mn-st-pause",("en espera",),"hiatus"),
        ):
            if marker in classes or any (word in text for word in words ):
                return value 
        return None 

    @staticmethod 
    def _date (value :str )->str |None :
        from datetime import datetime 

        found =_PLOT_DATE .search (value or "")
        month =_PLOT_MONTHS .get (found .group (1 ).casefold ())if found else None 
        if month is None :
            return None 
        try :
            return datetime (int (found .group (3 )),month ,int (found .group (2 ))).isoformat ()
        except ValueError :
            return None 


class GeneratedPlotTwistNoFansubSource (PlotTwistNoFansubSource ):
    name ='plottwistnofansub_es'
    display_name ='Plot Twist No Fansub'
    base_url ='https://plotnofansub.com'
    language ='es'
    requests_per_minute =120 
    content_warning ='mixed'
    image_headers ={'Referer':'https://plotnofansub.com/'}


SOURCE =PlottwistnofansubSource

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
