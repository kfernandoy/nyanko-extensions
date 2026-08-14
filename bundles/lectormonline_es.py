
class MadaraDetailsSource :
    pass 


class LectormonlineSource (MadaraDetailsSource ):
    async def _json (self ,path :str ,params :dict |None =None )->dict :
        response =await self ._request (
        "GET",f"{self .base_url }{path }",params =params or {},
        )
        response .raise_for_status ()
        try :
            return response .json ()or {}
        except ValueError :
            return {}

    async def _html (self ,path :str )->str :
        response =await self ._request ("GET",f"{self .base_url }{path }")
        response .raise_for_status ()
        return response .text 

    @staticmethod 
    def _resolver (datos :list ,indice ,vistos :frozenset =frozenset ()):
        """Deshace el aplanado de SvelteKit siguiendo los indices."""
        if not isinstance (indice ,int )or not 0 <=indice <len (datos ):
            return indice 
        if indice in vistos :
            return None 
        valor =datos [indice ]
        marcados =vistos |{indice }
        if isinstance (valor ,dict ):
            return {
            clave :GatoLibreriaSource ._resolver (datos ,hijo ,marcados )
            for clave ,hijo in valor .items ()
            }
        if isinstance (valor ,list ):
            return [GatoLibreriaSource ._resolver (datos ,hijo ,marcados )for hijo in valor ]
        return valor 

    async def _payload (self ,path :str )->dict :
        datos =await self ._json (f"{path }/__data.json")
        nodos =datos .get ("nodes")or []
        for nodo in reversed (nodos ):
            if isinstance (nodo ,dict )and nodo .get ("type")=="data":
                plano =nodo .get ("data")
                if isinstance (plano ,list )and plano :
                    resuelto =self ._resolver (plano ,0 )
                    if isinstance (resuelto ,dict ):
                        return resuelto 
        return {}

    @staticmethod 
    def _genero (valor )->str :
        """Nombre legible de un genero.

        La API no devuelve cadenas sino objetos completos:
        ``{"id": 13, "name": "Drama", "slug": "drama", "createdAt": ...}``. Al pasarlos
        por ``str()`` la ficha mostraba el diccionario entero como etiqueta.
        Se acepta la cadena suelta por si alguna respuesta viene ya aplanada.
        """
        if isinstance (valor ,dict ):
            return str (valor .get ("name")or valor .get ("slug")or "").strip ()
        return str (valor or "").strip ()

    def _serie (self ,fila :dict )->SourceSeries :
        generos =fila .get ("genres")
        return SourceSeries (
        source_id =str (fila .get ("slug")or fila .get ("id")or ""),
        title =str (fila .get ("title")or "").strip ()or str (fila .get ("slug")or ""),
        source_name =self .name ,
        cover_url =str (fila .get ("coverImage")or fila .get ("urlCover")or "")or None ,
        description =str (fila .get ("description")or "").strip ()or None ,
        author =str (fila .get ("author")or "").strip ()or None ,
        artist =str (fila .get ("artist")or "").strip ()or None ,
        status =str (fila .get ("status")or "").strip ()or None ,
        content_tags =tuple (
        nombre for genero in generos if (nombre :=self ._genero (genero ))
        )if isinstance (generos ,list )else (),
        web_url =f"{self .base_url }/comics/{fila .get ('slug','')}",
        )

    def _listado (self ,datos :dict )->dict :
        filas =[fila for fila in (datos .get ("data")or [])if isinstance (fila ,dict )]
        paginacion =datos .get ("pagination")or {}
        pagina =int (paginacion .get ("page")or 1 )
        total =int (paginacion .get ("totalPages")or 0 )
        return {
        "items":[self ._serie (fila )for fila in filas ],
        "has_more":pagina <total ,
        }

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
            # La API no admite ordenar: probado `sort=views`, devuelve exactamente la misma
            # secuencia. Se sirve el mismo listado en ambas pestañas en vez de simular un
            # orden que el sitio no aplica.
        params :dict [str ,object ]={"page":max (page ,1 ),"limit":_POR_PAGINA }
        return self ._listado (await self ._json ("/api/comics",params ))

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        params :dict [str ,object ]={"page":max (page ,1 ),"limit":_POR_PAGINA }
        if query .strip ():
        # Es `search`: con `title` o `q` la API ignora el filtro.
            params ["search"]=query .strip ()
        return self ._listado (await self ._json ("/api/comics",params ))

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        slug =series .source_id if isinstance (series ,SourceSeries )else str (series )
        payload =await self ._payload (f"/comics/{slug }")
        comic =payload .get ("comic")
        if not isinstance (comic ,dict )or not comic .get ("title"):
            return series if isinstance (series ,SourceSeries )else self ._serie ({"slug":slug })
        comic .setdefault ("slug",slug )
        return self ._serie (comic )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        slug =series .source_id if isinstance (series ,SourceSeries )else str (series )
        # La API solo expone `recent_chapters` (2 ultimos); la lista completa esta en el
        # HTML de la ficha.
        html =await self ._html (f"/comics/{slug }")
        ids =list (dict .fromkeys (_CAPITULO_HTML .findall (html )))
        capitulos :list [SourceChapter ]=[]
        for indice ,chapter_id in enumerate (ids ):
            capitulos .append (
            SourceChapter (
            source_id =f"{slug }/{chapter_id }",
            title =f"Capítulo {len (ids )-indice }",
            series_id =slug ,
            source_name =self .name ,
            number =float (len (ids )-indice ),
            language =self .language ,
            )
            )
        return capitulos 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        slug ,_ ,numero =chapter_id .partition ("/")
        payload =await self ._payload (f"/comics/{slug }/chapters/{numero }")
        datos =payload .get ("chapter")
        urls =(datos or {}).get ("url_pages")if isinstance (datos ,dict )else None 
        paginas :list [SourcePage ]=[]
        for indice ,url in enumerate (urls or []):
            if not url :
                continue 
            paginas .append (
            SourcePage (
            source_id =str (url ),
            chapter_id =chapter_id ,
            index =indice ,
            filename =str (url ).rsplit ("/",1 )[-1 ]or f"{indice +1 :03d}.webp",
            source_name =self .name ,
            )
            )
        return paginas 

class GeneratedGatoLibreriaSource (GatoLibreriaSource ):
    name ='lectormonline_es'
    display_name ='Gato Librería'
    base_url ='https://gatolibreria.com'
    language ='es'
    requests_per_minute =60 


SOURCE =LectormonlineSource

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
