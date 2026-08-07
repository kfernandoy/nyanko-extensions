---
created: 2026-08-03T12:00:00-04:00
title: Validar y portar individualmente cada extension Kotlin sin asumir paridad por motor
area: extensions
severity: major
files:
  - ../nyanko-extensions/index.json
  - ../nyanko-extensions/tools/generate.py
  - ../nyanko-extensions/engines/
  - ../nyanko-extensions/bundles/
  - ../extensions-source-main/src/
  - apps/backend/nyanko_api/sources/contract.py
  - apps/backend/nyanko_api/sources/engine.py
  - apps/backend/nyanko_api/extension_loader.py
  - apps/backend/nyanko_api/extension_repo.py
  - apps/backend/nyanko_api/main.py
  - apps/desktop/src/SourceBrowseView.tsx
---

## Objetivo

Validar y, cuando haga falta, modificar **cada variante de fuente del indice**, comparando su
implementacion Python con la fuente Kotlin de la que fue portada. Compartir motor, tema o clase base
solo permite reutilizar codigo: **nunca es evidencia suficiente para aprobar una fuente**.

El trabajo termina cuando cada `id` de `../nyanko-extensions/index.json` tiene un resultado individual
con evidencia estatica y en vivo, y todas las diferencias funcionales estan implementadas, declaradas
como bloqueo real o retiradas mediante una decision explicita.

Este TODO es independiente de las fases 7, 8 y 9. No modificarlas ni absorber su alcance.

## Por que existe

Foto del catalogo al 2026-08-03:

- 1.901 variantes publicadas en el indice Python.
- 1.891 variantes se pudieron relacionar automaticamente con una fuente Kotlin.
- 10 necesitan mapeo manual; no se pueden omitir.
- 994 variantes (52,3 %) estan publicadas como `engine: custom`.
- Las variantes `custom` se generan mediante `_generic_bundle(...)`, que inserta el mismo scraper
  heuristico para fuentes Kotlin que pueden tener selectores, API, autenticacion, WebView,
  preferencias o transformaciones de imagen completamente diferentes.
- En Kotlin hay 699 variantes `SAFE`, 586 `MIXED` y 606 `NSFW`. El indice y los bundles Python no
  conservan actualmente esta clasificacion.
- Aproximadamente 883 variantes Kotlin declaran filtros, 539 preferencias, 410 indicios de
  autenticacion/token, 178 WebView y 182 logica relacionada especificamente con contenido adulto.

La conclusion operativa es que el motor compartido sirve para localizar codigo reutilizable, pero la
unidad de aceptacion tiene que ser siempre la variante concreta publicada en el indice.

## Unidad de trabajo

Una unidad es **un `extension.id` de `index.json`**, no un modulo Kotlin, dominio ni motor.

- Si un modulo Kotlin declara varios idiomas o bloques `source {}`, cada variante se valida por
  separado.
- Si dos variantes comparten bundle o clase base, cada una conserva su propia prueba de idioma,
  filtros, catalogo, capitulos e imagenes.
- MangaDex tiene 61 variantes: se validan las 61. Una futura `mangadex_global` sera una variante
  adicional, no un reemplazo de esas validaciones.
- Una fuente sin capitulos tambien se valida: debe devolver una lista vacia real y la app debe mostrar
  "No hay capitulos disponibles" sin romper la ficha.

## Fuentes de verdad, en orden

1. Bloque `source {}` y `ContentWarning` del modulo en `../extensions-source-main/src/`.
2. Clase Kotlin concreta y todas sus clases base, helpers, DTO, interceptores y preferencias.
3. Bundle publicado en `../nyanko-extensions/bundles/<id>.py`.
4. Entrada publicada en `../nyanko-extensions/index.json`.
5. Comportamiento actual del sitio en una comprobacion en vivo.

Si Kotlin y el sitio actual difieren, registrar ambos hechos. No copiar a ciegas un selector Kotlin
obsoleto, pero tampoco reemplazarlo silenciosamente con una heuristica.

## Prerrequisitos de contrato

