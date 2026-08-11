"""Regresiones de Mantraz Scan.

El sitio se rehizo con Next.js y la API vieja (`/api/series`) desaparecio. Estas
pruebas fijan el contrato nuevo y, sobre todo, las dos trampas que costaron caras:

1. Las rutas se piden con barra final / en forma canonica. Sin ella el sitio
   responde 308 y el fetcher de la app pierde la cookie de clearance en el salto,
   porque la manda por peticion y no en el jar -> Cloudflare devuelve 403.
2. `badge-pill` la comparten el estado y la demografia, asi que coger la primera
   da "SHOUJO" en vez de "En emision".
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _cargar():
    ruta = RAIZ / "engines" / "manual" / "mantrazscan_es.py"
    spec = importlib.util.spec_from_file_location("mantrazscan_manual", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


MODULO = _cargar()


class _Respuesta:
    def __init__(self, texto: str = "", payload: dict | None = None) -> None:
        self.text = texto
        self._payload = payload or {}
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FetcherFalso:
    """Registra las URLs pedidas y devuelve respuestas preparadas."""

    def __init__(self, respuestas: dict[str, _Respuesta]) -> None:
        self.respuestas = respuestas
        self.urls: list[str] = []

    async def request(self, method: str, url: str, **kwargs):
        params = kwargs.get("params") or []
        consulta = "&".join(f"{clave}={valor}" for clave, valor in params)
        completa = f"{url}?{consulta}" if consulta else url
        self.urls.append(completa)
        for patron, respuesta in self.respuestas.items():
            if patron in completa:
                return respuesta
        return _Respuesta("")


def _fuente(respuestas: dict[str, _Respuesta]) -> tuple:
    fuente = MODULO.SOURCE()
    fetcher = _FetcherFalso(respuestas)
    fuente.fetcher = fetcher
    return fuente, fetcher


LISTADO = """
<div class="s-card">
  <a class="s-card-imglink" href="/manga/serie-uno/">
    <div class="s-card-img"><img src="https://img.mantrazscan.co/img/uno.jpg"/></div>
  </a>
  <div class="s-card-body">
    <a class="s-card-title" href="/manga/serie-uno/">Serie Uno</a>
    <div class="s-card-chs"><a class="ch-chip" href="/manga/serie-uno/capitulo-3/">Cap 3</a></div>
  </div>
</div>
<div class="s-card">
  <a class="s-card-imglink" href="/manga/serie-dos/">
    <div class="s-card-img"><img src="https://img.mantrazscan.co/img/dos.jpg"/></div>
  </a>
  <div class="s-card-body">
    <a class="s-card-title" href="/manga/serie-dos/">Serie Dos</a>
  </div>
</div>
"""

FICHA = """
<div class="series-info">
  <div class="series-badges">
    <span class="badge-pill">\U0001f338 SHOUJO</span>
    <span class="badge-pill badge-ongoing">En emisi\u00f3n</span>
  </div>
  <h1 class="series-title">Serie Uno</h1>
  <div class="series-genres">
    <a class="genre-tag" href="/genero/accion/">Acci\u00f3n</a>
    <a class="genre-tag" href="/genero/drama/">Drama</a>
  </div>
  <div class="series-desc">Una sinopsis cualquiera.</div>
  <a href="/manga/serie-uno/capitulo-2/">Cap 2</a>
  <a href="/manga/serie-uno/capitulo-1/">Cap 1</a>
  <a href="/manga/serie-uno/resena/">Rese\u00f1a</a>
  <a href="/manga/serie-uno/wiki/">Wiki</a>
</div>
"""

LECTOR = (
    'algo:[\\"$\\",\\"div\\",null,{\\"num\\":1,\\"images\\":'
    '[\\"https://img.mantrazscan.co/img/data/capitulo-1/1.jpg\\",'
    '\\"https://img.mantrazscan.co/img/data/capitulo-1/2.jpg\\"]}]'
)


@pytest.mark.asyncio
async def test_browse_usa_la_ruta_canonica_sin_redirect():
    """`?page=N` provoca un 308 que tira la cookie de clearance: se pide /page/N/."""
    fuente, fetcher = _fuente({"/explorar/": _Respuesta(LISTADO)})

    await fuente.browse("popular", 1)
    await fuente.browse("popular", 3)

    assert fetcher.urls[0] == "https://mantrazscan.co/explorar/"
    assert fetcher.urls[1] == "https://mantrazscan.co/explorar/page/3/"
    assert not any("?page=" in url for url in fetcher.urls)


@pytest.mark.asyncio
async def test_browse_devuelve_series_con_portada():
    fuente, _ = _fuente({"/explorar/": _Respuesta(LISTADO)})

    resultado = await fuente.browse("latest", 1)

    assert [serie.source_id for serie in resultado["items"]] == ["serie-uno", "serie-dos"]
    assert resultado["items"][0].title == "Serie Uno"
    assert resultado["items"][0].cover_url == "https://img.mantrazscan.co/img/uno.jpg"
    assert resultado["has_more"] is True


@pytest.mark.asyncio
async def test_browse_sin_resultados_corta_la_paginacion():
    """El sitio no declara total de paginas: una vacia es el final."""
    fuente, _ = _fuente({"/explorar/": _Respuesta("<div></div>")})

    resultado = await fuente.browse("popular", 40)

    assert resultado["items"] == []
    assert resultado["has_more"] is False


@pytest.mark.asyncio
async def test_search_pide_api_con_barra_final():
    fuente, fetcher = _fuente({
        "/api/search/": _Respuesta(payload={
            "results": [
                {"postId": 1, "title": "Serie Uno", "slug": "serie-uno", "cover": "u.jpg"},
            ]
        }),
    })

    resultado = await fuente.search("uno")

    assert fetcher.urls == ["https://mantrazscan.co/api/search/?q=uno"]
    assert resultado["items"][0].source_id == "serie-uno"
    # El endpoint es de autocompletado: no pagina.
    assert resultado["has_more"] is False


GENERO = """
<div class="series-grid">
  <a class="s-card" href="/manga/serie-uno/">
    <div class="s-card-img"><img src="https://img.mantrazscan.co/img/uno.jpg" alt="Serie Uno"/></div>
    <div class="s-card-body"><div class="s-card-title">Serie Uno</div></div>
  </a>
  <a class="s-card" href="/manga/serie-dos/">
    <div class="s-card-img"><img src="https://img.mantrazscan.co/img/dos.jpg" alt="Serie Dos"/></div>
    <div class="s-card-body"><div class="s-card-title">Serie Dos</div></div>
  </a>
