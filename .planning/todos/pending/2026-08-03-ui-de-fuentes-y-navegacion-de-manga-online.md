---
created: 2026-08-03T04:40:00.000Z
title: UI de fuentes y navegacion de manga online — orden de idiomas, iconos, rejilla portrait y buscador
area: ui
severity: minor
files:
  - apps/desktop/src/SourcesView.tsx:167-169,217-220,304-309
  - apps/desktop/src/sourcesDisabled.ts
  - apps/desktop/src/SourceBrowseView.tsx:453,476-481
  - apps/desktop/src/styles.css:427-450
---

## Problem

Peticion de kfern el 2026-08-03, dictada **para que la recoja otro agente**. No se implementa en la
sesion que la captura.

Cuatro cosas sobre la UI de fuentes y la navegacion de manga online. Estado del arbol VERIFICADO en
el commit `63a9e5a` — el que lo implemente NO tiene que re-derivar esto:

**Lo que YA existe y no hay que rehacer:**

- **Agrupar extensiones por idioma ya funciona**: `SourcesView.tsx:217-220` usa `groupSourceRows`
  (`sourcesDisabled.ts`) y traduce el nombre del idioma con `Intl.DisplayNames`
  (`SourcesView.tsx:304-309`). Salio en la quick `260801-ewa`. Lo que falta **no es agrupar, es el
  ORDEN de los grupos**.
- **`SourceBrowseView` ya tiene un buscador de SERIES** (`source-browse-search`, `:453`). El que se
  pide es otro: uno para filtrar EXTENSIONES.

**Lo que NO existe:**

1. **Orden de los grupos de idioma.** Dictado literal del usuario: «ordenarlos por ingles, español,
   y despues ordenar por cantidad (de mayor a menor) el resto». O sea: cabecera fija `en` → `es`, y
   la cola son los idiomas restantes ordenados por NUMERO DE EXTENSIONES, de mayor a menor.
   El usuario aclaro ademas que su primera formulacion («agrupar por idioma») estaba mal expresada:
   lo que quiere es este orden, no la agrupacion que ya hay.

   **Preguntas abiertas — resolver con el usuario, NO inventar:**
   - ¿Que pasa con el grupo «otros» / idioma nulo? Hoy queda al final, y lo fija el test
     «agrupa por idioma, ordena los grupos y deja otros al final». ¿Sigue al final, por debajo de la
     cola ordenada por cantidad?
   - ¿Desempate cuando dos idiomas tienen la misma cantidad? Sin criterio explicito el orden queda a
     merced del orden de llegada — que es exactamente el bug que se acaba de arreglar en `chapters`
     (quick `260803-5bd`). Fijar un desempate determinista y cubrirlo con un test.
   - `en` antes que `es` esta dictado tal cual por el usuario. Se asume deliberado (el grueso de los
     conectores es `en`), pero conviene confirmarlo antes de congelarlo en un test.

2. **Icono de cada extension**, desde el campo `icon_url` del indice del repo. **No es solo UI.**
   `grep icon_url` sobre todo el repo Nyanko da **cero resultados**: ni backend ni renderer lo leen.
   El usuario confirma que el campo SI viaja en el JSON del repo de extensiones — cuando se escaneo,
   su repo aun no estaba actualizado. Asi que el dato llega, pero hay que parsearlo del indice,
   persistirlo y exponerlo al renderer.

   **La decision de diseño es la mitad del trabajo**: `icon_url` es una URL de un TERCERO y el
   renderer corre con `webSecurity:true`. O se proxya por el sidecar (como ya se hace con
   `/assets/pages`, que ademas exige URL firmada desde la quick `260802-vzq`), o hay que abrir el
   CSP a hosts arbitrarios — lo segundo choca de frente con la postura de seguridad del proyecto.
   Y hay que decidir **que se pinta cuando falta el icono o la URL falla**: de los 1901 conectores
   portados, muchos no lo traeran.

3. **Las series se muestran de LAS DOS formas**: rejilla portrait **por defecto**, y lista como
   alternativa. Es un conmutador de vista, **no un reemplazo**. Hoy solo existe la lista
   (`manga-library-list source-browse-grid` con tarjetas `manga-library-open source-browse-card`,
   `SourceBrowseView.tsx:476-481`).
   A decidir: ¿la eleccion se recuerda entre sesiones? ¿Por fuente o global? Hay precedente de
   preferencias persistidas en el proyecto (`reader_prefs`, por serie).

4. **Buscador en la parte superior** para filtrar extensiones. `SourcesView` hoy solo tiene un
   `<input>`, y es el de pegar la URL del repo (`:167-169`).

## Solution

**LEER ANTES: este todo va DESPUES de
[2026-08-03-reestructurar-la-seccion-lector](./2026-08-03-reestructurar-la-seccion-lector.md).**
Esa reestructuracion mueve el listado de extensiones de Ajustes a una subseccion «Explorar →
Extensiones», o sea que **cambia el sitio donde aterrizan el buscador y los iconos de este todo**.
Implementar este primero significa colocarlos dos veces.

**Vehiculo sugerido: `/gsd-ui-phase`**, que produce un UI-SPEC antes de tocar codigo. Hay decisiones
visuales de verdad: tamaño y densidad de la rejilla, donde vive el conmutador de vista, placeholder
del icono ausente, y donde va el buscador respecto al campo de la URL del repo.

**Reparto sugerido**: la pata de backend del icono (parsear `icon_url` del indice + decidir el
transporte de la imagen) puede ir aparte y ANTES, porque arrastra una decision de seguridad. Los
puntos 1, 3 y 4 son frontend limpio y podrian ir juntos en una quick.

**Restriccion de primer orden del proyecto** (`ecosistema-abierto-sin-porteros`): nada de esto puede
exigirle a un autor de extension que declare un icono o un idioma para aparecer bien. Si falta, la
UI tiene que quedar digna igualmente.

**Severidad**: marcada `minor` por el capturador sin preguntar al usuario — es una peticion de mejora,
no un defecto, y la taxonomia blocker/major/minor/cosmetic no le encaja del todo. Cambiala si al
triarla te estorba.
