from __future__ import annotations

try :
    from .madara import (
    FuenteBaseSource ,_Node ,_TreeParser 
    )
except ImportError :
    pass 

class FuenteBaseSource :
    pass 


class TraduccionesmoonlightSource (FuenteBaseSource ):
    def __init__ (self ,fetcher =None )->None :
        super ().__init__ (fetcher )
        self ._catalogo :list [dict ]|None =None 

    async def _json (self ,path :str )->dict :
        response =await self ._request ("GET",f"{self .base_url }{path }")
        response .raise_for_status ()
        try :
            return response .json ()or {}
        except ValueError :
            return {}

    async def _todo_el_catalogo (self )->list [dict ]:
        if self ._catalogo is None :
            datos =await self ._json ("/api/comics")
            respuesta =datos .get ("response")
            self ._catalogo =[
            fila for fila in (respuesta or [])
            if isinstance (fila ,dict )and fila .get ("slug")
            ]
        return self ._catalogo 

    def _serie (self ,fila :dict )->SourceSeries :
        generos =fila .get ("genders")
        etiquetas =[
        str (genero .get ("name")or "").strip ()
        for genero in (generos or [])
        if isinstance (genero ,dict )and str (genero .get ("name")or "").strip ()
        ]
        estado =fila .get ("state")
        return SourceSeries (
        source_id =str (fila .get ("slug")or ""),
        title =str (fila .get ("name")or "").strip ()or str (fila .get ("slug")or ""),
        source_name =self .name ,
        cover_url =str (fila .get ("urlImg")or "")or None ,
        description =str (fila .get ("sinopsis")or "").strip ()or None ,
        author =self ._primer_nombre (fila .get ("autors")),
        artist =self ._primer_nombre (fila .get ("artists")),
        status =str ((estado or {}).get ("name")or "").strip ()or None 
        if isinstance (estado ,dict )else None ,
        content_tags =tuple (etiquetas ),
        web_url =f"{self .base_url }/ver/{fila .get ('slug','')}",
        )

    @staticmethod 
    def _primer_nombre (valores :object )->str |None :
        nombres =[
        str (valor .get ("name")or "").strip ()
        for valor in (valores if isinstance (valores ,list )else [])
        if isinstance (valor ,dict )and str (valor .get ("name")or "").strip ()
        ]
        return ", ".join (nombres )or None 

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
            filas .sort (key =lambda fila :str (fila .get ("actualizacionCap")or ""),reverse =True )
        elif kind =="popular":
            filas .sort (key =lambda fila :float (fila .get ("averageRating")or 0 ),reverse =True )
        return self ._pagina (filas ,page )

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        consulta =query .strip ().casefold ()
        filas =await self ._todo_el_catalogo ()
        if consulta :
            filas =[
            fila for fila in filas 
            if consulta in str (fila .get ("name")or "").casefold ()
            or consulta in str (fila .get ("alternativeName")or "").casefold ()
            ]
        return self ._pagina (list (filas ),page )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        slug =series .source_id if isinstance (series ,SourceSeries )else str (series )
        datos =await self ._json (f"/api/showProject/{slug }")
        fila =datos .get ("response")
        if not isinstance (fila ,dict )or not fila .get ("slug"):
            return series if isinstance (series ,SourceSeries )else self ._serie ({"slug":slug })
        return self ._serie (fila )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        slug =series .source_id if isinstance (series ,SourceSeries )else str (series )
        datos =await self ._json (f"/api/showProject/{slug }")
        fila =datos .get ("response")or {}
        capitulos :list [SourceChapter ]=[]
        # `lastChapters` trae la lista completa, no solo los ultimos.
        for entrada in fila .get ("lastChapters")or []:
            if not isinstance (entrada ,dict )or not entrada .get ("slug"):
                continue 
            try :
                numero =float (entrada .get ("num")or 0 )
            except (TypeError ,ValueError ):
                numero =0.0 
            capitulos .append (
            SourceChapter (
            source_id =f"{slug }/{entrada ['slug']}",
            title =str (entrada .get ("name")or f"Capítulo {numero :g}"),
            series_id =slug ,
            source_name =self .name ,
            number =numero ,
            language =self .language ,
            )
            )
        capitulos .sort (key =lambda capitulo :capitulo .number ,reverse =True )
        return capitulos 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        datos =await self ._json (f"/api/showProject/{chapter_id }")
        fila =datos .get ("response")or {}
        contenido =fila .get ("pages")
        crudo =(contenido or {}).get ("urlImg")if isinstance (contenido ,dict )else None 
        # `urlImg` es un string con un JSON dentro; hay que deserializarlo aparte.
        if isinstance (crudo ,str ):
            try :
                urls =json .loads (crudo )
            except ValueError :
                urls =[]
        else :
            urls =crudo if isinstance (crudo ,list )else []
        paginas :list [SourcePage ]=[]
        for indice ,url in enumerate (urls ):
            if not url :
                continue 
            paginas .append (
            SourcePage (
            source_id =str (url ),
            chapter_id =chapter_id ,
            index =indice ,
            filename =str (url ).rsplit ("/",1 )[-1 ]or f"{indice +1 :03d}.jpg",
            source_name =self .name ,
            )
            )
        return paginas 



SOURCE =TraduccionesmoonlightSource

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
