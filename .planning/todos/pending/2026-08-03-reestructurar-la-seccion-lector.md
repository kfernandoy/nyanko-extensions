---
created: 2026-08-03T05:10:00.000Z
title: Reestructurar «Manga local» como seccion «Lector» con Biblioteca, Explorar y Descargas
area: ui
severity: major
files:
  - apps/desktop/src/App.tsx:50,184-186,1176-1190,1245,1280-1283
  - apps/desktop/src/MangaLibraryView.tsx
  - apps/desktop/src/SourceBrowseView.tsx
  - apps/desktop/src/SourcesView.tsx
  - apps/desktop/src/DownloadsView.tsx
---

## Problem

Dictado por kfern el 2026-08-03 **explicitamente como trabajo separado para otro agente**, para no
descarrilar la fase en curso. No se implementa en la sesion que lo captura.

El origen concreto fue un fallo de navegacion que el usuario encontro probando: **si pulsa «cerrar»
en un capitulo, la app le devuelve a la biblioteca de manga local, cuando deberia devolverle al
LISTADO DE CAPITULOS**. Y del mismo modo, volver desde un listado de capitulos deberia devolverle al
PROVEEDOR, no a manga local. Tirando de ese hilo, lo que aparece es que la seccion entera esta plana
donde deberia estar anidada.

**Estado del arbol, verificado en el commit `79092ae`** — el que lo implemente no tiene que
re-derivarlo:

- `App.tsx:50` — `type View` es una **union plana de 12 vistas de primer nivel**:
  `"library" | "manga" | "local-manga" | "now-playing" | "history" | "activity" | "seasons" |
  "statistics" | "discovery" | "torrents" | "downloads" | "local-library"`.
- `local-manga` es la seccion de manga. `downloads` es una vista **de primer nivel, suelta al final**
  de la lista de secciones (`App.tsx:1176-1190` pinta el nav desde ese mismo array).
- El listado de extensiones vive hoy dentro de **Ajustes** (`SourcesView.tsx`).
- No hay jerarquia: cada vista es hermana de las demas, asi que «volver» no tiene a donde volver que
  no sea otra vista de primer nivel. Eso es la causa estructural del fallo de navegacion de arriba.

## Solution

### 1. Renombrar y reencuadrar

- **«Manga local» pasa a llamarse «Lector».**
- **Deja de abrir por defecto la carpeta de manga local.** El proposito de la seccion es trabajar en
  primera instancia con **bibliotecas de manga online que traen las extensiones**; el manga local es
  el segundo caso, no el primero. Hoy el nombre y el arranque dicen lo contrario.

### 2. Subsecciones dentro de «Lector»

**Biblioteca** — las series que el usuario ha añadido desde distintas fuentes.
- Se pueden **etiquetar**, y cada etiqueta **crea una entrada de nav con el nombre de la etiqueta**.
- Las series añadidas **conservan el nombre que les dio el proveedor**.
- Cada serie puede **asociarse al tracker** igual que se hace hoy en manga local. La profundidad de
  ese ajuste queda explicitamente abierta: el usuario dijo que se discute mas adelante.

**Explorar** — con un nav propio de tres secciones:
- **Fuentes** — las fuentes ya descargadas.
- **Extensiones** — el listado que **hoy vive en Ajustes** (`SourcesView.tsx`). Se mueve aqui.
- **Migrar** — migrar el contenido de una fuente a otra. **Funcionalidad nueva, no existe hoy.**

**Descargas** — el listado de descargas que hoy es una vista suelta de primer nivel. Se absorbe aqui.

### 3. Anidamiento de la navegacion (el fallo que lo origino)

- Cerrar un capitulo → vuelve al **listado de capitulos** de esa serie. Hoy vuelve a la biblioteca.
- Volver desde un listado de capitulos → vuelve al **proveedor**. Hoy vuelve a manga local.

Esto probablemente exige que `View` deje de ser una union plana y pase a tener una pila o una ruta
con padre. Es el cambio estructural del que cuelga todo lo demas, y conviene hacerlo **primero**.

### 4. Ajustes

En el menu general de ajustes, **el apartado «Fuentes» se reemplaza por «Lector»**. De momento es un
**cascaron**, con estos apartados dentro:
- Biblioteca
- Visor
- Descargas
- Explorar — aqui vive el listado de extensiones (el JSON)
- Almacenamiento
- Ajustes avanzados

## Notas para quien lo recoja

- **Va emparejado con [2026-08-03-ui-de-fuentes-y-navegacion-de-manga-online]** (orden de idiomas,
  iconos desde `icon_url`, rejilla portrait con lista como alternativa, buscador de extensiones).
  Ese es el retoque visual; este es la arquitectura de informacion. **Este va antes**: mover las
  extensiones de Ajustes a Explorar cambia donde aterrizan el buscador y los iconos del otro todo.
- **«Migrar» es funcionalidad nueva**, no una reubicacion. Merece su propio alcance y sus propias
  preguntas (¿que se migra: progreso, marcadores, el vinculo con el tracker? ¿que pasa si la fuente
  destino no tiene la serie?).
- **Restriccion de primer orden del proyecto** (`ecosistema-abierto-sin-porteros`): nada de esto
  puede exigirle a un autor de extension que declare nada para aparecer bien.
- El usuario dejo esto aparcado **a proposito**, estando a mitad de la fase 07, para no comprometer
  el cierre del milestone. No lo arranques sin confirmarselo.