</div>
"""


@pytest.mark.asyncio
async def test_las_tarjetas_de_genero_tienen_otro_markup():
    """En `/genero/` la tarjeta ES el <a> y el titulo un <div>, no un <a>."""
    fuente, _ = _fuente({"/genero/action/": _Respuesta(GENERO)})

    resultado = await fuente.search("", filters={"genre": "action"})

    assert [serie.source_id for serie in resultado["items"]] == ["serie-uno", "serie-dos"]
    assert resultado["items"][0].title == "Serie Uno"
    assert resultado["items"][0].cover_url == "https://img.mantrazscan.co/img/uno.jpg"


@pytest.mark.asyncio
async def test_filtro_de_genero_usa_su_propia_ruta():
    """El filtro por genero se sirve de `/genero/<slug>/`, no de la busqueda."""
    fuente, fetcher = _fuente({"/genero/action/": _Respuesta(GENERO)})

    resultado = await fuente.search("", filters={"genre": "action"})

    assert fetcher.urls == ["https://mantrazscan.co/genero/action/"]
    assert [serie.source_id for serie in resultado["items"]] == ["serie-uno", "serie-dos"]
    # `/genero/<slug>/page/2/` responde 404: ese listado no pagina.
    assert resultado["has_more"] is False


@pytest.mark.asyncio
async def test_el_genero_no_pagina():
    fuente, fetcher = _fuente({"/genero/action/": _Respuesta(LISTADO)})

    resultado = await fuente.search("", page=2, filters={"genre": "action"})

    assert resultado["items"] == []
    assert fetcher.urls == []


def test_los_generos_del_filtro_son_slugs_en_ingles():
    """La web se ve en español pero `/genero/` usa slugs en ingles."""
    fuente = MODULO.SOURCE()

    opciones = dict(fuente.get_filters()[0].options)

    assert opciones["action"] == "Acción"
    assert opciones["martial-arts"] == "Artes Marciales"
    assert "accion" not in opciones


@pytest.mark.asyncio
async def test_details_ignora_la_badge_de_demografia():
    """Coger la primera `badge-pill` daba "SHOUJO" y dejaba el estado en None."""
    fuente, _ = _fuente({"/manga/serie-uno/": _Respuesta(FICHA)})

    ficha = await fuente.details("serie-uno")

    assert ficha.status == "ongoing"
    assert ficha.title == "Serie Uno"
    assert list(ficha.content_tags) == ["Acción", "Drama"]
    assert ficha.description == "Una sinopsis cualquiera."


@pytest.mark.asyncio
async def test_chapters_descarta_resena_y_wiki():
    fuente, _ = _fuente({"/manga/serie-uno/": _Respuesta(FICHA)})

    capitulos = await fuente.chapters("serie-uno")

    assert [capitulo.source_id for capitulo in capitulos] == [
        "serie-uno/capitulo-2",
        "serie-uno/capitulo-1",
    ]
    assert capitulos[0].number == 2.0


@pytest.mark.asyncio
async def test_pages_lee_las_imagenes_del_payload_de_next():
    """Las paginas no estan en <img>: van en el payload flight de Next."""
    fuente, _ = _fuente({"/manga/serie-uno/capitulo-1/": _Respuesta(LECTOR)})

    paginas = await fuente.pages("serie-uno/capitulo-1")

    assert [pagina.source_id for pagina in paginas] == [
        "https://img.mantrazscan.co/img/data/capitulo-1/1.jpg",
        "https://img.mantrazscan.co/img/data/capitulo-1/2.jpg",
    ]
    assert [pagina.index for pagina in paginas] == [1, 2]
    assert paginas[0].filename == "1.jpg"


@pytest.mark.asyncio
async def test_ids_viejos_avisan_en_vez_de_romper():
    """Antes los ids eran `<id>#<slug>`; hay que pedir refrescar, no fallar opaco."""
    fuente, _ = _fuente({})

    with pytest.raises(MODULO.SourceNotFoundError):
        await fuente.pages("4571#serie-uno")


def test_slug_acepta_el_formato_viejo_de_series():
    """La biblioteca del usuario guarda ids `<id>#<slug>`: deben seguir resolviendo."""
    assert MODULO.SOURCE._slug("4571#serie-uno") == "serie-uno"
    assert MODULO.SOURCE._slug("serie-uno") == "serie-uno"
    assert MODULO.SOURCE._slug("/manga/serie-uno/") == "serie-uno"
