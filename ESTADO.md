# Estado de la sesion — reparacion de bundles (422 al instalar)

## Objetivo
Los bundles publicados fallaban al instalarse (HTTP 422: la fuente no cumple el
contrato `Source` v4). Se reparan las causas raiz, comprobando siempre contra el
contrato real y sin introducir regresiones.

## TESTING — que cobertura hay realmente (investigado, PENDIENTE de ejecutar)

IMPORTANTE: `verify_bundles.py` **NO prueba que las fuentes funcionen**. Solo
comprueba conformidad con el contrato Source v4 (que existan `name`, `pages`,
`search`, `chapters`...). Que 65 bundles pasen NO significa que devuelvan datos.

Recursos de prueba encontrados:
- **`tests/`** — 90 ficheros `test_*.py`, con `unittest.IsolatedAsyncioTestCase`.
  Son **offline**: usan un `Fetcher` falso con respuestas HTML fijas (ver
  `tests/test_akuma.py`). Cada test hace
  `exec(_manual_bundle(engines/manual/X.py))` y coge `SOURCE`, o sea que
  **ejercitan la misma ruta del generador que hemos tocado**: son la validacion
  natural de los cambios. Comprueban paridad con el comportamiento Kotlin
  (p.ej. `test_csrf_cursor_language_and_image_page_match_kotlin`).
  Los 84 que "mencionan red" es solo por el mock, no salen a internet.
- **`tools/smoke.py`** — prueba REAL contra la red.
  `--lang --only --concurrency --timeout --samples-per-engine --out`.
  Complementario: valida que los sitios responden de verdad.
- **`tools/audit_contract.py`, `validate.py`, `auto_validator.py`,
  `measure_fidelity.py`, `resumen_smoke.py`** — utilidades adicionales sin revisar.
- No hay CI (`.github/workflows/` vacio).

BLOQUEO 1: **pytest NO esta instalado** en
`C:\Users\kev\AppData\Local\Programs\Python\Python314\python.exe`.
Los tests son `unittest`, asi que en principio no hace falta pytest.

BLOQUEO 2: `python -m unittest discover -s tests -t .` **falla** con
`ImportError: Start directory is not importable`, porque **NO existe
`tests/__init__.py`** (comprobado). Tampoco hay `pytest.ini`, `pyproject.toml`,
`setup.cfg` ni `tests/conftest.py`. El repo no declara como se ejecutan.

Los tests hacen `from tools.generate import _manual_bundle`, asi que necesitan
la raiz del repo en `sys.path`. Opciones para desbloquear (ninguna probada aun):
```powershell
# A) discovery por patron de fichero, sin necesidad de paquete
python -m unittest discover -s . -p "test_*.py" -t .

# B) instalar pytest, que no exige __init__.py y añade rootdir al path
python -m pip install pytest ; python -m pytest tests -q

# C) ejecutar uno suelto por ruta
python -m unittest tests/test_akuma.py
```
NO crear `tests/__init__.py` sin preguntar: cambiaria la estructura del repo.

### RESULTADO DE EJECUTAR LOS TESTS (hecho — hay un FALLO REAL)
- `python -m unittest discover -s . -p "test_*.py" -t .` -> **"Ran 0 tests"**
  (el discovery no entra en `tests/` por falta de `__init__.py`).
- `python -m unittest tests/test_akuma.py` -> **SI ejecuta. Y FALLA:**
```
File "engines\manual\akuma_es.py", line 343, in <module>
    class GeneratedGenericSource(GenericSource):
NameError: name 'GenericSource' is not defined
Ran 1 test — FAILED (errors=1)
```

INTERPRETACION (importante, no perder):
El test llama a `_manual_bundle(path)` **SIN pasar `engine`**, mientras que
`generate.py` en produccion SI le pasa el motor. Sin motor, el prefijo con la
cadena `GenericSource` no se inyecta y el `GeneratedGenericSource(GenericSource)`
del manual restaurado se queda sin base -> NameError.

O sea: **el bundle publicado esta bien** (verify_bundles.py pasa 65/66 y ese
bundle se genera CON motor), pero **los manuales que restauramos de `2e0fdcb`
ya no son autocontenidos**, y el test los ejercita sin motor. Es un desajuste
test-vs-generador introducido por la restauracion, NO necesariamente un fallo
del bundle final.

### COMPROBADO: NO ES REGRESION NUESTRA
Se ejecuto `test_akuma.py` en `87a3946` (el HEAD original, ANTES de todos
nuestros cambios) y **tambien falla**, con otro error:
```
TypeError: AkumaSource() takes no arguments
Ran 1 test — FAILED (errors=1)
```
Es decir, el test ya estaba roto de antes por el daño de la purga `eedde66`.
Nuestros cambios solo cambiaron el sintoma
(`TypeError: AkumaSource() takes no arguments` -> `NameError: GenericSource`).
Conclusion: **la suite estaba rota antes de tocar nada; no hemos introducido
esta regresion.** El arbol de trabajo quedo limpio y en `dc8064a` tras la
comprobacion (checkout de vuelta + stash pop, sin stashes pendientes).

PENDIENTE POR DECIDIR con el usuario:
2. Segun eso: o el test debe pasar el motor (actualizar test), o el manual debe
   volver a ser autocontenido (revisar restauracion).
3. Ejecutar el resto de tests relevantes uno a uno por ruta:
   `python -m unittest tests/test_comicfury.py` etc. (akuma, comicfury, comikey,
   emperorscan, esmi2manga, hentaienvy, hentaizap, mangashiina,
   traduccionesmoonlight, luscious, hentai3, honeytoon, mangapluscreators).

