---
created: 2026-08-06T03:40:00.000Z
title: Medir la fidelidad del port heuristico contra el Kotlin original, por motor y por campo
area: extensions
severity: major
files:
  - .planning/extension-validation/results/
  - .planning/todos/pending/2026-08-03-validacion-individual-de-extensiones.md
  - ../nyanko-extensions/tools/smoke.py
  - ../nyanko-extensions/tools/generate.py
  - ../nyanko-extensions/index.json
---

## Problem

Pedido por kfern el 2026-08-05, a raiz del diagnostico de Temple Scan
([2026-08-06-portadas-madara-por-background-image](./2026-08-06-portadas-madara-por-background-image.md)).
Capturado **sin implementar**, para otro agente.

**El modo de fallo del port heuristico es la degradacion parcial SILENCIOSA, no el error.**
Temple Scan pasaria cualquier smoke test: HTTP 200, 10 series, 107 capitulos con su numero bien
parseado. Lo unico roto era **un campo** —`cover_url`, 0 de 10— y nadie se entero hasta que un
humano comparo con Mihon. Un harness que pregunte «¿funciona?» no cazara al siguiente.

Encima el fallo no era de la fuente, era **del motor compartido**: `_image_url` no mira
`background-image`. Un bug asi vale por cientos de extensiones, y buscarlo fuente a fuente es
inviable.

### Estado MEDIDO el 2026-08-06 (no re-derivar)

Ya existe el andamiaje, del TODO
[2026-08-03-validacion-individual-de-extensiones](./2026-08-03-validacion-individual-de-extensiones.md).
**Esto NO se construye de cero — se rellena.** `.planning/extension-validation/results/` tiene
**1901** JSON, uno por extension, con el esquema correcto ya diseñado (`kotlin_module`,
`kotlin_class`, `kotlin_commit`, `engine`, `features{}` con 17 capacidades entre ellas `cover`,
`differences[]`, `live_checks[]`, `blockers[]`).

Lo que hay dentro:

| medida | valor |
|---|---|
| celdas de `features` en `PENDING` | **30 494** |
| celdas `IMPLEMENTED` | 1 823 (solo `filters`/`preferences`, deducidas estaticamente) |
| ficheros con `kotlin_module` | **1 787** de 1901 |
| ficheros con `engine` | **0** |
| ficheros con `differences` | **0** |
| `status: READY_REVIEW` / `BLOCKED_MAPPING` | 1 787 / 114 |

O sea: **el enlace al original Kotlin existe para el 94% del catalogo, y el analisis de fidelidad
no se ha corrido nunca.** 30 494 celdas no las revisa un humano: el pase automatico tiene que
reducirlas a una lista corta.

Reparto por motor en `index.json` (`Counter(e["engine"])`):

```
custom 1006 | madara 306 | mangathemesia 146 | mangadex 61
hentaihand 56 | galleryadults 49 | zeistmanga 35 | keyoapp 18
```

## Solution

### La idea central

No medir «¿funciona?» sino **relleno por campo**, y contrastarlo con **lo que el Kotlin promete**.
Sin ese contraste `cover: 0/10` es ambiguo (¿bug, o esa fuente no tiene portadas?). Con el Kotlin
delante deja de serlo:

> Si el Kotlin asigna `thumbnail_url` en `popularMangaFromElement` y el port devuelve 0/10 → **BUG**.
> Si el Kotlin nunca lo asigna → 0/10 es **fiel**.

Eso convierte un numero que exige juicio humano en un veredicto automatico. Es lo que hace que
30 494 celdas sean tratables.

### Fase A — diff estatico contra el Kotlin. Cero peticiones de red, cubre 1787.

Por extension, del fuente Kotlin: que clase extiende y **que metodos sobreescribe**
(`popularMangaSelector`, `popularMangaFromElement`, `mangaDetailsParse`, `chapterListSelector`,
`pageListParse`, `imageUrlParse`…). El port Python tiene una forma FIJA por motor: **todo override
que caiga fuera de esa forma se pierde en silencio**. Eso rellena `differences[]` y degrada los
`features` que el motor generico no puede cumplir.

Es el paso que habria cazado Temple Scan sin tocar la red, y el que mas cubre por euro. **Empezar
por aqui.**

### Fase B — sonda en vivo, muestreada y agregada POR MOTOR

Cambiar la metrica de `tools/smoke.py`: que `probe()` registre `cover: 7/10`, no `ok`. Y agregar
**por motor**, no por extension — un campo vacio en muchas fuentes del mismo motor es **un bug de
motor**, no N bugs de sitio. Asi aparece `_image_url` ignorando `background-image` sin inspeccionar
306 sitios.

### Fase C — fixtures como suite de regresion

Guardar el HTML de la muestra. Despues, cambiar `_image_url` se re-verifica contra los 306 madara
**sin red y de forma determinista**. Sin esto no se puede tocar un motor compartido con confianza,
que es justo lo que pide el TODO de las portadas. Encaja con el enfoque de fixture de conformidad
que ya existe en la suite del backend.

### Dos cosas que desbloquean el resto

1. **Rellenar `engine`** en los 1901 (esta vacio; el dato esta en `index.json`). Es casi gratis y
   es lo UNICO que permite agregar por motor. Sin eso no hay priorizacion posible.
2. **Priorizar por radio de explosion.** `custom` son 1006 casos de uno, pero los otros ~900 se
   reparten en **7 motores**. Hacer bien esos siete cubre casi la mitad del catalogo con siete
   arreglos.

### Restricciones que no deben saltarse

- **NO barrer 1901 sitios en vivo.** Son sitios de scanlation y las peticiones salen desde la IP
  del usuario: todo el diseño de `RateLimitedClient` + `SOURCE_RATE_LIMIT_CEILING` existe para que
  «la fuente no banee al usuario». Muestra estratificada por motor —10-15 por motor bastan para
  detectar un fallo sistematico— y a partir de ahi, fixtures.
- **Fijar `kotlin_commit`** (hoy vacio en los 1901) o el diff de la fase A no es reproducible en
  cuanto Keiyoushi actualice.
- Los **114 `BLOCKED_MAPPING`** no tienen `kotlin_module`: quedan fuera de la fase A por
  construccion. Decidir si se resuelven a mano o se aceptan como zona sin cobertura, pero que la
  decision sea explicita y no un silencio.

### Salida esperada

Lo mas valioso NO es el veredicto por extension: es **la lista priorizada de bugs de motor**,
ordenada por cuantas fuentes arregla cada uno. El caso Temple Scan ya aporta la primera entrada
confirmada de esa lista.
