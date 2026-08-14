
class MadaraDetailsSource :
    pass 


class OnfmangasSource (MadaraDetailsSource ):
    """Capitulos y paginas viajan como JSON en hexadecimal dentro de un script."""

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("tab","Categoría principal","select",[
        ("general","General"),("yuri","GL / Yuri"),
        ("yaoi","BL / Yaoi"),("doujinshi","Doujinshis"),
        ],"general"),
        SourceFilter ("genero","Género","select",list (_ONF_GENRES ),"0"),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind =="popular":
            response =await self ._fetch ("GET",f"{self .base_url }/populares.php")
            root =_parse_html (response .text )
            base =str (response .url )or self .base_url 
            items :list [SourceSeries ]=[]
            for anchor in root .descendants ("a"):
                if not (anchor .has_class ("pop-podium-card")or anchor .has_class ("pop-card")):
                    continue 
                heading =_first (
                anchor ,
                lambda node :node .has_class ("pop-podium-name")or node .has_class ("pop-name"),
                )
                href =anchor .attrs .get ("href","")
                if heading is None or not heading .text ().strip ()or not href :
                    continue 
                image =_first (anchor ,lambda node :node .tag =="img")
                items .append (
                SourceSeries (
                source_id =urlparse (urljoin (base ,href )).path .lstrip ("/"),
                title =heading .text ().strip (),
                source_name =self .name ,
                cover_url =urljoin (base ,image .attrs .get ("src",""))if image is not None else None ,
                web_url =urljoin (base ,href ),
                )
                )
            return {"items":items ,"has_more":False }
        if kind =="latest":
            return await self ._grid ([
            ("tab","general"),("genero","0"),("q",""),("page",str (page )),
            ])
        return {"items":[],"has_more":False }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        values =filters or {}
        params :list [tuple [str ,str ]]=[
        ("q",query ),("page",str (page )),("tab",str (values .get ("tab")or "general")),
        ]
        genre =str (values .get ("genero")or "0")
        # El sitio espera "generos[0]"; la categoria "0" no se envia.
        if genre !="0":
            params .append (("generos[0]",genre ))
        return await self ._grid (params )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._fetch ("GET",urljoin (f"{self .base_url }/",series_id ))
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        heading =_first (root ,lambda node :node .has_class ("manga-title"))
        if heading is None or not heading .text ().strip ():
            raise SourceNotFoundError (f"{self .display_name }: ficha sin titulo")
        author =_first (root ,lambda node :node .has_class ("author-link"))
        summary =_first (root ,lambda node :node .has_class ("manga-description"))
        poster =_first (root ,lambda node :node .has_class ("manga-poster"))
        badges =[
        node 
        for holder in root .descendants ("div")
        if holder .has_class ("manga-meta")
        for node in holder .descendants ("span")
        ]
        text =badges [-1 ].text ().casefold ()if badges else ""
        return SourceSeries (
        source_id =series_id ,
        title =heading .text ().strip (),
        source_name =self .name ,
        cover_url =urljoin (base ,poster .attrs .get ("src",""))if poster is not None else None ,
        description =(summary .text ().strip ()if summary is not None else None )or None ,
        author =(author .text ().strip ()if author is not None else None )or None ,
        status ="ongoing"if "emisión"in text else "completed"if "finalizado"in text else None ,
        content_tags =tuple (
        value 
        for node in root .descendants ()
        if node .has_class ("genre-tag")and (value :=node .text ().strip ())
        ),
        web_url =urljoin (f"{self .base_url }/",series_id ),
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._fetch ("GET",urljoin (f"{self .base_url }/",series_id ))
        entries =self ._hex_payload (response .text ,_ONF_HEX_CHAPTERS )
        # El sitio ordena en el cliente: numero descendente y luego fecha.
        entries .sort (
        key =lambda item :(self ._number (item ),str (item .get ("fecha_subida")or "")),
        reverse =True ,
        )
        result :list [SourceChapter ]=[]
        for item in entries :
            result .append (self ._chapter (item ,None ,series_id ))
            for other in item .get ("otras_versiones")or []:
                if isinstance (other ,dict ):
                    result .append (self ._chapter (other ,item ,series_id ))
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._fetch ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        result :list [SourcePage ]=[]
        for index ,item in enumerate (self ._hex_payload (response .text ,_ONF_HEX_PAGES )):
            source =str (item .get ("src")or "")
            if not source :
                continue 
            fallback =str (item .get ("fallback")or "").strip ()
            result .append (
            SourcePage (
            source_id =f"{source }#fallback={fallback }"if fallback else source ,
            chapter_id =chapter_id ,
            index =index ,
            filename =urlparse (source ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",
            source_name =self .name ,
            )
            )
        return result 

        # Las paginas de este sitio son hotlinks a la red de MangaDex. Esos hosts miran el
        # Referer: si llega `onfmangas.com` devuelven un 200 con una imagen-aviso de ~59 KB
        # ("you can read this at mangadex.org") en lugar de la pagina real de ~700 KB. Por eso
        # el capitulo se veia bien en el WebView (que no manda ese Referer al CDN) y salia el
        # disclaimer en la app. Sin Referer sirven la imagen correcta.
    _HOSTS_MANGADEX =("mangadex.org","mangadex.network")

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else str (page )
        source ,_ ,fragment =url .partition ("#fallback=")
        try :
            return await self ._pagina (page ,source if fragment else url )
        except Exception :
            if not fragment :
                raise 
                # El origen principal falla a menudo; el sitio publica un respaldo.
            return await self ._pagina (page ,fragment )

    async def _pagina (self ,page :SourcePage |str ,url :str )->SourcePageContent :
        host =urlparse (url ).netloc 
        if not any (host .endswith (dominio )for dominio in self ._HOSTS_MANGADEX ):
            return await super ().page_bytes (url )
            # OJO: no basta con mandar `headers={}`. El fetcher fusiona lo que se le pasa con
            # las cabeceras de `capabilities`, que ya llevan el Referer del sitio, asi que hay
            # que SOBREESCRIBIRLO. Se pone el propio MangaDex, que es lo que ve el CDN cuando
            # el capitulo se abre en el WebView y devuelve la imagen completa.
        response =await self ._request (
        "GET",url ,headers ={"Referer":"https://mangadex.org/"},
        )
        response .raise_for_status ()
        return SourcePageContent (
        media_type =response .headers .get ("content-type","image/jpeg"),
        chunks =iter ([response .content ]),
        )

        # -------------------------------------------------------------- internals
    async def _grid (self ,params :list [tuple [str ,str ]])->dict :
        response =await self ._fetch ("GET",f"{self .base_url }/mangas.php",params =params )
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        items :list [SourceSeries ]=[]
        for grid in root .descendants ("div"):
            if not grid .has_class ("manga-grid"):
                continue 
            for card in grid .descendants ("div"):
                if not card .has_class ("manga-card"):
                    continue 
                heading =_first (card ,lambda node :node .has_class ("manga-title"))
                anchor =_first (card ,lambda node :node .tag =="a")
                if heading is None or anchor is None :
                    continue 
                title ,href =heading .text ().strip (),anchor .attrs .get ("href","")
                if not title or not href :
                    continue 
                image =next (
                (
                node 
                for holder in card .descendants ()
                if holder .has_class ("card-cover")
                for node in holder .descendants ("img")
                ),
                None ,
                )
                items .append (
                SourceSeries (
                source_id =urlparse (urljoin (base ,href )).path .lstrip ("/"),
                title =title ,
                source_name =self .name ,
                cover_url =urljoin (base ,image .attrs .get ("src",""))if image is not None else None ,
                web_url =urljoin (base ,href ),
                )
                )
        has_more =any (
        anchor .has_class ("page-btn")and "siguiente"in anchor .text ().casefold ()
        for holder in root .descendants ()
        if holder .has_class ("pagination")
        for anchor in holder .descendants ("a")
        )
        return {"items":items ,"has_more":has_more }

    async def _fetch (self ,method :str ,url :str ,**kwargs :Any )->Any :
        response =await self ._request (method ,url ,**kwargs )
        response .raise_for_status ()
        if "Verificando"in (response .text or "")[:8192 ]:
        # El reto es un script que fija una cookie; aqui no hay motor JS.
            raise ValueError (
            f"{self .display_name } está pidiendo una verificación: ábrelo en WebView y vuelve a intentarlo",
            )
        return response 

    def _chapter (self ,item :dict ,parent :dict |None ,series_id :str )->SourceChapter :
        number =item .get ("numero")or (parent or {}).get ("numero")
        title =(
        item .get ("titulo_str")
        or (parent or {}).get ("titulo_str")
        or (f"Capítulo {number }"if number else "Capítulo sin número")
        )
        groups =[
        str (group .get ("nombre")or "")
        for group in item .get ("grupos_list")or []
        if isinstance (group ,dict )
        ]
        stamp =(item if parent is None else parent ).get ("fecha_subida")
        return SourceChapter (
        source_id =str (item .get ("url")or "").lstrip ("/"),
        title =str (title ),
        series_id =series_id ,
        source_name =self .name ,
        number =self ._number (item ),
        language =self .language ,
        scanlator =" & ".join (groups ),
        uploaded_at =self ._date (stamp ),
        )

    @staticmethod 
    def _hex_payload (text :str ,pattern :Any )->list [dict ]:
        found =pattern .search (text or "")
        if not found or len (found .group (1 ))%2 :
            return []
        try :
            decoded =bytes .fromhex (found .group (1 )).decode ("utf-8")
            value =json .loads (decoded )
        except (ValueError ,UnicodeDecodeError ):
            return []
        return [item for item in value or []if isinstance (item ,dict )]

    @staticmethod 
    def _number (item :dict )->float :
        try :
            return float (item .get ("numero"))
        except (TypeError ,ValueError ):
            return 0.0 

    @staticmethod 
    def _date (value :Any )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        try :
            return datetime .strptime (str (value ),"%Y-%m-%d %H:%M:%S").isoformat ()
        except ValueError :
            return None 


class GeneratedOnfMangasSource (OnfMangasSource ):
    name ='onfmangas_es'
    display_name ='ONF MANGAS'
    base_url ='https://onfmangas.com'
    language ='es'
    requests_per_minute =60 
    content_warning ='mixed'
    extra_headers ={
    'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0',
    'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language':'en-US,en;q=0.9',
    'Sec-Fetch-Site':'none',
    }
    image_headers ={'Referer':'https://onfmangas.com/'}


SOURCE =OnfmangasSource

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
