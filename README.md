# nyanko-extensions

Adapters Python externos para Nyanko, portados al contrato Source v4 desde las extensiones
de Keiyoushi (Apache-2.0). Los sitios que comparten motor se generan desde una
implementación común; las fuentes personalizadas usan el adaptador HTML/JSON y pueden
añadir perfiles específicos para lectores protegidos.

Para servir el índice instalable localmente:

```powershell
python -m http.server 8877
```

Después añade `http://127.0.0.1:8877/index.json` en **Ajustes → Fuentes**. Nyanko verifica
la firma del índice y pide aceptar una sola vez la huella de la clave del repositorio.

Cada entrada de `index.json` puede declarar un `icon_url` opcional. Los iconos se guardan
como PNG de 192×192 en `icons/<id>.png`; Nyanko podrá usar este metadato sin ejecutar el
bundle.

## Firma del índice

La firma usa Ed25519 detached sobre los bytes exactos de `index.json`. Requiere
`cryptography`:

```powershell
python -m pip install cryptography
```

Genera la clave privada **fuera del repositorio**. `openssl genpkey` escribe el secreto
directamente al archivo indicado y no lo muestra en la terminal:

```powershell
$keyDir = Join-Path $env:USERPROFILE ".nyanko"
New-Item -ItemType Directory -Force $keyDir | Out-Null
openssl genpkey -algorithm ED25519 -out (Join-Path $keyDir "repository-signing-key.pem")
```

La clave es PEM PKCS8 y debe mantenerse privada; no la copies ni la añadas al repositorio.
En sistemas POSIX aplica `chmod 600 /ruta/repository-signing-key.pem`.

Firma o verifica un índice con:

```powershell
python tools/sign_index.py sign index.json --private-key "$env:USERPROFILE\.nyanko\repository-signing-key.pem"
python tools/sign_index.py verify index.json
```

También puede definirse `NYANKO_REPO_SIGNING_KEY` con la ruta de la clave. La firma produce:

- `index.json.pub`: base64 estándar de los 32 bytes raw de la clave pública, más `\n`.
- `index.json.sig`: base64 estándar de la firma raw de 64 bytes, más `\n`.

Los dos archivos se escriben mediante reemplazo atómico. Cualquier cambio en `index.json`,
`index.json.pub` o `index.json.sig` invalida la verificación.

## Generación y validación

Para regenerar todas las extensiones, firmar al final y validar todos los artefactos:

```powershell
$env:NYANKO_REPO_SIGNING_KEY = "$env:USERPROFILE\.nyanko\repository-signing-key.pem"
python tools/generate.py
$env:PYTHONPATH = "..\Nyanko\apps\backend"
python tools/validate.py
```

También se puede pasar `--signing-key RUTA` a `tools/generate.py`. Si no se proporciona
ninguna clave y la variable no existe, la generación conserva los sidecars actuales sin
borrarlos ni reemplazarlos. `tools/validate.py` verifica la firma antes de leer las entradas
del índice o cargar una extensión.

Prueba local:

```powershell
$env:PYTHONPATH = "..\Nyanko\apps\backend"
..\Nyanko\apps\backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
