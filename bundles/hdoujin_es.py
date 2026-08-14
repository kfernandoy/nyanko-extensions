from __future__ import annotations


class MadaraDetailsSource :
    pass 


class HdoujinSource (MadaraDetailsSource ):
# Mascara del idioma que sirve esta variante; 0 = sin filtro (todos).
    language_mask =0 

    def _cabeceras (self )->dict [str ,str ]:
        return {"Referer":f"{self .base_url }/","Origin":self .base_url }

    async def _pedir (self ,path :str ,params :dict |None =None )->dict :
        response =await self ._request (
        "GET",f"{_API }{path }",params =params or {},headers =self ._cabeceras (),
        )
        response .raise_for_status ()
        try :
            return response .json ()or {}
        except ValueError :
            return {}

    def _serie (self ,fila :dict )->SourceSeries |None :
        if not (fila .get ("id")and fila .get ("key")):
            return None 
        miniatura =fila .get ("thumbnail")or {}
        portada =miniatura .get ("path")if isinstance (miniatura ,dict )else None 
        return SourceSeries (
        source_id =f"{fila ['id']}/{fila ['key']}",
        title =str (fila .get ("title")or "").strip ()or str (fila .get ("id")),
        source_name =self .name ,
        cover_url =portada ,
        web_url =f"{self .base_url }/g/{fila ['id']}/{fila ['key']}",
        )

    def _listado (self ,payload :dict )->dict :
        filas =payload .get ("entries")or []
        items =[serie for fila in filas if (serie :=self ._serie (fila ))]
        # `total` es el global; se pagina mientras la pagina venga llena.
        limite =int (payload .get ("limit")or 0 )
        return {"items":items ,"has_more":bool (limite )and len (filas )>=limite }

    def _parametros (self ,page :int )->dict :
        params :dict [str ,object ]={"page":max (page ,1 )}
        if self .language_mask :
            params ["lang"]=self .language_mask 
        return params 

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        ruta ="/books/popular"if kind =="popular"else "/books"
        return self ._listado (await self ._pedir (ruta ,self ._parametros (page )))

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        params =self ._parametros (page )
        if query .strip ():
            params ["s"]=query .strip ()
        return self ._listado (await self ._pedir ("/books",params ))

    @staticmethod 
    def _nombres (tags :object ,espacio :str )->list [str ]:
        return [
        str (tag .get ("name")or "").strip ()
        for tag in (tags if isinstance (tags ,list )else [])
        if isinstance (tag ,dict )
        and str (tag .get ("namespace")or "")==espacio 
        and str (tag .get ("name")or "").strip ()
        ]

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        data =await self ._pedir (f"/books/detail/{series_id }")
        miniaturas =data .get ("thumbnails")or {}
        base =str (miniaturas .get ("base")or "")
        principal =miniaturas .get ("main")or {}
        etiquetas =data .get ("tags")
        todas =[
        str (tag .get ("name")or "").strip ()
        for tag in (etiquetas if isinstance (etiquetas ,list )else [])
        if isinstance (tag ,dict )and str (tag .get ("name")or "").strip ()
        ]
        return SourceSeries (
        source_id =series_id ,
        title =str (data .get ("title")or "").strip ()or series_id ,
        source_name =self .name ,
        cover_url =f"{base }{principal .get ('path','')}"if base and principal else None ,
        description =str (data .get ("subtitle")or "").strip ()or None ,
        author =", ".join (self ._nombres (etiquetas ,"artist"))or None ,
        artist =", ".join (self ._nombres (etiquetas ,"artist"))or None ,
        content_tags =tuple (dict .fromkeys (todas )),
        web_url =f"{self .base_url }/g/{series_id }",
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        data =await self ._pedir (f"/books/detail/{series_id }")
        # Cada libro es una galeria de un solo capitulo.
        return [
        SourceChapter (
        source_id =series_id ,
        title =str (data .get ("title_short")or data .get ("title")or "Galería"),
        series_id =series_id ,
        source_name =self .name ,
        number =1.0 ,
        language =self .language ,
        )
        ]

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        data =await self ._pedir (f"/books/detail/{chapter_id }")
        miniaturas =data .get ("thumbnails")or {}
        base =str (miniaturas .get ("base")or "")
        entradas =miniaturas .get ("entries")or []
        paginas :list [SourcePage ]=[]
        for indice ,entrada in enumerate (entradas ):
            ruta =str (entrada .get ("path")or "")if isinstance (entrada ,dict )else ""
            if not ruta :
                continue 
            url =f"{base }{ruta }"
            paginas .append (
            SourcePage (
            source_id =url ,
            chapter_id =chapter_id ,
            index =indice ,
            filename =urlparse (url ).path .rsplit ("/",1 )[-1 ]or f"{indice }.webp",
            source_name =self .name ,
            )
            )
        return paginas 



SOURCE =HdoujinSource

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
