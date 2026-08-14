from __future__ import annotations


class MadaraDetailsSource :
    pass 


class MantrazscanSource (MadaraDetailsSource ):
    """Lee el HTML del sitio: la API JSON que usaba esta extension ya no existe."""

    async def _html (self ,path :str ,params :list [tuple [str ,str ]]|None =None )->str :
        response =await self ._request ("GET",f"{self .base_url }{path }",params =params or [])
        response .raise_for_status ()
        return response .text 

    @staticmethod 
    def _slug (identificador :str )->str :
        """Slug a partir del id guardado.

        Los ids antiguos eran ``<numero>#<slug>``; los nuevos son el slug pelado.
        Se acepta cualquiera de los dos para no invalidar la biblioteca existente.
        """
        texto =str (identificador or "").strip ().strip ("/")
        if "#"in texto :
            texto =texto .partition ("#")[2 ]or texto .partition ("#")[0 ]
        return texto .rsplit ("/",1 )[-1 ]

    def _tarjetas (self ,html :str )->list [SourceSeries ]:
        """Series de un listado, en el orden en que aparecen.

        El sitio usa DOS markups distintos para la misma tarjeta:

        * ``/explorar/``  -> ``div.s-card`` con ``a.s-card-imglink`` y ``a.s-card-title``.
        * ``/genero/...`` -> el propio ``a.s-card`` es el enlace y el titulo es un ``div``.

        Por eso se ancla en el contenedor ``.s-card`` y de ahi se sacan enlace, titulo
        y portada, en vez de depender de que el titulo sea un ``<a>``. Se parsea el
        arbol y no con regex porque los titulos llevan entidades y comillas.
        """
        root =_parse_html (html )
        resultado :list [SourceSeries ]=[]
        vistos :set [str ]=set ()
        for tarjeta in root .descendants ():
            if not tarjeta .has_class ("s-card"):
                continue 
                # El enlace de la serie es la tarjeta misma o algun `<a>` de dentro; los
                # accesos rapidos a capitulos (`.ch-chip`) no valen: apuntan al lector.
            enlace =tarjeta if tarjeta .tag =="a"else next (
            (
            nodo 
            for nodo in tarjeta .descendants ("a")
            if not nodo .has_class ("ch-chip")and nodo .attrs .get ("href")
            ),
            None ,
            )
            if enlace is None :
                continue 
            slug =self ._slug (enlace .attrs .get ("href",""))
            if not slug or slug in vistos :
                continue 
            vistos .add (slug )
            titulo =_first (tarjeta ,lambda nodo :nodo .has_class ("s-card-title"))
            imagen =_first (tarjeta ,lambda nodo :nodo .tag =="img")
            resultado .append (
            SourceSeries (
            source_id =slug ,
            title =(titulo .text ().strip ()if titulo else "")
            or (imagen .attrs .get ("alt","").strip ()if imagen else ""),
            source_name =self .name ,
            cover_url =_image_url (imagen ,self .base_url )if imagen else None ,
            web_url =f"{self .base_url }/manga/{slug }/",
            )
            )
        return resultado 

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
            # `/explorar/` no admite ordenar: no hay parametro de orden y probar `sort`
            # devuelve la misma secuencia. Se sirve el mismo listado en ambas pestañas
            # en vez de simular un orden que el sitio no aplica.
            #
            # La ruta canonica es `/explorar/page/N/`. Es IMPORTANTE pedirla tal cual:
            # `?page=N` responde un 308 hacia ella, y el fetcher de la app manda la
            # cookie de clearance por peticion, no en el jar del cliente, asi que httpx
            # NO la reenvia en el salto -> Cloudflare contesta 403. Sin redirect no hay
            # salto y la cookie llega.
        numero =max (page ,1 )
        ruta ="/explorar/"if numero ==1 else f"/explorar/page/{numero }/"
        items =self ._tarjetas (await self ._html (ruta ))
        # El sitio no declara el total de paginas; una pagina vacia es el final.
        return {"items":items ,"has_more":bool (items )}

    def get_filters (self )->list [SourceFilter ]:
        return [SourceFilter ("genre","Género","select",list (_MANTRAZ_GENEROS ),"")]

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        texto =query .strip ()
        genero =str ((filters or {}).get ("genre")or "").strip ()
        if genero and not texto :
        # `/genero/<slug>/` lista ~48 series y NO pagina: `/page/2/` da 404.
            if page >1 :
                return {"items":[],"has_more":False }
            return {
            "items":self ._tarjetas (await self ._html (f"/genero/{genero }/")),
            "has_more":False ,
            }
        if not texto :
            return await self .browse ("latest",page )
            # `/api/search` es el unico JSON vivo, pensado para el autocompletado: no
            # pagina y devuelve como mucho 8 resultados. Tampoco acepta genero, asi que
            # con texto el filtro no se aplica (el sitio no ofrece esa combinacion).
        if page >1 :
            return {"items":[],"has_more":False }
            # Con barra final: sin ella responde un 308 y el salto pierde la cookie
            # de clearance (misma trampa que en `browse`).
        response =await self ._request (
        "GET",f"{self .base_url }/api/search/",params =[("q",texto )],
        )
        response .raise_for_status ()
        payload =response .json ()or {}
        items =[]
        for fila in payload .get ("results")or []:
            if not isinstance (fila ,dict ):
                continue 
            slug =self ._slug (str (fila .get ("slug")or ""))
            if not slug :
                continue 
            items .append (
            SourceSeries (
            source_id =slug ,
            title =str (fila .get ("title")or "").strip (),
            source_name =self .name ,
            cover_url =str (fila .get ("cover")or "")or None ,
            web_url =f"{self .base_url }/manga/{slug }/",
            )
            )
        return {"items":items ,"has_more":False }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        slug =self ._slug (series_id )
        html =await self ._html (f"/manga/{slug }/")
        root =_parse_html (html )

        titulo =_first (root ,lambda node :node .tag =="h1"and node .has_class ("series-title"))
        descripcion =_first (root ,lambda node :node .has_class ("series-desc"))
        # `badge-pill` la comparten el estado y las etiquetas de demografia ("🌸 SHOUJO"),
        # asi que no vale con coger la primera: se busca la que diga un estado conocido.
        badge =next (
        (
        nodo 
        for nodo in root .descendants ()
        if nodo .has_class ("badge-pill")
        and nodo .text ().strip ().casefold ()in _MANTRAZ_ESTADOS 
        ),
        None ,
        )
        portada =_first (
        root ,
        lambda node :node .tag =="img"
        and "img.mantrazscan.co"in _image_url (node ,self .base_url ),
        )
        generos =tuple (
        texto 
        for node in root .descendants ("a")
        if node .has_class ("genre-tag")and (texto :=node .text ().strip ())
        )
        estado =_MANTRAZ_ESTADOS .get (badge .text ().strip ().casefold ())if badge else None 
        return SourceSeries (
        source_id =slug ,
        title =titulo .text ().strip ()if titulo else (
        series .title if isinstance (series ,SourceSeries )else slug 
        ),
        source_name =self .name ,
        cover_url =_image_url (portada ,self .base_url )if portada else (
        series .cover_url if isinstance (series ,SourceSeries )else None 
        ),
        description =descripcion .text ().strip ()if descripcion else None ,
        status =estado ,
        content_tags =generos ,
        web_url =f"{self .base_url }/manga/{slug }/",
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        slug =self ._slug (series_id )
        html =await self ._html (f"/manga/{slug }/")
        # La ficha enlaza `/manga/<slug>/capitulo-N/`. Se conserva el orden del sitio
        # (descendente) y se deduplica: cada capitulo aparece en la lista y ademas en
        # los accesos rapidos de la cabecera.
        vistos :list [str ]=[]
        for encontrado in re .finditer (
        rf'href="/manga/{re .escape (slug )}/([a-z0-9-]+)/?"',html ,
        ):
            capitulo =encontrado .group (1 )
            # La ficha enlaza tambien `/resena/` y `/wiki/`, que no son capitulos.
            if capitulo in _MANTRAZ_NO_CAPITULOS or capitulo in vistos :
                continue 
            vistos .append (capitulo )
        resultado :list [SourceChapter ]=[]
        for capitulo in vistos :
            numero =self ._float (capitulo .rsplit ("-",1 )[-1 ])
            etiqueta =capitulo .replace ("-"," ").strip ().capitalize ()
            resultado .append (
            SourceChapter (
            source_id =f"{slug }/{capitulo }",
            title =etiqueta ,
            series_id =slug ,
            source_name =self .name ,
            number =numero ,
            language =self .language ,
            )
            )
        return resultado 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        ruta =str (chapter_id ).strip ("/")
        if "#"in ruta :
        # Id del formato viejo (`<id>#<slug>`): no se puede resolver a una URL.
            raise SourceNotFoundError (
            "Actualiza la lista de capitulos para leer este capitulo.",
            )
        html =await self ._html (f"/manga/{ruta }/")
        # Las paginas NO estan en etiquetas <img>: el lector las recibe por el payload
        # flight de Next, como `\"images\":[\"https://...1.jpg\", ...]`.
        bloque =_MANTRAZ_IMAGENES .search (html )
        urls =_MANTRAZ_URL .findall (bloque .group (1 ))if bloque else []
        return [
        SourcePage (
        source_id =url ,
        chapter_id =chapter_id ,
        index =indice ,
        filename =urlparse (url ).path .rsplit ("/",1 )[-1 ]or f"{indice +1 :03d}.jpg",
        source_name =self .name ,
        )
        for indice ,url in enumerate (urls ,1 )
        ]

    @staticmethod 
    def _float (value :Any )->float |None :
        try :
            return float (value )
        except (TypeError ,ValueError ):
            return None 


class GeneratedManhwaScanSource (ManhwaScanSource ):
    name ='mantrazscan_es'
    display_name ='Manhwa Scan'
    base_url ='https://mantrazscan.co'
    language ='es'
    requests_per_minute =60 
    content_warning ='nsfw'
    extra_headers ={'Accept':'*/*'}
    image_headers ={'Referer':'https://mantrazscan.co/'}


SOURCE =MantrazscanSource

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