Antes de intentar portar todo el catalogo hay que cerrar las capacidades que el contrato v3 no puede
representar. Mientras falte una, la fuente afectada queda `BLOCKED_CONTRACT`, no `VERIFIED`.

1. **Clasificacion de contenido**
   - Añadir `content_warning: safe | mixed | nsfw | unknown` al indice, backend y UI.
   - Una entrada antigua sin campo es `unknown`, nunca se presume `safe`.
   - La preferencia global para mostrar contenido adulto no reemplaza los filtros internos de la
     fuente.
2. **Filtros de catalogo y busqueda**
   - Poder describir filtros tipados y enviar sus valores a `browse` y `search`.
   - Conservar valores por defecto y combinaciones validas del Kotlin.
   - Incluir pagina/continuacion tambien en busqueda cuando la fuente la soporte.
3. **Preferencias por fuente**
   - Idiomas, mirror, URL personalizada, calidad de portada, data saver, grupos bloqueados y demas
     opciones que cambian las peticiones no pueden quedar fijadas en el bundle.
4. **Autenticacion y WebView**
   - Declarar si necesita login, cookies, token o paso por WebView.
   - WebView abre el `web_url` original de la serie, no una URL sintetica ni la portada.
5. **Imagenes protegidas**
   - `image_headers` cubre cabeceras como Referer, pero no descrambling, descifrado, firma o
     reconstruccion de mosaicos. Añadir un hook acotado cuando la fuente Kotlin lo haga.
6. **Resultado paginado explicito**
   - El adaptador debe poder informar si existe continuacion. Inferir `has_more` solo porque una
     pagina no esta vacia puede repetir paginas, cortar antes o crear scroll infinito.

## Caracteristicas obligatorias de la app

La validacion de una fuente debe cubrir lo siguiente. `N/A` solo se acepta con una razon y evidencia.

### A. Instalacion e identidad

- Entrada unica en el indice, SHA-256 valido, bundle e icono accesibles.
- El bundle carga mediante `SOURCE` o `build_source` y respeta Source API v3.
- `name`, `display_name`, idioma, URL base, version y rate limit corresponden a la variante Kotlin.
- El icono aparece en Fuentes y la tarjeta completa abre la fuente; no se requiere boton "Abrir".
- Se declara `content_warning` correcto.

### B. Catalogo, busqueda, filtros y paginacion

- `popular` y `latest` reproducen los endpoints, parametros, orden y selectores del Kotlin.
- Se comprueba pagina 1, pagina 2 y terminacion real para cada flujo paginado.
- La UI extiende el catalogo hacia abajo mediante scroll continuo, sin controles de pagina visibles,
  duplicados ni bucles.
- La busqueda encuentra al menos una obra conocida y respeta su paginacion.
- **Cada filtro Kotlin** queda inventariado por nombre, tipo, valor por defecto y opciones.
- Se prueba cada dimension de filtro; los filtros adultos se prueban al menos en modo excluido e
  incluido cuando el proveedor ofrece ambos.
- Si un filtro se envia aunque no se muestre en la UI, queda registrado como defecto, no como
  compatibilidad.

### C. Serie y portada

- Cada serie conserva el identificador estable del proveedor y su titulo.
- El catalogo muestra `cover_url` y se observa una peticion real de imagen en Network.
- La ficha reutiliza la misma portada a la izquierda del listado de capitulos.
- La portada se obtiene mediante `/assets/source-images/...`, con cookies/cabeceras/transformacion
  equivalentes a Kotlin cuando sean necesarias.
- La ficha conserva, si el proveedor los entrega: resumen, tags, autor, artista, estado y metadata.
- `web_url` es la URL original de la serie y abre correctamente en WebView.
- Los botones Añadir a la biblioteca, Proximo episodio, Seguimiento y WebView siguen disponibles.

### D. Capitulos

- Se recuperan todos los capitulos que devuelve la fuente, incluyendo continuaciones internas de la
  API o HTML; no solo la primera pagina.
