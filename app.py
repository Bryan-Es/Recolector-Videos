"""Servidor web para colectar videos de Lengua de Señas.

Elige una de las 30 frases, graba con la cámara y el video se sube
directamente a Google Drive mediante OAuth (cuenta personal del dueño).
"""

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from drive_upload import DriveCliente, _limpiar_nombre

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
FRASES_PATH = BASE_DIR / "frases.json"
TOKEN_PATH = BASE_DIR / "token.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("colector")

app = FastAPI(title="Colector de Videos Rimay")

# CORS: el frontend se sirve desde el mismo servidor, por lo que por defecto no
# se permite ningún origen externo. Si el frontend vive en otro dominio, define
# ALLOWED_ORIGINS en el .env con la lista separada por comas.
_origines_permitidos = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origines_permitidos or [],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

TAMANO_MAX_VIDEO = int(os.getenv("MAX_VIDEO_MB", "200")) * 1024 * 1024
EXTENSIONES_VIDEO = {".webm", ".mp4", ".mov", ".mkv", ".ogv", ".m4v", ".ogg"}
MAX_USUARIO_LEN = 60

_drive: DriveCliente | None = None


class RateLimiter:
    """Límite simple por clave (IP) con ventana deslizante, en memoria."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def permitir(self, key: str) -> bool:
        ahora = time.monotonic()
        historial = [t for t in self._hits.get(key, []) if ahora - t < self.window]
        self._hits[key] = historial
        if len(historial) >= self.max_requests:
            return False
        historial.append(ahora)
        return True


_limite_subidas = RateLimiter(
    int(os.getenv("MAX_SUBIDAS_POR_HORA", "30")), 3600
)
_semaforo_subidas = asyncio.Semaphore(
    int(os.getenv("MAX_SUBIDAS_CONCURRENTES", "3"))
)


def ip_cliente(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "desconocido"


def get_drive() -> DriveCliente:
    global _drive
    if _drive is None:
        if not CLIENT_ID or not CLIENT_SECRET:
            raise HTTPException(
                status_code=500,
                detail="Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en el .env",
            )
        _drive = DriveCliente(CLIENT_ID, CLIENT_SECRET, TOKEN_PATH)
    return _drive


def redirect_uri() -> str:
    return os.getenv(
        "GOOGLE_REDIRECT_URI", f"http://localhost:{os.getenv('PORT', 8000)}/oauth/callback"
    )


def cargar_frases() -> list[str]:
    with open(FRASES_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/frases")
def frases():
    return cargar_frases()


@app.get("/auth/iniciar")
def auth_iniciar():
    state = secrets.token_urlsafe(32)
    url = get_drive().url_autorizacion(redirect_uri(), state=state)
    response = RedirectResponse(url)
    response.set_cookie(
        "oauth_state",
        state,
        httponly=True,
        samesite="lax",
        secure=os.getenv("COOKIE_SECURE") == "1",
        max_age=600,
    )
    return response


@app.get("/oauth/callback")
def oauth_callback(
    code: str = "", error: str | None = None, state: str = "", request: Request = None
):
    if error:
        return RedirectResponse("/?auth_error=" + error)
    if not code:
        raise HTTPException(status_code=400, detail="Falta el código de autorización")

    esperado = request.cookies.get("oauth_state")
    if not esperado or not state or not secrets.compare_digest(esperado, state):
        raise HTTPException(status_code=403, detail="Estado de autorización inválido")

    email_permitido = os.getenv("GOOGLE_ADMIN_EMAIL", "").strip() or None
    try:
        get_drive().procesar_codigo(code, redirect_uri(), email_permitido)
    except ValueError as exc:
        logger.warning("Conexión rechazada: %s", exc)
        return RedirectResponse("/?auth_error=cuenta_no_autorizada")

    response = RedirectResponse("/?auth=ok")
    response.delete_cookie("oauth_state")
    return response


@app.get("/api/auth/estado")
def auth_estado():
    try:
        conectado = get_drive().esta_conectado()
    except Exception:
        conectado = False
    return {"conectado": conectado}


@app.get("/api/videos/{frase}")
def video_referencia(frase: str):
    frases_validas = set(cargar_frases())
    if frase not in frases_validas:
        raise HTTPException(status_code=404, detail="Frase no válida")

    try:
        drive = get_drive()
    except HTTPException:
        raise HTTPException(status_code=401, detail="No conectado a Google")

    carpeta_raiz_id = os.getenv("DRIVE_FOLDER_ID", "")
    try:
        ref = drive.video_referencia(frase, carpeta_raiz_id)
    except RuntimeError:
        raise HTTPException(status_code=401, detail="No conectado a Google")
    except Exception:
        logger.exception("Error al buscar video de referencia")
        raise HTTPException(status_code=500, detail="Error al buscar el video de referencia")

    if ref is None:
        raise HTTPException(status_code=404, detail="No hay video de referencia para esta frase")

    file_id, mime_type = ref
    return StreamingResponse(
        drive.stream_video(file_id),
        media_type=mime_type or "video/mp4",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/subir")
async def subir_video(
    request: Request,
    video: UploadFile,
    frase: str = Form(...),
    usuario: str = Form("anonimo"),
):
    if not _limite_subidas.permitir(ip_cliente(request)):
        raise HTTPException(status_code=429, detail="Demasiadas subidas. Inténtalo más tarde.")

    frase = frase.strip()
    frases_validas = set(cargar_frases())
    if frase not in frases_validas:
        raise HTTPException(status_code=400, detail="Frase no válida")

    usuario = _limpiar_nombre(usuario or "anonimo")[:MAX_USUARIO_LEN] or "anonimo"
    ext = (Path(video.filename or "video.webm").suffix or ".webm").lower()
    if ext not in EXTENSIONES_VIDEO:
        ext = ".webm"
    nombre_archivo = f"{uuid.uuid4().hex[:8]}_{usuario}{ext}"
    carpeta_raiz_id = os.getenv("DRIVE_FOLDER_ID", "")

    async with _semaforo_subidas:
        contenido = await video.read()
        if not contenido:
            raise HTTPException(status_code=400, detail="El video está vacío")
        if len(contenido) > TAMANO_MAX_VIDEO:
            raise HTTPException(status_code=413, detail="El video supera el tamaño máximo")

        try:
            drive = get_drive()
            link = drive.subir_video(contenido, nombre_archivo, frase, carpeta_raiz_id)
            logger.info("Video %s subido para frase '%s'", nombre_archivo, frase)
        except RuntimeError:
            raise HTTPException(
                status_code=401,
                detail="No estás conectado a Google. Haz clic en 'Conectar Google'.",
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error al subir a Drive")
            raise HTTPException(status_code=500, detail="No se pudo subir el video. Inténtalo de nuevo.")

    return JSONResponse({"ok": True, "frase": frase, "archivo": nombre_archivo, "link": link})
