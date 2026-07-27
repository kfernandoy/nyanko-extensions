# nyanko-extensions

Adapters Python externos para Nyanko. El primer bundle es **MangaDex (ES/LatAm) 0.1.0**,
portado al contrato Source v3 desde la extensión MangaDex de Keiyoushi
(`extensions-source-main/src/all/mangadex`, Apache-2.0).

Para servir el índice instalable localmente:

```powershell
python -m http.server 8877
```

Después añade `http://127.0.0.1:8877/index.json` en **Ajustes → Fuentes**, instala el
bundle y acepta la huella mostrada por Nyanko.

Prueba local:

```powershell
$env:PYTHONPATH = "..\Nyanko\apps\backend"
..\Nyanko\apps\backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