- ID, titulo, numero decimal, idioma, scanlator y fecha se conservan cuando existen.
- El orden es estable y no elimina variantes con igual numero pero diferente idioma/scanlator.
- Los saltos correlativos muestran "Falta 1 capitulo" o "Faltan N capitulos" entre grupos.
- Los capitulos vistos aparecen en gris tanto en la fuente como en Descargas.
- Una lista realmente vacia muestra "No hay capitulos disponibles".
- Capitulo licenciado, externo o sin paginas no se convierte silenciosamente en un capitulo roto:
  registrar el comportamiento original y la decision tomada.

### E. Lector, imagenes y descargas

- Abrir un capitulo devuelve una lista de paginas completa, ordenada y sin duplicados.
- Se descarga y decodifica al menos la primera, una intermedia y la ultima imagen.
- Se validan Referer, User-Agent, cookies, URL firmada, descrambling o descifrado usados por Kotlin.
- Una pagina que falle por filtro, token o cabecera cuenta como fallo de la fuente aunque las demas
  carguen.
- Descargar un capitulo conserva nombre de serie y numero de capitulo.
- Descargas muestra spinner mientras descarga e icono de completado al terminar.
- Abrir la descarga usa los bytes locales y, al cerrar, vuelve al listado de capitulos de la serie en
  su fuente.

### F. Casos condicionales

- Login, token, cookies, WebView challenge, Cloudflare/captcha.
- URL personalizada, servidor local o mirrors.
- API JSON/GraphQL, POST de busqueda o payload cifrado.
- Imagenes fragmentadas, cifradas, base64, canvas o lazy-load.
- Doujin/gallery sin capitulos convencionales.
- Multiidioma, traducciones alternativas y titulos localizados.
- Fuentes que requieren una preferencia antes de poder listar contenido.

## Flujo obligatorio por fuente

### 1. Reservar e identificar

- Elegir un `id` pendiente directamente desde `index.json`.
- Localizar modulo, bloque `source {}`, clase concreta, padres y helpers Kotlin.
- Registrar hashes/versiones del indice, bundle y repositorio Kotlin usados en la comprobacion.
- Si el mapeo no es inequivoco, marcar `BLOCKED_MAPPING`; no elegir por parecido de nombre.

### 2. Comparacion estatica completa

Construir una matriz Kotlin → Python para:

- endpoints y metodos HTTP;
- parametros, cabeceras, cookies e interceptores;
- selectores/DTO y transformaciones;
- popular, latest, search, detail, chapters y pages;
- filtros y preferencias;
- paginacion y criterio de terminacion;
- contenido adulto;
- autenticacion, WebView, mirrors y URL configurable;
- portadas e imagenes del lector.

Revisar todas las funciones sobrescritas. No basta comparar el archivo `build.gradle.kts` ni contar
que la clase hereda de Madara/MangaThemesia.

### 3. Implementar la paridad minima real

- Reutilizar un motor solo para comportamiento comprobado como identico.
- Conservar overrides particulares en un perfil o clase especifica de la fuente.
- Si el scraper generico no reproduce Kotlin, escribir un adaptador dedicado.
- No añadir abstracciones para una sola fuente; primero usar configuracion o un override pequeño.
- No degradar una capacidad Kotlin para mantener el bundle dentro de `_supported_generic`.

### 4. Pruebas deterministas

Añadir la prueba mas pequeña que proteja cada diferencia portada:

- fixture HTML/JSON sanitizada para parser, filtros y paginacion;
- prueba de parametros/cabeceras construidos;
- prueba de terminacion y deduplicacion;
- prueba de detalle, portada, capitulos y paginas;
- prueba de transformacion de imagen cuando corresponda.

Las fixtures no sustituyen la comprobacion en vivo: impiden que la paridad se pierda despues.

### 5. Comprobacion en vivo

Registrar fecha, URL sanitizada, operacion, resultado y evidencia para cada criterio aplicable. La
comprobacion debe usar la app/sidecar, no solo una peticion manual al sitio.

