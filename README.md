# Colector de Videos Rimay

Web pequeña para recopilar videos de **Lengua de Señas Ecuatoriana (LSEC)**.
El usuario elige una de las **30 frases** del dataset de Rimay-IA, se graba con
la cámara, y el video se sube **directamente a Google Drive** de la cuenta que
se autorice (el dueño inicia sesión una sola vez con OAuth).

## Estructura

``` text
Colector-Videos/
├── app.py              # Servidor FastAPI (web + OAuth + subida)
├── drive_upload.py     # Subida a Google Drive (OAuth)
├── frases.json         # Las 30 frases del dataset
├── static/index.html   # Frontend (selector + cámara + grabación)
├── requirements.txt
├── .env.example
└── token.json          # Token de sesión (se genera al conectar, NO se sube a git)
```

## Por qué OAuth y no cuenta de servicio

Desde **abril de 2025** Google quitó la cuota de almacenamiento a las cuentas de
servicio nuevas, por lo que **no pueden subir archivos** a un Drive personal
(`@gmail.com`). Con **OAuth** el dueño de la carpeta autoriza la app una vez y
los videos se guardan en **su** Drive usando **su** cuota. Es la única vía que
funciona con cuentas de Google gratuitas.

## Configuración de Google Cloud (una sola vez)

1. Ve a [Google Cloud Console](https://console.cloud.google.com) → tu proyecto
   (`gen-lang-client-0869264172`).
2. Activa la **Google Drive API** (APIs y servicios → Biblioteca → "Google
   Drive API" → Habilitar).
3. Crea una credencial de tipo **IDs de cliente de OAuth 2.0 → Aplicación web**.
   Si ya tienes el archivo `client_secret_*.json` descargado, úsalo; si no, crea
   una nueva y descárgala.
4. En la credencial, en **URI de redireccionamiento autorizados** agrega:
   - `http://localhost:8000/oauth/callback` (para probar en tu PC)
   - Y cuando despliegues, la URL de tu sitio (ej. `https://tu-sitio.com/oauth/callback`)
5. En **Pantalla de consentimiento**: pon el modo de prueba y agrega tu cuenta
   como **usuario de prueba** (si la app está en "En producción" no hace falta,
   pero igual aparece un aviso de "App no verificada").
6. Copia `.env.example` a `.env` y pega `GOOGLE_CLIENT_ID` y
   `GOOGLE_CLIENT_SECRET` del JSON descargado.

### Carpeta destino en tu Drive

Crea (o elige) la carpeta donde van los videos, ábrela en el navegador y copia
el **ID** de la URL: `https://drive.google.com/drive/folders/ESTE_ID`
→ ponlo en `DRIVE_FOLDER_ID` del `.env`.

## Ejecución local

``` bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python -m uvicorn app:app --reload
```

Abre `http://localhost:8000`, haz clic en **Conectar Google**, autoriza con tu
cuenta y ya puedes grabar. Los videos se guardan en tu Drive dentro de
`<Tu carpeta>/<Frase>/`.

## Despliegue para varios usuarios (internet)

1. Sube el proyecto a un repo de GitHub **sin** `.env`, `token.json` ni
   `service_account.json`.
2. En Render / Railway / VPS:
   - Agrega las variables del `.env` como variables de entorno.
   - `GOOGLE_REDIRECT_URI` debe apuntar a tu dominio
     (ej. `https://tu-sitio.onrender.com/oauth/callback`).
   - Agrega esa misma URL en la credencial OAuth de la consola (paso 4 arriba).
   - **En producción define `COOKIE_SECURE=1`** (el sitio va por HTTPS) y
     `GOOGLE_REFRESH_TOKEN` (el refresh token que se genera al conectar una vez;
     en contenedores efímeros el `token.json` en disco no persiste).
   - Comando de inicio: `uvicorn app:app --host 0.0.0.0 --port $PORT`.
3. Abre tu sitio, conéctate con Google una vez (tú) y listo: los demás solo
   graban, los videos caen en tu Drive.

### Prueba rápida con túnel (ngrok)
``` bash
ngrok http 8000
```
Te da una URL pública temporal. Recuerda añadir esa URL (más `/oauth/callback`)
a las URIs de redirección autorizadas en la consola.

## Cómo se guardan los videos

```
Mi Drive/
└── <Tu carpeta>/
    └── Buenos días/
        └── 20260101_153000_2f3a9c22_maria.webm
```

- Carpeta por frase (se crea automáticamente).
- Nombre: fecha_hora + id aleatorio + nombre del usuario.
- Formato: `webm` (MP4 en navegadores que no lo soporten).

## Endpoints

| Método | Ruta                | Descripción                                   |
| ------ | ------------------- | --------------------------------------------- |
| GET    | `/`                 | Página web                                     |
| GET    | `/api/frases`       | Las 30 frases del dataset                      |
| GET    | `/auth/iniciar`     | Inicia sesión con Google (redirige a OAuth)    |
| GET    | `/oauth/callback`   | Recibe el código y guarda el token             |
| GET    | `/api/auth/estado`  | ¿Está conectado a Google?                      |
| POST   | `/api/subir`        | Sube el video a Drive (`frase`, `usuario`, `video`) |

## Seguridad

Medidas aplicadas antes de salir a internet:

- Los secretos (`.env`, `token.json`, `service_account.json`) están en
  `.gitignore` **y** en `.dockerignore`, para que no entren al repositorio ni a
  la imagen de Docker.
- El contenedor corre como usuario **sin privilegios** (no root).
- **CORS cerrado por defecto**: el frontend se sirve del mismo servidor; si lo
  separas en otro dominio, usa `ALLOWED_ORIGINS`.
- **Anti-abuso** en `/api/subir`: límite por IP por hora
  (`MAX_SUBIDAS_POR_HORA`) y subidas simultáneas (`MAX_SUBIDAS_CONCURRENTES`).
- **Límite de tamaño** del video (`MAX_VIDEO_MB`) para no agotar la memoria.
- Flujo **OAuth con `state`** (cookie `httpOnly`) contra CSRF de inicio de sesión.
- El nombre de usuario se limpia y la extensión del archivo se valida antes de
  guardarlo en Drive; los errores internos se registran en el log pero no se
  devuelven al navegador.
- En producción, marca `COOKIE_SECURE=1` (HTTPS).

## Cómo actualizar las frases

Edita `frases.json`. Para regenerarlas desde el dataset de Rimay-IA:

``` bash
python -c "import json; d=json.load(open('../Rimay-IA/dataset/processed/dataset_maestro.json')); print(json.dumps(list(dict.fromkeys(x['label'] for x in d)), ensure_ascii=False, indent=2))" > frases.json
```
