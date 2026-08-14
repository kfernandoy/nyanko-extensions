from __future__ import annotations


class MadaraDetailsSource :
    pass 



class ManhwaLatinoSource (MadaraDetailsSource ):
    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        series_url =urljoin (f"{self .base_url }/",series_id )
        response =await self ._request ("GET",series_url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        if not self ._chapter_nodes (root ):
            holder =_first (root ,lambda node :node .attrs .get ("id","").startswith ("manga-chapters-holder"))
            if holder is not None :
                response =await self ._request (
                "POST",f"{series_url .rstrip ('/')}/ajax/chapters",
                headers ={"X-Requested-With":"XMLHttpRequest"},
                )
                response .raise_for_status ()
                root =_parse_html (response .text )

        result =[]
        page =1 
        while True :
            for item in self ._chapter_nodes (root ):
                box =_first (item ,lambda node :node .tag =="div"and node .has_class ("mini-letters"))
                anchor =_first (box ,lambda node :node .tag =="a"and bool (node .attrs .get ("href")))if box else None 
                if anchor is None :
                    continue 
                whole_text ="".join (
                child .text ()if isinstance (child ,_Node )else child for child in anchor .children 
                )
                title =whole_text .split ("\n",1 )[-1 ].strip ()or anchor .text ().strip ()
                image =_first (item ,lambda node :node .tag =="img"and not node .has_class ("thumb"))
                relative =_first (
                item ,
                lambda node :node .tag =="a"and node .parent is not None 
                and node .parent .tag =="span"and bool (node .attrs .get ("title")),
                )
                date =_first (item ,lambda node :node .has_class ("chapter-release-date"))
                date_text =(
                image .attrs .get ("alt","")if image else relative .attrs .get ("title","")if relative 
                else date .text ()if date else ""
                )
                url =urljoin (series_url ,anchor .attrs ["href"]).split ("?style=paged",1 )[0 ]
                if not url .endswith (self .chapter_url_suffix ):
                    url +=self .chapter_url_suffix 
                number =re .search (r"\d+(?:\.\d+)?",title )
                result .append (SourceChapter (
                source_id =url ,title =title or "Capítulo",series_id =series_id ,
                source_name =self .name ,number =float (number .group ())if number else None ,
                language =self .language ,uploaded_at =self ._madara_date (date_text ),
                ))
            if not self ._latino_has_next (root ):
                return result 
            page +=1 
            response =await self ._request ("GET",series_url ,params ={"t":str (page )})
            response .raise_for_status ()
            root =_parse_html (response .text )

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        response .raise_for_status ()
        urls =[
        _image_url (image ,str (response .url ))
        for image in _parse_html (response .text ).descendants ("img")
        if image .has_class ("wp-manga-chapter-img")and self ._has_class_ancestor (image ,"page-break")
        ]
        return [SourcePage (
        source_id =url ,chapter_id =chapter_id ,index =index ,
        filename =urlparse (url ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",source_name =self .name ,
        )for index ,url in enumerate (dict .fromkeys (urls ))]

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else str (page )
        response =await self ._request (
        "GET",url ,
        headers ={
        "Accept-Encoding":"",
        "Referer":page .chapter_id if isinstance (page ,SourcePage )else self .base_url ,
        },
        )
        response .raise_for_status ()
        media_type =response .headers .get ("Content-Type","image/jpeg")
        if "application/octet-stream"in media_type .casefold ():
            media_type ="image/jpeg"
        return SourcePageContent (media_type =media_type ,chunks =iter ([response .content ]))

    @staticmethod 
    def _latino_has_next (root :_Node )->bool :
        for current in root .descendants ("span"):
            parent =current .parent 
            if not current .has_class ("current")or parent is None or not parent .has_class ("pagination"):
                continue 
            index =parent .children .index (current )
            if any (isinstance (sibling ,_Node )and sibling .tag =="span"for sibling in parent .children [index +1 :]):
                return True 
        return False 


SOURCE =ManhwaLatinoSource

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
