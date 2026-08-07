---
created: 2026-08-06T03:22:00.000Z
title: El fallback de capitulos del motor Madara devuelve el mismo capitulo varias veces
area: extensions
severity: minor
files:
  - ../nyanko-extensions/engines/manual/templescanesp_es.py:202-247
  - ../nyanko-extensions/engines/manual/templescanesp_es.py:534-546
---

## Problem

Hallazgo lateral del diagnostico de Temple Scan del 2026-08-05 (ver
[2026-08-06-portadas-madara-por-background-image](./2026-08-06-portadas-madara-por-background-image.md)).
kfern pidio explicitamente tratarlo **aparte**: al lado de las portadas es cosmetico.

Igual que el otro, el arreglo vive en `E:/2023-09-04/anitracker/nyanko-extensions`, no en Nyanko.

**Medido** contra `https://aedexnox.akan01.com/serie/deja-de-fumar/`:

```
capitulos devueltos: 109 | unicos: 107 | duplicados: 2
   x3  /serie/deja-de-fumar/capitulo-105/?style=list
   x1  /serie/deja-de-fumar/capitulo-104/?style=list
```

El capitulo mas reciente sale **tres veces**. Los numeros y titulos se parsean bien; lo unico malo
es la repeticion.

## Solution

Sospecha (NO confirmada — quien lo coja que la verifique antes de tocar):
`_fallback_chapter_nodes` (`:534-546`) recorre TODOS los nodos `li`, `div` y `tr` y se queda con
cualquiera que contenga un ancla cuyo texto o `href` huela a capitulo. En un markup anidado, el
mismo ancla cae dentro del `li`, del `div` que lo envuelve y del `tr` que envuelve a ese — y cada
contenedor entra en la lista por separado. Luego `chapters` (`:228-247`) construye un
`SourceChapter` por contenedor, sin deduplicar.

Ese fallback solo corre cuando `_chapter_nodes` no encuentra `li.wp-manga-chapter`, que es
justo el caso de este sitio re-skineado.

Arreglo natural: deduplicar por `source_id` en `chapters` conservando el orden de aparicion
(`dict.fromkeys`, que es el patron que el propio fichero ya usa para las paginas en `:310-314`).
Es preferible a afinar el fallback: la deduplicacion es cierta para cualquier markup, y afinar el
selector solo mueve el problema al siguiente sitio con otra anidacion.

Ojo al alcance: `_fallback_chapter_nodes` es del motor Madara compartido — **306 bundles** del
indice usan `engine: madara`. Deduplicar es aditivo y no deberia cambiar nada donde hoy no hay
repetidos, pero conviene comprobarlo contra un Madara clasico ademas de contra Temple Scan.

Reproduccion: la misma receta del TODO hermano, terminando en
`await inner.chapters("https://aedexnox.akan01.com/serie/deja-de-fumar/")`.
