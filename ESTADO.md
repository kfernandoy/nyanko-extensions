# Estado de la sesion — reparacion de bundles (422 al instalar)

## Objetivo
Los bundles publicados fallaban al instalarse (HTTP 422: la fuente no cumple el
contrato `Source` v4). Se reparan las causas raiz, comprobando siempre contra el
contrato real y sin introducir regresiones.

## Resultado actual
- **HEAD original: 66 fallos → ahora 5. 61 arreglados, 0 regresiones.**
- Restantes: `__init__` (no es fuente, falso positivo), `hentaienvy_es`,
  `hentaizap_es`, `mangashiina_es`, `traduccionesmoonlight_es`.

## Comandos clave
```powershell
python tools/generate.py                 # regenera bundles/ (avisa de firma pendiente)
python tools/verify_bundles.py           # verifica todos contra el contrato real
python tools/verify_bundles.py --only X,Y -v
```
El contrato real se carga de `E:\2023-09-04\anitracker\Nyanko\apps\backend`.
Miembros exigidos: `api_version, browse, capabilities, chapters, display_name,
name, page_bytes, pages, search`. En la practica el que faltaba siempre era
`pages`.

## Nota de PowerShell
`git show ... > archivo` **corrompe** la codificacion (reencoda a UTF-16). Para
leer blobs del historial hay que usar `subprocess.run([...]).stdout` desde
Python y escribir en binario. Evitar tambien regex con comillas dentro de
`python -c`: PowerShell las destroza. Mejor crear un script en `tools/`.

## Causa raiz comun
El commit `eedde66` ("purgar manuales fantasmas") recorto manuales reales de
~20KB a ~7KB, borrando el motor inlineado que aportaba `pages`, y dejo el
`SOURCE` apuntando a clases inexistentes o a stubs vacios. `ab508d2` /
`87a3946` intentaron enmendarlo parcialmente.
Ultimo commit sano previo a la purga: **`2e0fdcb`**.

## Commits de esta sesion
1. `9e93440` fix(comicfury): restaurar ComicFurySource borrada por la purga
   — 14 manuales restaurados de `2e0fdcb` + arreglo del import multilinea de
   `.madara` sin parentesis. 66 → 20.
2. `ebd34f8` fix(dragon): reenganchar overlays huerfanos de emperorscan/esmi2manga
   — heredaban de `DragonTranslationOrgSource`, que no existe (la real es
   `DragontranslationorgSource`). 20 → 18.
3. `7b28560` fix(manuales): restaurar motor purgado y apuntar SOURCE al overlay
   — 9 manuales restaurados + 8 con SOURCE reapuntado. 17 → 9.
4. `0325c49` fix(generate): no preferir un manual cuyo SOURCE no implementa pages
   — sustituye la lista blanca hardcodeada de `generate.py:3721` por
   `_manual_cumple_contrato()`. 9 → 5.
   (Nota: `comicfury_es` se arreglo entre 2 y 3, 18 → 17, reconstruido desde
   `comicfury_en` por ser el unico de 14 variantes con stub `.base`.)

## Herramientas creadas (en `tools/`)
- `find_missing_source.py` — detecta `SOURCE = X` con `X` inexistente;
  `--recover` busca la clase en el historial.
- `why_no_pages.py <manuales>` — vuelca clases, bases y metodos; marca quien
  tiene `pages`.
- `restore_purged.py` — restaura desde `2e0fdcb` los manuales que perdieron
  `pages`; `--history` rastrea varios commits; `--apply` escribe.
- `fix_source_target.py` — reapunta `SOURCE` al overlay que alcanza `pages`.
  Resuelve herencia dentro del modulo; solo toca los realmente rotos.
- `fix_comicfury_es.py` — reconstruye `comicfury_es` desde `comicfury_en`.

## Firma del indice — RESUELTO
La clave privada original estaba en el equipo:
```
C:\Users\kev\.config\nyanko\extension-repository-signing-key.pem
```
Su clave publica derivada coincide **exactamente** con `index.json.pub`
(`fnZX/U9lsUi0amQfL1fRKJ1uBMOwWAPYo0K9odjU/sE=`), asi que NO es una clave nueva:
`index.json.pub` no cambia y ningun usuario tiene que volver a confiar en el
repo. Importante porque el backend (v16, tabla `trusted_repo_keys`) exige
aceptacion explicita de la clave y no la deriva solo.

Comando correcto (ojo: es `--private-key`, no `--signing-key`):
```powershell
python tools/sign_index.py sign --private-key "C:\Users\kev\.config\nyanko\extension-repository-signing-key.pem"
python tools/sign_index.py verify   # -> "Firma valida"
```
Estado: **firmado y verificado**. Solo cambia `index.json.sig`.

AVISO CONOCIDO: `generate.py` imprime al final "index.json cambio pero no se
volvio a firmar" porque comprueba la firma ANTES de reescribir el indice. Si
`sign_index.py verify` dice "Firma valida", el aviso es un falso positivo. Pero
si `generate.py` llega a modificar los sha256, hay que volver a firmar DESPUES.
Regla: **firmar siempre como ultimo paso, tras el ultimo `generate.py`**.

## Siguientes pasos
Quedan 4 fuentes reales, en 2 grupos:
- `hentaienvy_es`, `hentaizap_es` → "Source missing name"
- `mangashiina_es`, `traduccionesmoonlight_es` → "object.__init__() takes
  exactly one argument": la clase base queda como stub y `super().__init__(fetcher)`
  acaba en `object.__init__`.
Ambos parecen la misma familia de dano (stub base sin la cadena real).
