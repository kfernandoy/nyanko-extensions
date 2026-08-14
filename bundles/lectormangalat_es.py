from __future__ import annotations


class MadaraDetailsSource :
    pass 


class LectormangalatSource (MadaraDetailsSource ):
    async def _html (self ,path :str )->str :
        response =await self ._request ("GET",f"{self .base_url }{path }")
        response .raise_for_status ()
        return response .text 

    def _series_del_html (self ,html :str )->list [SourceSeries ]:
        vistas :dict [str ,SourceSeries ]={}
        for slug ,titulo ,portada in _TARJETA .findall (html ):
            if slug in vistas :
                continue 
            vistas [slug ]=SourceSeries (
            source_id =slug ,
            title =unescape (titulo ).strip ()or slug ,
            source_name =self .name ,
            cover_url =portada ,
            web_url =f"{self .base_url }/comics/{slug }",
            )
        return list (vistas .values ())

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        orden ="likes_count"if kind =="popular"else ""
        ruta =f"/comics?page={max (page ,1 )}"
        if orden :
            ruta +=f"&order_item={orden }"
        items =self ._series_del_html (await self ._html (ruta ))
        return {"items":items ,"has_more":bool (items )}

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        consulta =query .strip ()
        ruta =f"/comics?page={max (page ,1 )}"
        if consulta :
        # El parametro es `search`. Con `title`, `q` o `name` el sitio responde 200
        # pero ignora el filtro y devuelve el listado completo.
            ruta +=f"&search={consulta .replace (' ','+')}"
        items =self ._series_del_html (await self ._html (ruta ))
        return {"items":items ,"has_more":bool (items )}

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        slug =series .source_id if isinstance (series ,SourceSeries )else str (series )
        html =await self ._html (f"/comics/{slug }")
        titulo =_H1 .search (html )
        descripcion =_DESC .search (html )
        portada =_PORTADA_OG .search (html )
        return SourceSeries (
        source_id =slug ,
        title =unescape (_ETIQUETA .sub ("",titulo .group (1 ))).strip ()if titulo else slug ,
        source_name =self .name ,
        cover_url =portada .group (1 )if portada else None ,
        description =unescape (descripcion .group (1 )).strip ()if descripcion else None ,
        web_url =f"{self .base_url }/comics/{slug }",
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        slug =series .source_id if isinstance (series ,SourceSeries )else str (series )
        html =await self ._html (f"/comics/{slug }")
        capitulos :list [SourceChapter ]=[]
        for indice ,nombre in enumerate (dict .fromkeys (_CAPITULO .findall (html ))):
            numero =_NUMERO .search (nombre )
            capitulos .append (
            SourceChapter (
            source_id =f"{slug }/{nombre }",
            title =nombre .replace ("-"," ").capitalize (),
            series_id =slug ,
            source_name =self .name ,
            number =float (numero .group (1 ))if numero else float (indice +1 ),
            language =self .language ,
            )
            )
        capitulos .sort (key =lambda capitulo :capitulo .number ,reverse =True )
        return capitulos 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        html =await self ._html (f"/comics/{chapter_id }")
        urls =list (dict .fromkeys (_PAGINA .findall (html )))
        return [
        SourcePage (
        source_id =url ,
        chapter_id =chapter_id ,
        index =indice ,
        filename =url .rsplit ("/",1 )[-1 ]or f"{indice +1 :03d}.webp",
        source_name =self .name ,
        )
        for indice ,url in enumerate (urls )
        ]

class GeneratedLectorMangaSource (LectorMangaSource ):
    name ='lectormangalat_es'
    display_name ='Lectormanga'
    base_url ='https://lectormangass.net'
    language ='es'
    requests_per_minute =60 


SOURCE =LectormangalatSource

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