No guardar cookies, tokens, credenciales ni URLs firmadas completas. Se pueden registrar nombres de
parametros, codigos HTTP, host, tamaños, cantidades y capturas/rutas de logs sanitizados.

### 6. Revision independiente

Una fuente solo pasa a `VERIFIED` cuando otra revision confirma:

- que se leyo el Kotlin completo;
- que no quedan diferencias sin clasificar;
- que las pruebas deterministas cubren las particularidades;
- que la evidencia en vivo cubre catalogo o busqueda, detalle, portada, capitulos y lector;
- que los filtros y preferencias se probaron individualmente.

El autor del port puede dejarla `READY_REVIEW`, pero no autoaprobarla.

## Registro individual

Para evitar una tabla Markdown de 1.901 filas y conflictos entre agentes, cada resultado vive en:

`.planning/extension-validation/results/<extension_id>.json`

El nombre debe coincidir exactamente con el `id` del indice. Esquema minimo:

```json
{
  "source_id": "mangadex_es",
  "status": "PENDING",
  "index_sha256": "",
  "bundle_sha256": "",
  "kotlin_commit": "",
  "kotlin_module": "src/all/mangadex",
  "kotlin_class": "MangaDex",
  "engine": "mangadex",
  "content_warning": "mixed",
  "reviewer": "",
  "reviewed_at": "",
  "features": {
    "popular": "PENDING",
    "latest": "PENDING",
    "search": "PENDING",
    "browse_pagination": "PENDING",
    "search_pagination": "PENDING",
    "filters": "PENDING",
    "adult_filter": "PENDING",
    "preferences": "PENDING",
    "details": "PENDING",
    "cover": "PENDING",
    "web_url": "PENDING",
    "chapters": "PENDING",
    "chapter_pagination": "PENDING",
    "pages": "PENDING",
    "page_bytes": "PENDING",
    "downloads": "PENDING",
    "auth_webview": "PENDING"
  },
  "kotlin_filters": [],
  "kotlin_preferences": [],
  "differences": [],
  "tests": [],
  "live_checks": [],
  "blockers": [],
  "notes": ""
}
```

Valores permitidos para cada caracteristica: `PASS`, `FAIL`, `BLOCKED`, `N/A` o `PENDING`. Todo
`N/A` necesita una explicacion en `notes` o `differences`.

Estados generales:

- `PENDING`: todavia no investigada.
- `ANALYZING`: Kotlin y bundle bajo comparacion.
- `IMPLEMENTATION_REQUIRED`: se encontro una diferencia que se puede corregir.
- `BLOCKED_MAPPING`: no se encontro una correspondencia Kotlin inequivoca.
- `BLOCKED_CONTRACT`: Nyanko no puede representar una capacidad necesaria.
- `BLOCKED_AUTH`: requiere credenciales o intervencion que no estan disponibles.
- `BLOCKED_UPSTREAM`: dominio caido, challenge o servicio retirado; necesita evidencia fechada.
- `READY_REVIEW`: implementacion y evidencia completas, pendiente de segunda revision.
- `VERIFIED`: segunda revision aprobada sin `FAIL`, `BLOCKED` ni `PENDING`.
- `RETIRED`: retirada del indice mediante decision explicita; no equivale a verificada.

## Control de cobertura

El inventario siempre se deriva del indice actual; no copiarlo manualmente dentro de este TODO.
Este comando muestra fuentes sin resultado, resultados huerfanos o duplicados conceptuales:

```powershell
$index = Get-Content ..\nyanko-extensions\index.json -Raw | ConvertFrom-Json
$expected = @($index.extensions | ForEach-Object { $_.id } | Sort-Object -Unique)
$actual = @(
  Get-ChildItem .planning\extension-validation\results -Filter *.json -ErrorAction SilentlyContinue |
    ForEach-Object { $_.BaseName } |
    Sort-Object -Unique
)
Compare-Object -ReferenceObject $expected -DifferenceObject $actual
```

El catalogo completo solo se considera cerrado cuando:

