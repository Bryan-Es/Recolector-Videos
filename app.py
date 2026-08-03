"""Servidor web para colectar videos de Lengua de Señas.

Elige una de las 30 frases, graba con la cámara y el video se sube
directamente a Google Drive mediante OAuth (cuenta personal del dueño).
"""

import json
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from drive_upload import DriveCliente

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
FRASES_PATH = BASE_DIR / "frases.json"
TOKEN_PATH = BASE_DIR / "token.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("colector")

app = FastAPI(title="Colector de Videos Rimay")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

_drive: DriveCliente | None = None


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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/frases")
def frases():
    with open(FRASES_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.get("/auth/iniciar")
def auth_iniciar():
    url = get_drive().url_autorizacion(redirect_uri())
    return RedirectResponse(url)


@app.get("/oauth/callback")
def oauth_callback(code: str = "", error: str | None = None):
    if error:
        return RedirectResponse("/?auth_error=" + error)
    if not code:
        raise HTTPException(status_code=400, detail="Falta el código de autorización")
    get_drive().procesar_codigo(code, redirect_uri())
    return RedirectResponse("/?auth=ok")


@app.get("/api/auth/estado")
def auth_estado():
    try:
        conectado = get_drive().esta_conectado()
    except Exception:
        conectado = False
    return {"conectado": conectado}


@app.post("/api/subir")
async def subir_video(
    video: UploadFile, frase: str = Form(...), usuario: str = Form("anonimo")
):
    frase = frase.strip()
    usuario = (usuario or "anonimo").strip()

    with open(FRASES_PATH, encoding="utf-8") as f:
        frases_validas = set(json.load(f))
    if frase not in frases_validas:
        raise HTTPException(status_code=400, detail="Frase no válida")

    contenido = await video.read()
    if not contenido:
        raise HTTPException(status_code=400, detail="El video está vacío")

    if len(contenido) > 300 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El video supera los 300 MB")

    ext = Path(video.filename or "video.webm").suffix or ".webm"
    nombre_archivo = f"{uuid.uuid4().hex[:8]}_{usuario}{ext}"
    carpeta_raiz_id = os.getenv("DRIVE_FOLDER_ID", "")

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
    except Exception as exc:
        logger.exception("Error al subir a Drive")
        raise HTTPException(status_code=500, detail=f"Error al subir a Drive: {exc}")

    return JSONResponse({"ok": True, "frase": frase, "archivo": nombre_archivo, "link": link})
