---
created: 2026-08-06T03:20:00.000Z
title: El motor Madara no ve las portadas servidas como background-image (Temple Scan y hasta 306 bundles)
area: extensions
severity: major
files:
  - ../nyanko-extensions/engines/manual/templescanesp_es.py:102-118
  - ../nyanko-extensions/engines/manual/templescanesp_es.py:474-524
  - ../nyanko-extensions/bundles/templescanesp_es.py
  - ../nyanko-extensions/index.json
  - ../nyanko-extensions/tools/generate.py
---

## Problem

Reportado por kfern el 2026-08-05: «templescan en mihon da portadas, da capitulos y aqui nada».
Diagnosticado en esa misma sesion, **sin implementar** por falta de presupuesto. Va para otro agente.

**El arreglo NO vive en este repo.** Vive en `E:/2023-09-04/anitracker/nyanko-extensions`, que es
otro repositorio git (arbol limpio, HEAD `4a86a26`).

### Todo lo de abajo esta MEDIDO contra el sitio vivo. No hay que re-derivarlo.

**Lo que SI funciona hoy** (o sea: «nada» no es literal, y hasta el 2026-08-05 no se veia porque
`/api/manga/browse` respondia 500 — eso ya lo cerro la quick `260805-um9`):

| | resultado medido |
|---|---|
| series | 10 de las 10 que trae la respuesta ✅ |
| capitulos | 107 unicos, `number` bien parseado ✅ (109 devueltos, ver el otro TODO) |
| portadas | **0 de 10** ❌ |

**El dominio NO esta mal.** `https://aedexnox.akan01.com` **es** Temple Scan: sirve su logo desde
`/wp-content/uploads/2026/03/Logo_Temple.webp`. Es Temple Scan rebautizado «Aedex Nox». Que nadie
pierda el tiempo buscando la URL «buena».

### Causa raiz

El sitio es un **Madara re-skineado con Tailwind**. Entrega la portada como CSS en el propio ancla,
no como `<img>`:

```html
<a href="https://aedexnox.akan01.com/serie/deja-de-fumar/" title="¡Deja De Fumar!"
   style="background-image:url(https://aedexnox.akan01.com/wp-content/uploads/2025/03/PT-Fumar.webp)"
   class="flex flex-col ... bg-cover bg-center relative">
```

En los 46 080 bytes de la respuesta de `admin-ajax.php` hay **0 `<img>`** y **10
`background-image`**. `_image_url()` (`engines/manual/templescanesp_es.py:102-118`) solo consulta
atributos de `<img>` — `data-lm-orig-src`, `data-src`, `data-lazy-src`, `data-cfsrc`,
`data-manga-src`, `src`, y `srcset` — y **nunca mira `style`**. De ahi `cover_url=None`.

Ademas el sitio no trae las clases `page-item-detail` ni `manga__item`, asi que
`_series_from_root` (`:474-524`) cae a su **fallback de anclas** (`:501-523`): recupera
`source_id` y `title`, pero pierde portada, sinopsis, autor y estado. Encaja exactamente con el
JSON que devuelve la API hoy, con todo a `null`.

**Recuperable al 100%:** un regex sobre el `style` del ancla empareja las **10 de 10** portadas con
su serie. Comprobado:

```
deja-de-fumar/                    -> PT-Fumar.webp
plan-de-intercambio-de-madres/    -> PT-Intercambio.jpg
configurando-a-mi-equipo-de-vole  -> PT-Voleibol.png
```

### Por que Mihon si

`index.json` marca `templescanesp_es` con `"engine": "madara"` — el port heuristico le asigno el
tema generico. Su hermana `templescan_en` esta marcada `"engine": "custom"`. Keiyoushi mantiene
TempleScan como extension propia que conoce este markup.

## Solution

**Alcance sugerido: arreglar `_image_url`, no escribir una extension custom.** El fallback de
`background-image` solo entra cuando NO hay `<img>`, asi que es aditivo y no puede romper a quien
hoy funciona. Recuento del indice por motor: **306 bundles usan `madara`** — cualquier otro Madara
con skin moderno se arregla con el mismo cambio.

Puntos a resolver:

1. `_image_url` debe aceptar tambien el `style` del propio nodo (`background-image:url(...)`, con y
   sin comillas dentro del `url()`), y `_series_from_root` debe consultarlo cuando `_first(item,
   img)` no encuentra nada — **incluida la rama de fallback de anclas** (`:501-523`), que es la que
   corre en este sitio. Hoy `_image_url` solo se llama con un nodo `<img>`.
2. Decidir si el `style` se lee del ancla, de un ancestro o de cualquier descendiente. Aqui vive en
   el propio `<a href=".../serie/...">`, que es el nodo que el fallback ya tiene en la mano.
3. **`engines/manual/` y `bundles/` estan duplicados**: el bundle instalado es una copia
   minificada del engine. Hay que tocar el engine y **regenerar** con `tools/generate.py`, no
   editar el bundle a mano.
4. Subir `version` y recalcular `sha256` en `index.json` (hoy `0.12.0` /
   `7aeeef7cab767c01a804da7036cdea48e3e38dbfd945a0611141d96e3c29be61`), o la app no ofrecera la
   actualizacion. Hay `tools/validate.py` y `tools/smoke.py` para comprobar antes de publicar.
5. Verificar contra el sitio vivo que las 10 portadas salen, y **con un Madara clasico** (uno de
   los otros 305) que no se rompio nada.

**AVISO, y es lo que decide si esto vale la pena solo:** aunque el motor se arregle, **no se veran
portadas en la app**. `SourceBrowseView.tsx:659-670` pinta titulo y chevron; `cover_url` no lo
renderiza ninguna vista (`git log --all -S cover_url -- apps/desktop/src` devuelve un unico commit,
`a7afe8b`, y solo en `types.ts`/`readerLibrary.ts`). La rejilla portrait es el TODO
[2026-08-03-ui-de-fuentes-y-navegacion-de-manga-online](./2026-08-03-ui-de-fuentes-y-navegacion-de-manga-online.md),
punto 3, que a su vez va DESPUES de
[2026-08-03-reestructurar-la-seccion-lector](./2026-08-03-reestructurar-la-seccion-lector.md).
**Hacen falta las dos mitades para que kfern vea una portada.** La mitad de backend ya esta: la
quick `260805-um9` dejo `cover_url` llegando firmado y `/assets/source-images/` sirviendolo.

## Como reproducir

Desde `apps/backend`, con `uv run python`. El motor se instancia asi (el registry inyecta el
fetcher, y hay que desenvolver el adaptador v4 para llegar al `MadaraSource` de dentro):

```python
from nyanko_api.config import Settings
from nyanko_api.database import Database
from nyanko_api.extension_loader import installed_source_factories
from nyanko_api.sources import build_source_registry

settings = Settings()
db = Database(settings.database_path, anime_namespaces=())
reg = build_source_registry(sources=installed_source_factories(db, settings.data_dir),
                            library_folders=[])
source = reg.get("templescanesp_es")
inner = source
for attr in ("_legacy", "_inner", "_source", "_wrapped"):
    inner = getattr(inner, attr, inner)      # MadaraSource real
# inner._request("POST", f"{inner.base_url}/wp-admin/admin-ajax.php", data={...})
```

`load_more='always'`, asi que `browse` va SIEMPRE por `admin-ajax.php` con
`action=madara_load_more`, `template=madara-core/content/content-archive`,
`vars[meta_key]=_wp_manga_views` para populares. El listado HTML normal (`/serie/?m_orderby=views`)
**no sirve**: no trae ni una serie, solo el logo y un age gate.
