import smtplib
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage
import requests

URL_TOKEN_GOOGLE = "https://oauth2.googleapis.com/token"
TEXTO_SIN_HTML = "Este correo requiere un cliente que soporte HTML."


@dataclass(frozen=True)
class Correo:
    """Un correo listo para mandar (o para guardar en modo vista previa)."""
    nombre: str        # a quién está dirigido (para el log y el resumen de ejecución)
    destino: str       # casilla final, ya aplicado el modo prueba y las excepciones
    etiqueta: str      # [PRODUCCIÓN], [REDIRIGIDO A ADMIN], ... (sólo para el log)
    asunto: str
    html: str
    archivo: str       # nombre del .html en modo vista previa (sin extensión)
    detalle: str = ""  # dato extra para el log, ej. " (peticiones #1, #2)"


def obtener_access_token(credenciales):
    respuesta = requests.post(
        URL_TOKEN_GOOGLE,
        data={
            "client_id": credenciales.oauth_client_id,
            "client_secret": credenciales.oauth_client_secret,
            "refresh_token": credenciales.oauth_refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    respuesta.raise_for_status()
    return respuesta.json()["access_token"]


@contextmanager
def _conexion_smtp(credenciales, access_token):
    with smtplib.SMTP(credenciales.smtp_servidor, int(credenciales.smtp_puerto)) as servidor:
        servidor.starttls()
        cadena_auth = f"user={credenciales.smtp_usuario}\x01auth=Bearer {access_token}\x01\x01"
        servidor.auth("XOAUTH2", lambda challenge=None: cadena_auth)
        yield servidor


def _mensaje(remitente, destino, asunto):
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = destino
    return msg


def enviar_correos(credenciales, access_token, correos):
    print("\nIniciando motor de envío de correos...")
    enviados = 0
    with _conexion_smtp(credenciales, access_token) as servidor:
        for c in correos:
            msg = _mensaje(credenciales.remitente, c.destino, c.asunto)
            msg.set_content(TEXTO_SIN_HTML)
            msg.add_alternative(c.html, subtype="html")
            try:
                servidor.send_message(msg)
                # El repo es público: en el log van nombres y números de petición, nunca contenido.
                print(f"[OK] {c.etiqueta} Correo para: {c.nombre} -> Enviado a: {c.destino}{c.detalle}")
                enviados += 1
            except Exception as e:
                print(f"[ERROR] Error al enviar correo a {c.nombre}: {e}")
    if correos and enviados == 0:
        raise RuntimeError("Había correos para mandar pero no se pudo enviar ninguno "
                           "(ver errores individuales arriba).")
    return enviados


def enviar_texto(credenciales, access_token, destino, asunto, texto):
    """Manda un correo de texto plano (se usa para el resumen de ejecución)."""
    msg = _mensaje(credenciales.remitente, destino, asunto)
    msg.set_content(texto)
    with _conexion_smtp(credenciales, access_token) as servidor:
        servidor.send_message(msg)
