from __future__ import annotations


class MadaraDetailsSource :
    pass 


def _yellownote_kids (node :_Node ,tag :str ,class_name :str |None =None )->list [_Node ]:
    return [
    child 
    for child in node .children 
    if isinstance (child ,_Node )
    and child .tag ==tag 
    and (class_name is None or child .has_class (class_name ))
    ]


def _yellownote_classes (node :_Node ,*names :str )->bool :
    return all (node .has_class (name )for name in names )


class YellownoteSource (MadaraDetailsSource ):
    """Albumes de fotos: cada pagina del album es un capitulo."""

    def _text (self ,key :str )->str :
        strings =_YELLOWNOTE_STRINGS .get (self .language )or {}
        return strings .get (key )or _YELLOWNOTE_STRINGS ["en"].get (key ,key )

    def get_preferences (self )->list [SourcePreference ]:
        return [
        SourcePreference (
        id ="XChina::IMAGE_QUALITY",
        name =self ._text ("config.image_quality.title"),
        type ="select",
        options =[("original","原图(JPG)"),("webp_hd","高清(WebP)")],
        default ="original",
        )
        ]

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter (
        "sort",
        self ._text ("filter.sort.title"),
        "select",
        [(value ,self ._text (key ))for value ,key in _YELLOWNOTE_SORT ],
        "",
        ),
        SourceFilter (
        "category",
        self ._text ("filter.category.title"),
        "select",
        [(value ,self ._text (key ))for value ,key in _YELLOWNOTE_CATEGORIES ],
        _YELLOWNOTE_CATEGORIES [0 ][0 ],
        ),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind =="popular":
            return await self ._listing (f"{self .base_url }/photos/sort-hot/{page }.html")
        if kind =="latest":
            return await self ._listing (f"{self .base_url }/photos/{page }.html")
        return {"items":[],"has_more":False }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        values =filters or {}
        query =query .strip ()
        if query :
        # Una busqueda por texto ignora la categoria, como avisa el propio filtro.
            part =f"photos/keyword-{query }"
        else :
            part =str (values .get ("category")or _YELLOWNOTE_CATEGORIES [0 ][0 ])
        segments =[part ]
        sort =str (values .get ("sort")or "")
        if sort .strip ():
            segments .append (sort )
        segments .append (f"{page }.html")
        return await self ._listing (f"{self .base_url }/{'/'.join (segments )}")

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        card =self ._card (root )
        if card is None :
            raise SourceNotFoundError (f"{self .display_name }: ficha sin tarjeta de informacion")
        name =self ._by_icon (card ,"fa-address-card")
        media =self ._by_icon (card ,"fa-image")
        if name is None or media is None :
            raise SourceNotFoundError (f"{self .display_name }: ficha incompleta")
        number =self ._by_icon (card ,"fa-file")
        floating =next (
        (
        node 
        for node in card .descendants ("div")
        if _yellownote_classes (node ,"item","floating")
        ),
        None ,
        )
        tags =[
        value 
        for key ,skip_dash in (("fa-video-camera",True ),("fa-filter",False ),("fa-tags",False ))
        for value in (self ._list_by_icon (card ,key )or [])
        if not (skip_dash and value =="-")
        ]
        known =series if isinstance (series ,SourceSeries )else None 
        return SourceSeries (
        source_id =series_id ,
        title =f"{name }{f' {number }'if number else ''}({media })",
        source_name =self .name ,
        cover_url =known .cover_url if known else None ,
        author =(
        floating .text ().strip ()if floating is not None 
        else self ._by_icon (card ,"fa-circle-user")
        )or None ,
        status ="completed",
        content_tags =tuple (tags ),
        web_url =urljoin (f"{self .base_url }/",series_id ),
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        card =self ._card (root )
        stamp =self ._date (self ._by_icon (card ,"fa-calendar-days")if card is not None else None )
        if stamp is None :
            stamp =self ._version_date (root )
        numbers =[
        value 
        for anchor in self ._pager_anchors (root ,"pager-num")
        if (value :=self ._int (anchor .text ().strip ()))is not None 
        ]
        last =numbers [-1 ]if numbers else 1 
        base =series_id [:-5 ]if series_id .endswith (".html")else series_id 
        return [
        SourceChapter (
        source_id =f"{base }/{page }.html",
        title =f"Page {page }",
        series_id =series_id ,
        source_name =self .name ,
        number =float (page ),
        language =self .language ,
        uploaded_at =stamp ,
        )
        for page in range (last ,0 ,-1 )
        ]

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        urls :list [str ]=[]
        for holder in root .descendants ("div"):
            if not (
            _yellownote_classes (holder ,"list","photo-items")
            or _yellownote_classes (holder ,"list","amateur-items")
            ):
                continue 
            for item in _yellownote_kids (holder ,"div"):
                if not (
                _yellownote_classes (item ,"item","photo-image")
                or _yellownote_classes (item ,"item","amateur-image")
                ):
                    continue 
                value =self ._style_url (item )
                if not value :
                    continue 
                    # La calidad "original" (por defecto) pide el JPG en vez del WebP.
                if "_600x0.webp"in value :
                    value =value .replace ("_600x0.webp",".jpg")
                urls .append (value )
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

    async def _listing (self ,url :str )->dict :
        response =await self ._request ("GET",url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or url 
        items :list [SourceSeries ]=[]
        for holder in root .descendants ("div"):
            if not (
            _yellownote_classes (holder ,"list","photo-list")
            or _yellownote_classes (holder ,"list","amateur-list")
            ):
                continue 
            for item in _yellownote_kids (holder ,"div"):
                if not (
                _yellownote_classes (item ,"item","photo")
                or _yellownote_classes (item ,"item","amateur")
                ):
                    continue 
                anchor =_first (item ,lambda node :node .tag =="a")
                if anchor is None :
                    continue 
                href ,title =anchor .attrs .get ("href",""),anchor .attrs .get ("title","").strip ()
                if not href .strip ()or not title :
                    continue 
                count =next (
                (
                text 
                for tags in item .descendants ("div")
                if tags .has_class ("tags")
                for node in _yellownote_kids (tags ,"div")
                if _YELLOWNOTE_MEDIA_COUNT .match (text :=node .text ().strip ())
                ),
                "",
                )
                items .append (
                SourceSeries (
                source_id =urlparse (urljoin (base ,href )).path .lstrip ("/"),
                title =f"{title }({count })"if count else title ,
                source_name =self .name ,
                cover_url =self ._style_url (anchor )or None ,
                web_url =urljoin (base ,href ),
                )
                )
        return {"items":items ,"has_more":bool (self ._pager_anchors (root ,"pager-next"))}

    @staticmethod 
    def _card (root :_Node )->_Node |None :
        return next (
        (
        node 
        for node in root .descendants ("div")
        if _yellownote_classes (node ,"info-card","photo-detail")
        ),
        None ,
        )

    @staticmethod 
    def _pager_anchors (root :_Node ,class_name :str )->list [_Node ]:
        pager =next ((node for node in root .descendants ("div")if node .has_class ("pager")),None )
        if pager is None :
            return []
        return [node for node in pager .descendants ("a")if node .has_class (class_name )]

    @staticmethod 
    def _style_url (node :_Node )->str :
        image =next (
        (child for child in node .descendants ("div")if child .has_class ("img")),None ,
        )
        if image is None :
            return ""
        found =_YELLOWNOTE_STYLE_URL .search (image .attrs .get ("style",""))
        return found .group (1 )if found else ""

    @classmethod 
    def _item_by_icon (cls ,card :_Node ,icon :str )->_Node |None :
        for item in card .descendants ("div"):
            if not item .has_class ("item"):
                continue 
            marker =next (
            (
            node 
            for holder in item .descendants ()
            if holder .has_class ("icon")
            for node in _yellownote_kids (holder ,"i")
            if node .has_class (icon )
            ),
            None ,
            )
            if marker is not None :
                return next (
                (node for node in item .descendants ("div")if node .has_class ("text")),None ,
                )
        return None 

    @classmethod 
    def _by_icon (cls ,card :_Node ,icon :str )->str |None :
        node =cls ._item_by_icon (card ,icon )
        return node .text ().strip ()if node is not None else None 

    @classmethod 
    def _list_by_icon (cls ,card :_Node ,icon :str )->list [str ]|None :
        node =cls ._item_by_icon (card ,icon )
        if node is None :
            return None 
        return [
        child .text ().strip ()
        for child in node .children 
        if isinstance (child ,_Node )
        ]

    @classmethod 
    def _version_date (cls ,root :_Node )->str |None :
        for holder in root .descendants ("div"):
            if not holder .has_class ("tab-content"):
                continue 
            for card in holder .descendants ("div"):
                if not card .has_class ("info-card"):
                    continue 
                for node in card .descendants ("div"):
                    if node .has_class ("text")and (stamp :=cls ._date (node .text ())):
                        return stamp 
        return None 

    @staticmethod 
    def _int (value :str )->int |None :
        try :
            return int (value )
        except (TypeError ,ValueError ):
            return None 

    @staticmethod 
    def _date (value :str |None )->str |None :
        from datetime import datetime 

        found =_YELLOWNOTE_DATE .search (value or "")
        if not found :
            return None 
        try :
            return datetime .strptime (found .group (),"%Y.%m.%d").isoformat ()
        except ValueError :
            return None 


class GeneratedYellowNoteSource (YellowNoteSource ):
    name ='yellownote_es'
    display_name ='小黄书'
    base_url ='https://es.xchina.co'
    language ='es'
    requests_per_minute =60 
    content_warning ='nsfw'
    image_headers ={'Referer':'https://es.xchina.co/'}


SOURCE =YellownoteSource

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
