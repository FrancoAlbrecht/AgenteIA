import base64
import os
from agente.entorno import RAIZ_DEL_PROYECTO

COLOR = "#A75296"
COLOR_OSCURO = "#8B3D7C"
RUTA_LOGO = os.path.join(RAIZ_DEL_PROYECTO, "images", "LogoBlanco.png")

def _cargar_logo_base64():
    try:
        with open(RUTA_LOGO, "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"
    except Exception as e:
        print(f"[WARN] No se pudo cargar el logo: {e}")
        return None


LOGO_BASE64 = _cargar_logo_base64()


def url_peticion(redmine_url, issue_id):
    return f"{redmine_url}issues/{issue_id}"


def link_peticion(redmine_url, issue_id, estilo_extra=""):
    """'#1234' en negrita y con link a la petición en Redmine."""
    return (f"<a href='{url_peticion(redmine_url, issue_id)}' style='color: {COLOR}; "
            f"text-decoration: none; font-weight: bold;{estilo_extra}'>#{issue_id}</a>")


def saludo(nombre_pila, texto_html):
    return (f'<p style="font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">'
            f'<strong>Hola {nombre_pila},</strong><br>{texto_html}</p>')


def documento(cuerpo_html, ancho=900, pie="reporte"):
    """Correo completo alrededor de `cuerpo_html`. `ancho` en px (las tablas del resumen
    R2 necesitan más); `pie`: "reporte" o "aviso" ("Este reporte fue generado...")."""
    logo_html = (f'<img src="{LOGO_BASE64}" alt="Estudio Rivarossa" style="max-width: 280px; '
                 f'height: auto; margin-bottom: 15px;">' if LOGO_BASE64 else "")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light">
    <meta name="supported-color-schemes" content="light">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; margin: 0; padding: 0; }}
        .container {{ max-width: {ancho}px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12); }}
        .header {{ background-color: {COLOR}; background: linear-gradient(135deg, {COLOR} 0%, {COLOR_OSCURO} 100%); color: #ffffff !important; padding: 35px 20px; text-align: center; }}
        .logo-container {{ display: flex; justify-content: center; }}
        .logo-container img {{ filter: brightness(1.15) drop-shadow(0 2px 4px rgba(0,0,0,0.1)); }}
        .content {{ padding: 35px 32px; }}
        .content strong {{ color: {COLOR}; }}
        .footer {{ background-color: #f8f8f8; padding: 18px; text-align: center; font-size: 11px; color: #999; border-top: 1px solid #e8e8e8; }}
        .footer p {{ margin: 4px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo-container">
                {logo_html}
            </div>
        </div>
        <div class="content">
            {cuerpo_html}
        </div>
        <div class="footer">
            <p>Este {pie} fue generado de manera automática. &copy; Estudio Rivarossa</p>
        </div>
    </div>
</body>
</html>"""
