from __future__ import annotations


class MadaraDetailsSource :
    pass 


def _raven_kids (node :_Node ,tag :str ,class_name :str |None =None )->list [_Node ]:
    return [
    child 
    for child in node .children 
    if isinstance (child ,_Node )
    and child .tag ==tag 
    and (class_name is None or child .has_class (class_name ))
    ]


class RavenmangaSource (MadaraDetailsSource ):
    """El sitio no pagina populares ni recientes: ambos salen de la portada."""

    def get_filters (self )->list [SourceFilter ]:
        return []

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        response =await self ._request ("GET",self .base_url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        if kind =="popular":
        # Los rankings diario/semanal/mensual de la home son 11 series fijas y sin
        # paginar, asi que el catalogo se acababa ahi aunque el sitio tiene mas de
        # 100. Se dejan como cabecera y se continua por la biblioteca, que si pagina.
            figures :list [_Node ]=[]
            if page ==1 :
                for identifier in ("div-diario","div-semanal","div-mensual"):
                    holder =_first (
                    root ,
                    lambda node ,identifier =identifier :node .tag =="div"
                    and node .attrs .get ("id")==identifier ,
                    )
                    if holder is not None :
                        figures .extend (holder .descendants ("figure"))
            destacados =self ._figures (figures ,base )

            catalogo =await self .search ("",page ,{})
            if isinstance (catalogo ,dict ):
                series ,hay_mas =catalogo .get ("items",[]),catalogo .get ("has_more",False )
            else :
                series ,hay_mas =getattr (catalogo ,"items",[]),getattr (catalogo ,"has_more",False )

            vistos ={serie .source_id for serie in destacados }
            return {
            "items":destacados +[s for s in series if s .source_id not in vistos ],
            "has_more":hay_mas ,
            }
        figures =self ._grid_figures (root )
        return {"items":self ._figures (figures ,base ),"has_more":False }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        query =query .strip ()
        if query :
            if len (query )<2 :
                raise ValueError ("La búsqueda debe tener al menos 2 caracteres")
                # El buscador es local: /comics trae el listado completo en un script.
            response =await self ._request ("GET",f"{self .base_url }/comics")
            response .raise_for_status ()
            found =_RAVEN_PROJECTS .search (response .text )
            if found is None :
                return {"items":[],"has_more":False }
            try :
                projects =json .loads (found .group (1 ))
            except ValueError :
                return {"items":[],"has_more":False }
            needle =query .casefold ()
            return {
            "items":[
            SourceSeries (
            source_id =f"sr2/{item .get ('slug')or ''}",
            title =str (item .get ("nombre")or ""),
            source_name =self .name ,
            cover_url =str (item ["portada"])if item .get ("portada")else None ,
            web_url =urljoin (f"{self .base_url }/",f"sr2/{item .get ('slug')or ''}"),
            )
            for item in projects 
            if isinstance (item ,dict )and needle in str (item .get ("nombre")or "").casefold ()
            ],
            "has_more":False ,
            }
        response =await self ._request ("GET",f"{self .base_url }/comics",params ={"page":str (page )})
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        return {
        "items":self ._figures (self ._grid_figures (root ),base ),
        "has_more":self ._has_next (root ),
        }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        container =_first (
        root ,
        lambda node :node .tag =="section"and node .attrs .get ("id")=="section-sinopsis",
        )
        description ,tags ="",()
        if container is not None :
            description =" ".join (
            text for node in container .descendants ("p")if (text :=node .text ().strip ())
            )
            tags =tuple (self ._genres (container ))
        known =series if isinstance (series ,SourceSeries )else None 
        return SourceSeries (
        source_id =series_id ,
        title =known .title if known else series_id .rstrip ("/").rsplit ("/",1 )[-1 ],
        source_name =self .name ,
        cover_url =known .cover_url if known else None ,
        description =description or None ,
        content_tags =tags ,
        web_url =urljoin (f"{self .base_url }/",series_id ),
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        holder =_first (
        root ,
        lambda node :node .tag =="section"and node .attrs .get ("id")=="section-list-cap",
        )
        result :list [SourceChapter ]=[]
        for grid in holder .descendants ("div")if holder is not None else []:
            if not grid .has_class ("grid"):
                continue 
            for anchor in _raven_kids (grid ,"a"):
                name =_first (anchor ,lambda node :node .attrs .get ("id")=="name")
                moment =_first (anchor ,lambda node :node .tag =="time")
                title =name .text ().strip ()if name is not None else ""
                found =_RAVEN_NUMBER .search (title )
                result .append (
                SourceChapter (
                source_id =self ._path (anchor .attrs .get ("href",""),base ),
                title =title ,
                series_id =series_id ,
                source_name =self .name ,
                number =float (found .group (1 ))if found else None ,
                language =self .language ,
                uploaded_at =self ._relative_date (moment .text ()if moment is not None else ""),
                )
                )
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        url =urljoin (f"{self .base_url }/",chapter_id )
        response =await self ._request ("GET",url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or url 
        form =_first (
        root ,
        lambda node :node .tag =="form"
        and node .attrs .get ("id")=="redirectForm"
        and node .attrs .get ("method","").lower ()=="post",
        )
        if form is not None :
        # El lector queda detras de un POST de redireccion con campos ocultos.
            action =urljoin (base ,form .attrs .get ("action",""))
            response =await self ._request (
            "POST",
            action ,
            data ={
            control .attrs ["name"]:control .attrs .get ("value","")
            for control in form .descendants ("input")
            if control .attrs .get ("name")
            },
            headers ={"Referer":base },
            )
            response .raise_for_status ()
            root =_parse_html (response .text )
            base =str (response .url )or action 
        urls :list [str ]=[]
        for main in root .descendants ("main"):
            if main .has_class ("contenedor-imagen"):
                for section in _raven_kids (main ,"section"):
                    urls .extend (
                    _image_url (node ,base )
                    for node in section .descendants ("img")
                    if node .attrs .get ("src")
                    )
            urls .extend (
            _image_url (node ,base )
            for node in _raven_kids (main ,"img")
            if node .attrs .get ("src")
            )
        return [
        SourcePage (
        source_id =value ,
        chapter_id =chapter_id ,
        index =index ,
        filename =urlparse (value ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",
        source_name =self .name ,
        )
        for index ,value in enumerate (dict .fromkeys (value for value in urls if value ))
        ]

    def _grid_figures (self ,root :_Node )->list [_Node ]:
        result :list [_Node ]=[]
        for section in root .descendants ("section"):
            if not section .has_class ("flex"):
                continue 
            for grid in _raven_kids (section ,"div","grid"):
                result .extend (_raven_kids (grid ,"figure"))
        return result 

    def _figures (self ,figures :list [_Node ],base :str )->list [SourceSeries ]:
        result :dict [str ,SourceSeries ]={}
        for figure in figures :
            anchor =_first (figure ,lambda node :node .tag =="a")
            if anchor is None :
                continue 
            slug =self ._path (anchor .attrs .get ("href",""),base )
            if not slug or slug in result :
                continue 
            image =_first (figure ,lambda node :node .tag =="img")
            caption =_first (figure ,lambda node :node .tag =="figcaption")
            result [slug ]=SourceSeries (
            source_id =slug ,
            title =caption .text ().strip ()if caption is not None else "",
            source_name =self .name ,
            cover_url =_image_url (image ,base )or None if image is not None else None ,
            web_url =urljoin (f"{self .base_url }/",slug ),
            )
        return list (result .values ())

    def _genres (self ,container :_Node )->list [str ]:
        for block in container .descendants ("div"):
            if not block .has_class ("flex"):
                continue 
            label =_first (
            block ,lambda node :node .tag =="div"and "géneros"in node .text ().casefold (),
            )
            if label is None :
                continue 
            return [
            text 
            for anchor in block .descendants ("a")
            for node in anchor .descendants ("span")
            if (text :=node .text ().strip ())
            ]
        return []

    @staticmethod 
    def _has_next (root :_Node )->bool :
        for nav in root .descendants ("nav"):
            for holder in _raven_kids (nav ,"ul"):
                if not holder .has_class ("pagination"):
                    continue 
                for item in _raven_kids (holder ,"li"):
                    for anchor in _raven_kids (item ,"a"):
                        if anchor .attrs .get ("rel")=="next":
                            return True 
        return False 

    @staticmethod 
    def _path (href :str ,base :str )->str :
        return urlparse (urljoin (base ,href )).path .lstrip ("/")

    @staticmethod 
    def _relative_date (value :str )->str |None :
        from datetime import datetime ,timedelta 

        found =_RAVEN_NUMBER .search (value )
        if not found :
            return None 
        amount ,lowered =int (found .group (1 )),value .casefold ()
        for words ,unit in _RAVEN_UNITS :
            if not any (word in lowered for word in words ):
                continue 
            now =datetime .now ().replace (microsecond =0 )
            if unit =="months":
                return (now -timedelta (days =30 *amount )).isoformat ()
            if unit =="years":
                return (now -timedelta (days =365 *amount )).isoformat ()
            return (now -timedelta (**{unit :amount })).isoformat ()
        return None 


class GeneratedRavenMangaSource (RavenMangaSource ):
    name ='ravenmanga_es'
    display_name ='RavenManga'
    base_url ='https://raventard.xyz'
    language ='es'
    requests_per_minute =120 
    content_warning ='safe'
    image_headers ={'Referer':'https://raventard.xyz/'}


SOURCE =RavenmangaSource

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
