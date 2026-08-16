# Estado de la sesion — limpieza y revision de extensiones

## Historial de commits relevante (HEAD actual = 87a3946)
- `87a3946` fix(motores): deshacer la purga excesiva del wrapper Generated en las 106 fuentes reales
- `ab508d2` fix(motores): restaurar 106 manuales reales borrados por error
- `fb46048` chore: firmar el index despues de purga de fantasmas
- `eedde66` fix(motores): purgar manuales fantasmas (839 files) + parser AST comas + herencia generica
- `0b54c67` fix(motores): permitir imports generalizados y compilar sin SyntaxError de __future__  <-- **buen punto de referencia**
- `2e0fdcb` chore: ignorar validacion_humana*.txt

## Estado del working tree ANTES de limpiar
- 1642 entradas en `git status --short`
- 824 archivos STAGED en el index, todos bajo `engines/manual/` (resultado de `git checkout 0b54c67 -- engines/manual/`)
- 779 archivos modificados solo en worktree (mayoria `bundles/*.py` regenerados)
- `engines/manual/` tiene ahora 924 archivos .py

## Archivos basura creados durante la depuracion (BORRAR)
DEBUG.txt, DEBUG2.txt, DEBUG_DOUJ.txt, DEBUG3.txt, log.txt, out.txt, check_bundle.py,
patch_generate.py, patch_generate2.py .. patch_generate14.py,
reapply.py, reapply2.py, reapply_ast.py, reapply_manual.py,
test_ast.py, test_ast_madara.py, test_ast_real.py, test_compile3.py, test_contract.py,
test_deleted.py, test_exec.py, test_exec2.py, test_final_strip.py, test_generic.py,
test_instantiate.py, test_load3.py, test_load_missing.py, test_load_name.py,
test_strip.py, test_strip_loop.py

## Causa raiz del 422 "La fuente no cumple el contrato Source"
El backend valida `isinstance(instance, Source)` contra un Protocol runtime-checkable con:
name, display_name, api_version, capabilities, search, browse, chapters, pages, page_bytes.

Los bundles fallan porque a la clase concreta le falta `pages` (y en otros casos
api_version / capabilities / page_bytes). Esto lo causo un script de purga previo
(`test_deleted.py`) que borro por regex el wrapper `Generated*Source` y se llevo por
delante metodos reales de las subclases en `engines/manual/`.

Verificacion ultima ejecutada:
- doujinshell_es -> isinstance(Source): True
- anzmanga_es -> False, falta ['pages']
- akuma_es -> False, falta ['pages']

## Cambios REALES que valen la pena en tools/generate.py
1. `_refrescar_motor_en_manual`: en vez de tomar `root_class` = primera clase del engine
   que termine en "Source", usar el conjunto `engine_classes` de TODAS las clases del
   engine y quedarse con la ULTIMA coincidencia en el manual. Esto arregla comicfury
   (que declara MadaraSource y GenericSource) y doujinshell.
2. En `_manual_bundle`: eliminar el bloque que borraba `try/except ImportError` +
   stubs `class X: pass`. Era innecesario una vez arreglado el punto 1.

NOTA: `git restore tools/generate.py` fue ejecutado varias veces, hay que confirmar si
estos cambios estan aplicados o no en el archivo actual.

## Plan de limpieza
1. Borrar los archivos basura listados arriba.
2. `git reset` para desapilar el index y decidir con calma que de `engines/manual/`
   se conserva (la restauracion desde 0b54c67 es la version con metodos intactos).
3. Regenerar bundles con `python tools/generate.py`.
4. Validar contrato sobre TODOS los bundles, no solo 3.
5. Firmar index: `python tools/sign_index.py sign index.json --private-key C:\Users\kev\.config\nyanko\extension-repository-signing-key.pem`