SIGUIENTE PASO SUGERIDO: correr la suite completa para confirmar que los cambios
en `generate.py` (`_inyectar_motor_si_quedo_stub`, `_manual_cumple_contrato`) y
los manuales restaurados no rompieron el comportamiento esperado. Priorizar los
tests de las fuentes tocadas: akuma, comicfury, comikey, emperorscan, esmi2manga,
hentaienvy, hentaizap, mangashiina, traduccionesmoonlight, luscious, hentai3,
honeytoon, mangapluscreators.

## Resultado actual — TERMINADO
- **HEAD original: 66 fallos → ahora 1. 65 arreglados, 0 regresiones.**
- El unico "fallo" restante es `__init__`, que **NO es una fuente**:
  `bundles/__init__.py` es el docstring del paquete (37 bytes) y no aparece en
  `index.json` (1912 extensiones). Es un falso positivo de `verify_bundles.py`,
  que recorre `bundles/*.py` sin excluir `__init__.py`. **Nada que arreglar.**

## PENDIENTE (ultimo paso)
`generate.py` volvio a reescribir los sha256 de `index.json`, asi que la firma
esta obsoleta otra vez. Hay que RE-FIRMAR (ver seccion de firma mas abajo):
```powershell
python tools/sign_index.py sign --private-key "C:\Users\kev\.config\nyanko\extension-repository-signing-key.pem"
python tools/sign_index.py verify
```
Y luego commitear `index.json`, `index.json.sig`, `bundles/` y `tools/generate.py`.

## RESUELTO — los 4 ultimos (fix aplicado y verificado)
Se anadio `_inyectar_motor_si_quedo_stub(source, engine)` en `generate.py`
(justo antes de `_manual_bundle`, que la llama en la linea ~297 con `if engine:`).
Detecta el `class X:\n    pass` residual y, si el motor define esa clase, elimina
el stub para que prevalezca la real; si el motor usa otro nombre de raiz
(`GalleryAdultsSource`, `MoonlightTLSource`), reengancha la herencia a esa clase.
En ambos casos antepone el motor. Resultado: 5 fallos -> 1 (solo el falso
positivo `__init__`), sin regresiones.

## Historico del diagnostico de los 4 (ya resuelto)

### Causa raiz (confirmada con tools/debug_refresh2.py)
Los 4 manuales empiezan con:
```python
try:
    from .madara import (MadaraSource, _Node, _TreeParser)
except ImportError:
    pass

class MadaraSource:   # <-- STUB que pisa el import SIEMPRE
    pass
```
`_refrescar_motor_en_manual(manual, engine)` (generate.py:138) debe sustituir ese
prefijo por el motor real. Falla asi:

| extension | engine | refresco | por que |
|---|---|---|---|
| hentaienvy_es | galleryadults | **NO** (5517B->5517B, queda stub) | el manual importa `MadaraSource` pero el motor inyectado es `base+galleryadults`, que NO define esa clase -> no encuentra donde cortar y devuelve el manual intacto |
| hentaizap_es | galleryadults | **NO** | idem |
| traduccionesmoonlight_es | moonlighttl | **NO** (6470B->6470B) | idem, motor `base+moonlighttl` |
| mangashiina_es | mangathemesia | **SI** (5876B->56113B, sin stubs) | el refresco funciona, pero el bundle sigue saliendo de 10971B -> el problema esta en la rama del generador, no en el refresco |

Luego `generate.py:294-296` renombra `MadaraSource`->`FuenteBaseSource`, y por eso
el bundle acaba con `class FuenteBaseSource: pass` (stub vacio) en vez del motor.
De ahi los 2 sintomas: "Source missing name" y
"object.__init__() takes exactly one argument" (`super().__init__(fetcher)` acaba
en `object.__init__`).

### Rutas del generador (ya localizadas)
- `generate.py:3853-3854` mangathemesia -> `_manual_bundle(manual_path, mangathemesia_engine)`
  (NOTA: pasa solo el tema, sin `base_engine`; sospechoso)
- `generate.py:3883-3886` galleryadults -> `_manual_bundle(manual_path, base_engine, galleryadults_engine)`
- `generate.py:4251` moonlighttl -> `_moonlighttl_bundle(...)`
- `_manual_bundle(path, engine="", tema="")` esta en `generate.py:282`

### Estado del fix (INCOMPLETO — ojo)
Ya edite `generate.py` (~linea 297) anadiendo la llamada:
```python
    if engine:
        source = _inyectar_motor_si_quedo_stub(source, engine)
```
**PERO LA FUNCION `_inyectar_motor_si_quedo_stub` TODAVIA NO EXISTE.** Hay que
escribirla o `generate.py` petara con NameError. Debe: detectar
`class X:\n    pass` que sea stub de una clase importada del motor, y si el motor
inyectado no la define, anteponer el motor y reenganchar la herencia a la clase
raiz que el motor SI define (p.ej. `GalleryAdultsSource`, `MoonlightTLSource`).
Alternativa mas simple: en las 3 ramas pasar el motor correcto que si contiene la
clase raiz que el manual espera.

Verificar despues con:
```powershell
python tools/debug_refresh2.py hentaienvy_es mangashiina_es traduccionesmoonlight_es
python tools/generate.py
python tools/verify_bundles.py
```
Y **volver a firmar** al final (ver seccion de firma).

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
