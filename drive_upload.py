"""Subida de videos a Google Drive mediante OAuth 2.0 (cuenta personal).

La cuenta de servicio no sirve para cuentas personales (desde abril 2025
no tienen cuota de almacenamiento), por eso se usa OAuth: el dueño de la
carpeta inicia sesión una vez y el servidor guarda el refresh token.
"""

import io
import os
import re
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _limpiar_nombre(texto: str) -> str:
    texto = re.sub(r"[^\w\s\-]", "", texto, flags=re.UNICODE)
    texto = re.sub(r"\s+", "_", texto).strip("_")
    return texto or "Sin_nombre"


class DriveCliente:
    """Cliente OAuth que sube archivos a la carpeta del dueño de la cuenta."""

    def __init__(self, client_id: str, client_secret: str, token_file: str | Path):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = Path(token_file)
        self._credenciales: Credentials | None = None
        self._drive = None
        self._flow: Flow | None = None

    # --- Flujo de autorización ---

    def _crear_flow(self, redirect_uri: str) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )

    def url_autorizacion(self, redirect_uri: str) -> str:
        flow = self._crear_flow(redirect_uri)
        self._flow = flow
        return flow.authorization_url(access_type="offline", prompt="consent")[0]

    def procesar_codigo(self, code: str, redirect_uri: str) -> None:
        flow = self._flow or self._crear_flow(redirect_uri)
        self._flow = None
        token = flow.fetch_token(code=code)
        creds = Credentials(
            token=token.get("access_token"),
            refresh_token=token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=SCOPES,
        )
        self.token_file.parent.mkdir(exist_ok=True, parents=True)
        self.token_file.write_text(creds.to_json(), encoding="utf-8")
        self._credenciales = creds

    def esta_conectado(self) -> bool:
        return self._obtener_credenciales() is not None

    # --- Drive ---

    def _obtener_credenciales(self) -> Credentials | None:
        if self._credenciales is not None:
            return self._credenciales
        token_env = os.getenv("GOOGLE_REFRESH_TOKEN")
        if token_env:
            self._credenciales = Credentials(
                token=None,
                refresh_token=token_env,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=SCOPES,
            )
        elif self.token_file.exists():
            self._credenciales = Credentials.from_authorized_user_file(
                str(self.token_file), SCOPES
            )
        else:
            return None

        if self._credenciales and self._credenciales.refresh_token:
            if self._credenciales.expired and self._credenciales.refresh_token:
                try:
                    self._credenciales.refresh(Request())
                    if not token_env:
                        self.token_file.write_text(
                            self._credenciales.to_json(), encoding="utf-8"
                        )
                except Exception:
                    self._credenciales = None
                    if self.token_file.exists():
                        self.token_file.unlink()
        return self._credenciales

    def _servicio(self):
        if self._drive is None:
            creds = self._obtener_credenciales()
            if creds is None:
                raise RuntimeError("No autorizado")
            self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._drive

    def _buscar_carpeta(self, nombre: str, padre_id: str) -> str | None:
        query = (
            f"name='{nombre}' and mimeType='application/vnd.google-apps.folder' "
            f"and '{padre_id}' in parents and trashed=false"
        )
        res = (
            self._servicio()
            .files()
            .list(q=query, fields="files(id, name)", pageSize=1)
            .execute()
        )
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def _crear_carpeta(self, nombre: str, padre_id: str) -> str:
        body = {
            "name": nombre,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [padre_id],
        }
        file = (
            self._servicio().files().create(body=body, fields="id").execute()
        )
        return file["id"]

    def _obtener_carpeta(self, nombre: str, padre_id: str) -> str:
        existente = self._buscar_carpeta(nombre, padre_id)
        if existente:
            return existente
        return self._crear_carpeta(nombre, padre_id)

    def subir_video(
        self,
        video_bytes: bytes,
        nombre_archivo: str,
        frase: str,
        carpeta_raiz_id: str | None = None,
    ) -> str:
        if not carpeta_raiz_id:
            raise ValueError(
                "Falta DRIVE_FOLDER_ID: el ID de la carpeta de tu Drive donde "
                "se guardarán los videos (el de la URL de la carpeta)."
            )
        raiz_id = carpeta_raiz_id.strip()
        frase_id = self._obtener_carpeta(frase, raiz_id)

        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_final = f"{fecha}_{nombre_archivo}"
        metadata = {"name": nombre_final, "parents": [frase_id]}
        media = MediaIoBaseUpload(
            io.BytesIO(video_bytes), mimetype="video/webm", resumable=True
        )
        archivo = (
            self._servicio()
            .files()
            .create(body=metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )
        return archivo["webViewLink"]
