from __future__ import annotations


class MadaraDetailsSource :
    pass 



class MangaCrabSource (MadaraDetailsSource ):
    async def browse (self ,kind :str ,page :int =1 )->list [SourceSeries ]:
        if kind =="popular":
            if page >1 :
                return []
            response =await self ._request ("GET",self .base_url )
            profile ="popular"
        elif kind =="latest":
            response =await self ._request ("GET",f"{self .base_url }/page/{page }/")
            profile ="latest"
        else :
            return []
        response .raise_for_status ()
        return self ._crab_series (_parse_html (response .text ),profile ,str (response .url ))

    async def search (self ,query :str ,limit :int =20 )->list [SourceSeries ]:
        response =await self ._request ("GET",f"{self .base_url }/page/1/",params ={"s":query .strip ()})
        response .raise_for_status ()
        return self ._crab_series (_parse_html (response .text ),"search",str (response .url ))[:limit ]

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        series_url =urljoin (f"{self .base_url }/",series_id )
        response =await self ._request ("GET",series_url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        holder =_first (root ,lambda node :node .attrs .get ("id")=="mv-chapter-list")
        manga_id =holder .attrs .get ("data-manga-id","")if holder else ""
        manga_id_match =re .search (r'''["']manga_id["']\s*:\s*["']?(\d+)''',response .text )
        manga_id =manga_id or (manga_id_match .group (1 )if manga_id_match else "")
        nonce_match =re .search (
        r'''var\s+mvTheme\s*=\s*\{[^}]*["']nonce["']\s*:\s*["']([^"']+)''',
        response .text ,
        )or re .search (r'''["']nonce["']\s*:\s*["']([^"']+)''',response .text )
        chapters :list [SourceChapter ]=[]
        if manga_id and nonce_match :
            page =1 
            seen :set [str ]=set ()
            while True :
                try :
                    chapter_response =await self ._request (
                    "POST",
                    f"{self .base_url }/wp-admin/admin-ajax.php",
                    data ={
                    "action":"mv_get_chapters","nonce":nonce_match .group (1 ),
                    "manga_id":manga_id ,"page":str (page ),"search":"",
                    },
                    headers ={"X-Requested-With":"XMLHttpRequest"},
                    )
                    chapter_response .raise_for_status ()
                    payload =chapter_response .json ()if hasattr (chapter_response ,"json")else json .loads (chapter_response .text )
                except Exception :
                    break 
                success =payload .get ("success")
                if success not in {True ,"true"}:
                    break 
                data =payload .get ("data")or {}
                anchors =self ._crab_chapter_anchors (_parse_html (data .get ("list")or ""))
                fresh =[chapter for anchor in anchors if (chapter :=self ._crab_chapter (anchor ,series_id ,series_url )).source_id not in seen ]
                if not fresh :
                    break 
                chapters .extend (fresh )
                seen .update (chapter .source_id for chapter in fresh )
                page +=1 
        if chapters :
            return chapters 
        return [self ._crab_chapter (anchor ,series_id ,series_url )for anchor in self ._crab_chapter_anchors (root )]

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        response .raise_for_status ()
        header =re .search (r'''["']imgHeader["']\s*:\s*["']([^"']+)["']''',response .text )
        urls =[]
        for image in _parse_html (response .text ).descendants ("img"):
            page_break =self ._ancestor_with_class (image ,"page-break")
            if not (
            image .has_class ("mv-secure-img")
            or self ._has_class_ancestor (image ,"reader-body")
            or self ._has_id_ancestor (image ,"mv-reader-body")
            or page_break is not None and "display:none"not in page_break .attrs .get ("style","").replace (" ","")and not image .attrs .get ("src")
            ):
                continue 
            if url :=_image_url (image ,str (response .url )):
                urls .append (f"{url }#nodeHeader={header .group (1 )}"if header else url )
        return [SourcePage (
        source_id =url ,
        chapter_id =chapter_id ,
        index =index ,
        filename =urlparse (url ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",
        source_name =self .name ,
        )for index ,url in enumerate (dict .fromkeys (urls ),1 )]

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else str (page )
        parsed =urlparse (url )
        headers ={"Referer":page .chapter_id }if isinstance (page ,SourcePage )else {}
        if parsed .fragment .startswith ("nodeHeader="):
            headers ["Node"]=unquote (parsed .fragment .removeprefix ("nodeHeader="))
        response =await self ._request ("GET",urlunparse (parsed ._replace (fragment ="")),headers =headers )
        response .raise_for_status ()
        return SourcePageContent (
        media_type =response .headers .get ("Content-Type","image/jpeg"),
        chunks =iter ([response .content ]),
        )

    def _crab_series (self ,root :_Node ,profile :str ,base_url :str )->list [SourceSeries ]:
        wanted ={
        "popular":("mv-rank-item",),
        "latest":("manga-row",),
        "search":("catalog-card","mv-recent-card","manga-row","manga__item"),
        }[profile ]
        result :list [SourceSeries ]=[]
        for item in root .descendants ():
            if not any (item .has_class (value )for value in wanted ):
                continue 
            preferred ="manga-row-cover"if profile =="latest"else "mv-recent-link"if profile =="search"else ""
            anchor =_first (item ,lambda node :node .tag =="a"and bool (node .attrs .get ("href"))and (not preferred or node .has_class (preferred )))
            if anchor is None :
                anchor =_first (item ,lambda node :node .tag =="a"and bool (node .attrs .get ("href")))
            title =_first (item ,lambda node :node .has_class ("mv-rank-title")or node .has_class ("mv-recent-name")or node .tag in {"h2","h5"})
            if anchor is None :
                continue 
            source_id =urljoin (base_url ,anchor .attrs ["href"])
            image =_first (item ,lambda node :node .tag =="img")
            result .append (SourceSeries (
            source_id =source_id ,
            title =(title .text ()if title else anchor .text ()).strip (),
            source_name =self .name ,
            cover_url =_image_url (image ,base_url )if image else None ,
            web_url =source_id ,
            ))
        return list ({item .source_id :item for item in result }.values ())

    def _crab_chapter_anchors (self ,root :_Node )->list [_Node ]:
        return [
        anchor for anchor in root .descendants ("a")if anchor .attrs .get ("href")
        and (
        self ._has_class_ancestor (anchor ,"chapter-item")
        or self ._has_id_ancestor (anchor ,"mv-chapter-list")
        )
        ]

    def _crab_chapter (self ,anchor :_Node ,series_id :str ,series_url :str )->SourceChapter :
        title =anchor .text ().strip ()
        number =re .search (r"\d+(?:\.\d+)?",title )
        return SourceChapter (
        source_id =urljoin (series_url ,anchor .attrs ["href"]),
        title =title or "Capítulo",
        series_id =series_id ,
        source_name =self .name ,
        number =float (number .group ())if number else None ,
        language =self .language ,
        )

    @staticmethod 
    def _ancestor_with_class (node :_Node ,class_name :str )->_Node |None :
        parent =node .parent 
        while parent is not None :
            if parent .has_class (class_name ):
                return parent 
            parent =parent .parent 
        return None 
class GeneratedMadaraSource (MangaCrabSource ):
    name ='mangacrab_es'
    display_name ='Manga Crab'
    base_url ='https://mangacrab.org'
    language ='es'
    manga_substring ='series'
    load_more ='never'
    use_new_chapter_endpoint =False 
    chapter_url_suffix ='?style=list'
    supports_latest =True 
    requests_per_minute =300 
    pages_profile ='default'
    extra_headers ={}
    image_headers ={}
    date_format ='dd/MM/yyyy'
    date_locale ='es'
    details_profile ='default'
    content_warning ='safe'

SOURCE =GeneratedMadaraSource

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