- cantidad de IDs unicos del indice = cantidad de resultados;
- `Compare-Object` no devuelve filas;
- todos los resultados son `VERIFIED` o `RETIRED`;
- cada `RETIRED` tiene la decision de retiro enlazada;
- no hay `FAIL`, `BLOCKED`, `PENDING` ni `READY_REVIEW`;
- una regeneracion de bundles no invalida sus `bundle_sha256` sin revalidacion.

## Reglas para filtros adultos

- Copiar `ContentWarning` de Kotlin como clasificacion inicial y verificarla contra el sitio actual.
- Inventariar por separado filtros como `contentRating`, `includeAdult`, categorias Adult/18+,
  exclusiones NSFW y preferencias equivalentes.
- Verificar el valor por defecto exacto. No ampliar ni ocultar contenido por accidente.
- Probar una consulta con adulto desactivado y otra activada cuando sea posible.
- Una fuente enteramente NSFW debe seguir marcada NSFW aunque no tenga un filtro interno.
- Una fuente MIXED no puede quedar fijada permanentemente a `safe/suggestive` si Kotlin deja elegir
  otras clasificaciones; eso se registra como perdida funcional.

## MangaDex global

Puede añadirse despues de validar las variantes individuales, bajo estas condiciones:

- seleccion explicita de idiomas por el usuario;
- una sola consulta con varios `availableTranslatedLanguage[]`, no 61 consultas en abanico;
- deduplicacion por UUID de MangaDex, nunca por titulo;
- idioma conservado en cada capitulo;
- filtros de clasificacion y preferencias equivalentes al Kotlin;
- rate limit compartido;
- la fuente global tiene su propio resultado y pruebas;
- las variantes individuales no se marcan verificadas por el resultado de la global.

No inferir agregacion por `engine`. Madara, MangaThemesia y otros motores agrupan sitios distintos y
no comparten IDs ni catalogo. Toda familia agregable debe declararse explicitamente.

## Criterios de rechazo inmediato

No aprobar una fuente cuando ocurra cualquiera de estos casos:

- "usa el mismo motor que otra que funciona" como unica evidencia;
- solo carga la primera pagina de catalogo, busqueda o capitulos;
- existe portada en Kotlin/sitio pero `cover_url` esta vacio o Network no intenta obtenerla;
- algun filtro Kotlin no esta inventariado;
- contenido adulto queda incluido/excluido mediante un valor fijo no declarado;
- la lista de capitulos funciona pero una o mas paginas del lector no cargan;
- se reemplazo login, WebView, cookies o transformacion de imagen por el scraper generico;
- la comprobacion solo usa fixtures o solo usa el sitio fuera de Nyanko;
- se marca `N/A` sin evidencia;
- se valida el modulo entero a partir de una sola variante de idioma.

## Estrategia de ejecucion

1. Implementar primero los prerrequisitos de contrato que bloquean muchas fuentes.
2. Crear los resultados iniciales desde todos los IDs del indice con estado `PENDING`.
3. Trabajar una fuente por vez, priorizando `custom`, autenticacion, filtros y transformaciones de
   imagen antes de los wrappers pequeños de motores compartidos.
4. Mantener cambios y pruebas atribuibles a una fuente. Un arreglo compartido puede beneficiar a
   varias, pero cada beneficiaria debe repetir su validacion individual.
5. Regenerar el bundle, actualizar version/SHA cuando corresponda y volver a ejecutar su ficha.
6. Revisar por lotes pequeños; nunca aprobar automaticamente todas las variantes de un motor.

## Entregable por fuente

Una fuente terminada deja:

- adaptador/configuracion Python con paridad justificada;
- pruebas deterministas de sus diferencias relevantes;
- bundle e indice actualizados;
- resultado JSON completo y sin secretos;
- evidencia en vivo dentro de Nyanko;
- segunda revision registrada;
- cero caracteristicas `FAIL`, `BLOCKED` o `PENDING`.

El objetivo de este proceso no es demostrar que una URL responde: es demostrar, fuente por fuente,
que Nyanko conserva el comportamiento util de la extension Kotlin y las funciones necesarias de la
app.
