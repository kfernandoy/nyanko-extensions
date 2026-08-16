from __future__ import annotations

try :
    from .mangathemesia import (
    MangaThemesiaSource ,_Node ,_TreeParser 
    )
except ImportError :
    pass 

class MangaThemesiaSource :
    pass 


class MangashiinaSource (MangaThemesiaSource ):
    def __init__ (self ,fetcher =None )->None :
        super ().__init__ (fetcher )
        self ._catalogo :list [dict ]|None =None 

    async def _pedir (self ,path :str ,params :dict |None =None )->dict :
        response =await self ._request (
        "GET",f"{self .base_url }{_API }{path }",params =params or {},
        )
        response .raise_for_status ()
        try :
            return response .json ()or {}
        except ValueError :
            return {}

    async def _todo_el_catalogo (self )->list [dict ]:
        if self ._catalogo is None :
            datos =await self ._pedir ("/catalog")
            self ._catalogo =[m for m in (datos .get ("mangas")or [])if m .get ("id")]
        return self ._catalogo 

    def _serie (self ,fila :dict )->SourceSeries :
        generos =fila .get ("genres")
        return SourceSeries (
        source_id =str (fila ["id"]),
        title =str (fila .get ("titulo")or "").strip ()or str (fila ["id"]),
        source_name =self .name ,
        # Las portadas vienen con esquema http; se fuerza https para que no las
        # bloquee el cliente.
        cover_url =str (fila .get ("portada")or "").replace ("http://","https://")or None ,
        description =str (fila .get ("descripcion")or "").strip ()or None ,
        status =str (fila .get ("status")or "").strip ()or None ,
        content_tags =tuple (generos )if isinstance (generos ,list )else (),
        web_url =f"{self .base_url }/manga/{fila ['id']}",
        )

    def _pagina (self ,filas :list [dict ],page :int )->dict :
        inicio =(max (page ,1 )-1 )*_POR_PAGINA 
        trozo =filas [inicio :inicio +_POR_PAGINA ]
        return {
        "items":[self ._serie (fila )for fila in trozo ],
        "has_more":inicio +_POR_PAGINA <len (filas ),
        }

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        filas =list (await self ._todo_el_catalogo ())
        if kind =="latest":
            filas .sort (key =lambda fila :str (fila .get ("latest_update_at")or ""),reverse =True )
        return self ._pagina (filas ,page )

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        consulta =query .strip ().casefold ()
        filas =await self ._todo_el_catalogo ()
        if consulta :
            filas =[
            fila for fila in filas 
            if consulta in str (fila .get ("titulo")or "").casefold ()
            ]
        return self ._pagina (list (filas ),page )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        datos =await self ._pedir (f"/manga/{series_id }")
        fila =datos .get ("manga")if isinstance (datos .get ("manga"),dict )else datos 
        if not fila .get ("id"):
        # Respaldo: el catalogo ya trae la ficha completa.
            fila =next (
            (m for m in await self ._todo_el_catalogo ()if str (m ["id"])==series_id ),
            {"id":series_id },
            )
        return self ._serie (fila )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        datos =await self ._pedir (f"/series/{series_id }/chapters")
        capitulos :list [SourceChapter ]=[]
        for fila in datos .get ("chapters")or []:
            if not fila .get ("id"):
                continue 
            try :
                numero =float (fila .get ("chapter_number")or 0 )
            except (TypeError ,ValueError ):
                numero =0.0 
            capitulos .append (
            SourceChapter (
            source_id =str (fila ["id"]),
            title =str (fila .get ("title")or f"Capítulo {numero :g}"),
            series_id =series_id ,
            source_name =self .name ,
            number =numero ,
            language =self .language ,
            )
            )
        capitulos .sort (key =lambda capitulo :capitulo .number ,reverse =True )
        return capitulos 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        # Ojo: el parametro es `id`; con `chapter_id` la API responde 400.
        respuesta =await self ._request (
        "GET",f"{self .base_url }{_API }/chapters/content",params ={"id":chapter_id },
        )
        if respuesta .status_code ==401 :
        # Los capitulos de pago responden 401 con `locked: true`. Se traduce a un
        # error del contrato para que la app muestre el motivo en vez de un fallo
        # de red generico.
            raise SourceUnsupportedError (
            f"{self .display_name }: capítulo de pago, requiere cuenta con monedas"
            )
        respuesta .raise_for_status ()
        try :
            datos =respuesta .json ()or {}
        except ValueError :
            datos ={}
        paginas :list [SourcePage ]=[]
        for indice ,url in enumerate (datos .get ("images")or []):
            if not url :
                continue 
            paginas .append (
            SourcePage (
            source_id =str (url ).replace ("http://","https://"),
            chapter_id =chapter_id ,
            index =indice ,
            filename =str (url ).rsplit ("/",1 )[-1 ]or f"{indice +1 :03d}.webp",
            source_name =self .name ,
            )
            )
        return paginas 



SOURCE =MangashiinaSource

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
