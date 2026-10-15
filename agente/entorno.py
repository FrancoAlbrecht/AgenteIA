import os
from dataclasses import dataclass
from dotenv import load_dotenv

RAIZ_DEL_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(RAIZ_DEL_PROYECTO, ".env"))

@dataclass(frozen=True)
class Credenciales:
    redmine_url: str          # siempre termina en "/"
    redmine_api_key: str
    smtp_servidor: str
    smtp_puerto: str
    smtp_usuario: str         # cuenta de Gmail dueña del refresh token
    remitente: str
    oauth_client_id: str
    oauth_client_secret: str
    oauth_refresh_token: str

    @classmethod
    def desde_entorno(cls):
        url = os.getenv("REDMINE_URL")
        if url and not url.endswith("/"):
            url += "/"
        return cls(
            redmine_url=url,
            redmine_api_key=os.getenv("REDMINE_API_KEY"),
            smtp_servidor=os.getenv("SMTP_SERVER"),
            smtp_puerto=os.getenv("SMTP_PORT"),
            smtp_usuario=os.getenv("SMTP_USERNAME"),
            remitente=os.getenv("SMTP_FROM_EMAIL"),
            oauth_client_id=os.getenv("GMAIL_OAUTH_CLIENT_ID"),
            oauth_client_secret=os.getenv("GMAIL_OAUTH_CLIENT_SECRET"),
            oauth_refresh_token=os.getenv("GMAIL_OAUTH_REFRESH_TOKEN"),
        )


def bandera_activa(nombre):
    return os.getenv(nombre, "").strip() == "1"
