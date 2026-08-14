
class MadaraDetailsSource :
    pass 



class DoujinsHellSource (MadaraDetailsSource ):
    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("author","Autor","text",default =""),
        SourceFilter ("artist","Artista","text",default =""),
        SourceFilter ("year","Ano de publicacion","text",default =""),
        SourceFilter ("status","Estado","multi_select",[
        ("end","Completado"),("on-going","En curso"),
        ("canceled","Cancelado"),("on-hold","En espera"),
        ],[]),
        SourceFilter ("order","Ordenar por","select",[
        ("","Relevancia"),("latest","Mas recientes"),("alphabet","A-Z"),
        ("rating","Valoracion"),("trending","Tendencia"),
        ("views","Mas vistos"),("new-manga","Nuevos"),
        ],""),
        SourceFilter ("adult","Contenido adulto","select",[
        ("","Todo"),("0","Excluir"),("1","Solo adulto"),
        ],""),
        ]

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        values =filters or {}
        path =""if page ==1 else f"page/{page }/"
        params :list [tuple [str ,str ]]=[("s",query ),("post_type","wp-manga")]
        for key ,parameter in (("author","author"),("artist","artist"),("year","release")):
            if str (values .get (key ,"")).strip ():
                params .append ((parameter ,str (values [key ]).strip ()))
        statuses =values .get ("status",[])
        if isinstance (statuses ,list ):
            params .extend (("status[]",str (status ))for status in statuses )
        if values .get ("order"):
            params .append (("m_orderby",str (values ["order"])))
        params .append (("adult",str (values .get ("adult",""))))
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",path ),params =params )
        response .raise_for_status ()
        root =_parse_html (response .text )
        items =self ._series_from_root (root ,("c-tabs-item__content","manga__item"))
        has_more =any (
        node .attrs .get ("rel")=="next"or node .has_class ("nextpostslink")
        or node .has_class ("nav-previous")
        for node in root .descendants ()
        )
        return {"items":items ,"has_more":has_more }

    @staticmethod 
    def _doujinshell_date (value :str )->str |None :
        from datetime import datetime 
        months ={
        "enero":1 ,"febrero":2 ,"marzo":3 ,"abril":4 ,"mayo":5 ,"junio":6 ,
        "julio":7 ,"agosto":8 ,"septiembre":9 ,"octubre":10 ,
        "noviembre":11 ,"diciembre":12 ,
        }
        found =re .fullmatch (r"(\d{1,2})\s+([^,]+),\s*(\d{4})",value .strip ().lower ())
        if not found or found .group (2 )not in months :
            return None 
        return datetime (int (found .group (3 )),months [found .group (2 )],int (found .group (1 ))).isoformat ()

    @classmethod 
    def _doujinshell_chapter_nodes (cls ,root ):
        return [
        node for node in root .descendants ("li")
        if node .has_class ("wp-manga-chapter")and cls ._has_class_ancestor (node ,"listing-chapters_wrap")
        ]

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else series 
        series_url =urljoin (f"{self .base_url }/",series_id )
        response =await self ._request ("GET",series_url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        items =self ._doujinshell_chapter_nodes (root )
        holder =_first (root ,lambda node :node .attrs .get ("id","").startswith ("manga-chapters-holder"))
        if not items and holder is not None :
            chapter_response =await self ._request (
            "POST",f"{self .base_url }/wp-admin/admin-ajax.php",
            data ={"action":"manga_get_chapters","manga":holder .attrs .get ("data-id","")},
            )
            if getattr (chapter_response ,"status_code",200 )==400 :
                chapter_response =await self ._request ("POST",f"{series_url .rstrip ('/')}/ajax/chapters")
            chapter_response .raise_for_status ()
            items =self ._doujinshell_chapter_nodes (_parse_html (chapter_response .text ))
        result =[]
        for item in items :
            anchor =_first (item ,lambda node :node .tag =="a"and bool (node .attrs .get ("href")))
            if anchor is None :
                continue 
            title =anchor .text ().strip ()
            date =_first (item ,lambda node :node .tag =="span"and node .has_class ("chapter-release-date"))
            found =re .search (r"\d+(?:\.\d+)?",title )
            chapter_url =urljoin (series_url ,anchor .attrs ["href"]).split ("?style=paged",1 )[0 ]
            if not chapter_url .endswith (self .chapter_url_suffix ):
                chapter_url +=self .chapter_url_suffix 
            result .append (SourceChapter (
            source_id =chapter_url ,title =title ,series_id =series_id ,source_name =self .name ,
            number =float (found .group ())if found else None ,language =self .language ,
            uploaded_at =self ._doujinshell_date (date .text ())if date else None ,
            ))
        if len (result )==1 :
            only =result [0 ]
            result [0 ]=SourceChapter (
            source_id =only .source_id ,title ="Cap\u00edtulo",series_id =only .series_id ,
            source_name =only .source_name ,number =only .number ,language =only .language ,
            uploaded_at =only .uploaded_at ,
            )
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else chapter 
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        reading =_first (root ,lambda node :node .has_class ("reading-content"))
        images =[image for image in reading .descendants ("img")if not image .has_class ("aligncenter")]if reading else []
        if not images and reading and reading .descendants ("iframe"):
            raise ValueError ("No se admiten videos")
        urls =[_image_url (image ,str (response .url ))for image in images ]
        return [SourcePage (
        source_id =url ,chapter_id =chapter_id ,index =index ,
        filename =urlparse (url ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",source_name =self .name ,
        )for index ,url in enumerate (urls )]
class GeneratedMadaraSource (DoujinsHellSource ):
    name ='doujinshell_es'
    display_name ='DoujinsHell'
    base_url ='https://doujinshell.net'
    language ='es'
    manga_substring ='doujin'
    load_more ='never'
    use_new_chapter_endpoint =False 
    chapter_url_suffix ='?style=list'
    supports_latest =True 
    requests_per_minute =60 
    pages_profile ='default'
    extra_headers ={}
    image_headers ={}
    date_format ='d MMMM, yyyy'
    date_locale ='es'
    details_profile ='default'
    content_warning ='nsfw'

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
