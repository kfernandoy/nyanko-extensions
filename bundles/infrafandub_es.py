from __future__ import annotations


class MadaraDetailsSource :
    pass 



class InfraFandubSource (MadaraDetailsSource ):
    async def search (self ,query :str ,limit :int =20 )->list [SourceSeries ]:
    # El buscador de WordPress esta CAIDO en este sitio: `?s=a&post_type=wp-manga`
    # devuelve 404 y `?s=combat` un 500, asi que el `search` del motor rompia la
    # extension entera nada mas abrirla. El ajax `madara_load_more` con la plantilla
    # de busqueda si responde, y devuelve el mismo markup `c-tabs-item__content` que
    # ya parsea el motor, portadas incluidas.
        respuesta =await self ._request (
        "POST",
        f"{self .base_url }/wp-admin/admin-ajax.php",
        data ={
        "action":"madara_load_more",
        "page":"0",
        "template":"madara-core/content/content-search",
        "vars[s]":query .strip (),
        "vars[paged]":"1",
        "vars[template]":"search",
        "vars[post_type]":"wp-manga",
        "vars[post_status]":"publish",
        },
        headers ={"X-Requested-With":"XMLHttpRequest"},
        )
        respuesta .raise_for_status ()
        return self ._series (respuesta .text ,("c-tabs-item__content","manga__item"))[:limit ]

    def _series_from_root (self ,root :_Node ,classes :tuple [str ,...])->list [SourceSeries ]:
        result :list [SourceSeries ]=[]
        for item in root .descendants ("div"):
            if not item .has_class ("manga-item"):
                continue 
            title =_first (item ,lambda node :node .tag =="div"and node .has_class ("title"))
            anchor =_first (title or item ,lambda node :node .tag =="a"and bool (node .attrs .get ("href")))
            if anchor is None :
                continue 
            url =urljoin (f"{self .base_url }/",anchor .attrs ["href"])
            image =_first (item ,lambda node :node .tag =="img")
            result .append (SourceSeries (
            source_id =url ,
            title =anchor .text ().strip ()or anchor .attrs .get ("title","").strip (),
            source_name =self .name ,
            cover_url =_image_url (image ,self .base_url )if image else None ,
            web_url =url ,
            ))
            # El tema propio (`manga-item`) solo sale en las paginas del sitio; el ajax de
            # busqueda responde con el markup clasico de Madara, asi que se delega en el motor
            # cuando esta rama no encuentra nada.
        return result or super ()._series_from_root (root ,classes )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        title =_first (root ,lambda node :node .tag =="h1"and node .has_class ("series-title"))
        image =_first (
        root ,
        lambda node :node .tag =="img"and node .has_class ("series-cover")
        and self ._has_class_ancestor (node ,"sidebar"),
        )
        description =_first (root ,lambda node :node .tag =="div"and node .has_class ("summary-text"))

        def detail (label :str )->str :
            for item in root .descendants ("div"):
                if item .has_class ("detail-item")and label in item .text ().casefold ():
                    value =_first (item ,lambda node :node .tag =="span"and node .has_class ("detail-value"))
                    return value .text ().strip ()if value else ""
            return ""

        genres =tuple (
        node .text ().strip ()for node in root .descendants ("a")
        if node .has_class ("genre-tag")and self ._has_class_ancestor (node ,"genres")and node .text ().strip ()
        )
        return SourceSeries (
        source_id =series_id ,
        title =title .text ().strip ()if title else series .title if isinstance (series ,SourceSeries )else series_id .rstrip ("/").rsplit ("/",1 )[-1 ],
        source_name =self .name ,
        cover_url =_image_url (image ,str (response .url ))if image else None ,
        description =description .text ().strip ()if description else None ,
        author =detail ("autor")or None ,
        artist =detail ("artista")or None ,
        status =self ._madara_status (detail ("estado")),
        content_tags =genres ,
        web_url =str (response .url ),
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        series_url =urljoin (f"{self .base_url }/",series_id ).rstrip ("/")
        response =await self ._request ("POST",f"{series_url }/ajax/chapters/")
        response .raise_for_status ()
        # El sitio volvio al markup estandar de Madara: `ajax/chapters` sirve
        # <li class="wp-manga-chapter"> y ya no emite ningun `a.chapter-item`, asi que
        # este override devolvia 0 capitulos sobre una respuesta con 692. Se conserva el
        # camino propio por si vuelve el markup antiguo, pero se delega en el motor
        # cuando no aparece.
        if not _first (
        _parse_html (response .text ),
        lambda node :node .tag =="a"and node .has_class ("chapter-item"),
        ):
            return await super ().chapters (series )
        result :list [SourceChapter ]=[]
        for anchor in _parse_html (response .text ).descendants ("a"):
            if not anchor .has_class ("chapter-item")or not anchor .attrs .get ("href"):
                continue 
            title =_first (anchor ,lambda node :node .tag =="span"and node .has_class ("chapter-number"))
            date =_first (anchor ,lambda node :node .tag =="span"and node .has_class ("chapter-date"))
            name =title .text ().strip ()if title else anchor .text ().strip ()
            number =re .search (r"\d+(?:\.\d+)?",name )
            result .append (SourceChapter (
            source_id =urljoin (str (response .url ),anchor .attrs ["href"]),
            title =name ,
            series_id =series_id ,
            source_name =self .name ,
            number =float (number .group ())if number else None ,
            language =self .language ,
            uploaded_at =self ._madara_date (date .text ()if date else ""),
            ))
        return result 
class GeneratedMadaraSource (InfraFandubSource ):
    name ='infrafandub_es'
    display_name ='InfraFandub'
    base_url ='https://infrafandub.com'
    language ='es'
    manga_substring ='manga'
    load_more ='auto'
    use_new_chapter_endpoint =True 
    chapter_url_suffix ='?style=list'
    supports_latest =True 
    requests_per_minute =120 
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
