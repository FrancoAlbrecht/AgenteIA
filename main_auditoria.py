import os
import requests
import smtplib
import base64
import unicodedata
from datetime import datetime, timedelta, date
from email.message import EmailMessage
from dotenv import load_dotenv
from feriados import FERIADOS

load_dotenv()

# Cargar logo en base64
def cargar_logo_base64():
    """Carga la imagen del logo y la convierte a base64 para incrustarla en el HTML"""
    ruta_logo = os.path.join(os.path.dirname(__file__), "images", "LogoBlanco.png")
    try:
        with open(ruta_logo, "rb") as f:
            imagen_bytes = f.read()
            logo_b64 = base64.b64encode(imagen_bytes).decode('utf-8')
            return f"data:image/png;base64,{logo_b64}"
    except Exception as e:
        print(f"[WARN] No se pudo cargar el logo: {e}")
        return None

LOGO_BASE64 = cargar_logo_base64()

# ==========================================
# CONFIGURACIÓN GENERAL DEL AGENTE - SECTOR AUDITORÍA
# ==========================================
# Modo Prueba: mientras esté en True, TODOS los correos de auditoría se
# redirigen a CORREO_ADMIN_AUDITORIA (con los nombres y datos reales de cada
# destinatario en el cuerpo). Es independiente del modo del sector impuestos.
REDIRIGIR_A_ADMIN_AUDITORIA = True
CORREO_ADMIN_AUDITORIA = "francoalbrecht@rivarossa.com"

# Excepciones puntuales: personas cuyo correo, aun en producción, debe seguir
# llegando a CORREO_ADMIN_AUDITORIA en lugar de a su casilla real.
EXCEPCIONES_DESTINO_AUDITORIA = {}

# Trackers (tipos de petición) del sector auditoría que este reporte cubre.
TRACKERS_AUDITORIA = ["CyA Balance", "CyA Auditoria", "CyA Corte"]

# Estado que deben tener las peticiones para ser incluidas.
ESTADO_FILTRO_AUDITORIA = "Pendiente"

# Nombre del campo personalizado que define la fecha de cierre de la OT.
CAMPO_FECHA_CIERRE = "FECHA DE CIERRE OT"

# Día del mes en que se envía el reporte (se ajusta al día hábil siguiente si
# el día 20 cae en fin de semana o feriado, ver dia_envio_efectivo).
DIA_CORTE_MENSUAL = 20

# Campos personalizados que asignan personas a una petición, y la etiqueta con
# la que se muestran en el correo. Una misma persona puede figurar en más de
# un campo dentro de la misma petición (ej: Auditor 1 y Referente a la vez).
ROLES_AUDITORIA = {
    "AUDITOR 1": "Auditor 1",
    "AUDITOR 2": "Auditor 2",
    "AUX/JUNIOR": "Aux/Junior",
    "REFERENTE": "Referente",
    "CONTROL": "Control",
}

# Diccionario de respaldo (Fallback) por si la API de Redmine deniega el acceso a /users.json.
USUARIOS_FALLBACK_AUDITORIA = {
    # "ID": {"nombre": "Apellido, Nombre", "correo": "email@rivarossa.com"}
}

def _normalizar_texto(texto):
    """Quita tildes/diacríticos para poder matchear 'AUDITORÍA' y 'AUDITORIA' por igual."""
    return ''.join(c for c in unicodedata.normalize('NFKD', texto) if not unicodedata.combining(c))

def formatear_fecha(fecha_str):
    """Traduce el formato YYYY-MM-DD a texto legible en español"""
    try:
        fecha_obj = datetime.strptime(fecha_str, "%Y-%m-%d")
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        return f"{fecha_obj.day} de {meses[fecha_obj.month - 1]} de {fecha_obj.year}"
    except ValueError:
        return fecha_str

def es_dia_habil(fecha):
    """Lunes a viernes y que no esté en el calendario de feriados/no laborables de feriados.py"""
    return fecha.weekday() < 5 and fecha not in FERIADOS

def dia_envio_efectivo(anio, mes, dia_objetivo=DIA_CORTE_MENSUAL):
    """Devuelve la fecha efectiva de envío del reporte mensual: el día 20 del mes
    indicado, o el primer día hábil siguiente si el 20 cae en fin de semana o feriado."""
    fecha = date(anio, mes, dia_objetivo)
    while not es_dia_habil(fecha):
        fecha += timedelta(days=1)
    return fecha

def mes_siguiente(anio, mes):
    """Devuelve (año, mes) del mes calendario siguiente."""
    return (anio + 1, 1) if mes == 12 else (anio, mes + 1)

def _unir_roles(roles):
    """Combina los roles de una misma persona en una sola petición en un texto
    legible: ['Auditor 1'] -> 'Auditor 1'; ['Auditor 1','Referente'] -> 'Auditor 1 y Referente';
    ['A','B','C'] -> 'A, B y C'. No repite el registro por cada rol."""
    roles_unicos = list(dict.fromkeys(roles))
    if len(roles_unicos) == 1:
        return roles_unicos[0]
    return " y ".join([", ".join(roles_unicos[:-1]), roles_unicos[-1]])

def fetch_redmine_users(session, redmine_url, api_key):
    """Obtiene la lista de usuarios de Redmine para traducir el ID al Nombre real"""
    print("Sincronizando directorio de usuarios desde Redmine...")
    url = f"{redmine_url}users.json"
    headers = {"X-Redmine-API-Key": api_key}
    users_map = {}
    offset = 0
    limit = 100

    try:
        while True:
            response = session.get(url, headers=headers, params={"limit": limit, "offset": offset}, timeout=10)
            if response.status_code == 403:
                print("[WARN] Sin permisos para leer /users.json. Se utilizara USUARIOS_FALLBACK_AUDITORIA.")
                return USUARIOS_FALLBACK_AUDITORIA

            response.raise_for_status()
            data = response.json().get("users", [])
            if not data:
                break

            for u in data:
                nombre_completo = f"{u.get('lastname', '')}, {u.get('firstname', '')}".strip(" ,")
                users_map[str(u.get("id"))] = {
                    "nombre": nombre_completo,
                    "correo": u.get("mail", "")
                }
            offset += limit

        print(f"[OK] Directorio sincronizado: {len(users_map)} usuarios encontrados.")
        return users_map
    except Exception as e:
        print(f"[WARN] Error al sincronizar usuarios ({e}). Se utilizara USUARIOS_FALLBACK_AUDITORIA.")
        return USUARIOS_FALLBACK_AUDITORIA

def fetch_redmine_issues(session, redmine_url, api_key):
    """Descarga todas las peticiones abiertas usando una sesión persistente"""
    print("Descargando padrón de peticiones abiertas...")
    url = f"{redmine_url}issues.json"
    headers = {"X-Redmine-API-Key": api_key}
    all_issues = []
    offset = 0
    limit = 100

    while True:
        params = {"status_id": "open", "limit": limit, "offset": offset}
        try:
            response = session.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json().get("issues", [])
            if not data: break
            all_issues.extend(data)
            offset += limit
        except Exception as e:
            print(f"[ERROR] Error al descargar peticiones: {e}")
            break

    print(f"[OK] Peticiones descargadas: {len(all_issues)} en total.")
    return all_issues

def process_redmine_auditoria(issues, hoy=None):
    """Filtra las peticiones de auditoría (CyA Balance / CyA Auditoria / CyA Corte,
    en estado Pendiente) cuya Fecha de cierre OT cae dentro de la ventana mensual
    actual (hoy hasta el próximo día de envío del mes siguiente, inclusive), y
    arma un único registro por persona por petición aunque ocupe varios roles.
    Devuelve: { persona_id: { tipo_peticion: { fecha_cierre_ot: [tareas] } } }"""
    if hoy is None:
        hoy = datetime.now().date()

    anio_sig, mes_sig = mes_siguiente(hoy.year, hoy.month)
    proximo_corte = dia_envio_efectivo(anio_sig, mes_sig)

    notificaciones = {}
    descartadas_sin_fecha = 0
    descartadas_fuera_de_rango = 0

    for issue in issues:
        proyecto = issue.get("project", {}).get("name", "")
        tipo = issue.get("tracker", {}).get("name", "")
        estado = issue.get("status", {}).get("name", "")
        asunto = issue.get("subject", "")

        es_proyecto_auditoria = "AUDITORIA" in _normalizar_texto(proyecto).upper()

        if tipo in TRACKERS_AUDITORIA and es_proyecto_auditoria and estado == ESTADO_FILTRO_AUDITORIA:
            campos = issue.get("custom_fields", [])

            fecha_cierre_ot = ""
            # persona_id -> lista de roles que ocupa en ESTA petición
            roles_por_persona = {}

            for c in campos:
                nombre = c.get("name", "").strip().upper()
                valor = str(c.get("value", "")).strip()

                if nombre == CAMPO_FECHA_CIERRE and valor:
                    fecha_cierre_ot = valor
                elif nombre in ROLES_AUDITORIA and valor:
                    roles_por_persona.setdefault(valor, []).append(ROLES_AUDITORIA[nombre])

            # --- Filtro por Fecha de cierre OT: sólo entra en el reporte del mes en que
            # cae, o antes si ya está vencida (sigue pendiente); si la fecha cae después
            # del próximo corte, se difiere al reporte de un mes más adelante. ---
            try:
                fecha_cierre_dt = datetime.strptime(fecha_cierre_ot, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                descartadas_sin_fecha += 1
                continue

            if fecha_cierre_dt > proximo_corte:
                descartadas_fuera_de_rango += 1
                continue

            if not roles_por_persona:
                continue

            empresa = proyecto.split(" / ")[0].strip()
            issue_id = issue.get("id")

            for persona_id, roles in roles_por_persona.items():
                tarea_datos = {
                    "empresa": empresa,
                    "asunto": asunto,
                    "rol": _unir_roles(roles),
                    "issue_id": issue_id,
                }

                if persona_id not in notificaciones:
                    notificaciones[persona_id] = {}
                if tipo not in notificaciones[persona_id]:
                    notificaciones[persona_id][tipo] = {}
                if fecha_cierre_ot not in notificaciones[persona_id][tipo]:
                    notificaciones[persona_id][tipo][fecha_cierre_ot] = []

                notificaciones[persona_id][tipo][fecha_cierre_ot].append(tarea_datos)

    print(f"Filtrado por Fecha de cierre OT: {descartadas_sin_fecha} sin fecha válida, "
          f"{descartadas_fuera_de_rango} fuera de la ventana mensual (próximo corte: {proximo_corte}).")
    return notificaciones

def armar_html_persona(nombre_real, datos_por_tipo, redmine_url):
    """Genera el cuerpo HTML del reporte de auditoría para una persona.
    datos_por_tipo: { tipo_peticion: { fecha_cierre_ot: [tareas_dict] } }
    Se muestra un bloque por tipo de petición y, dentro de cada uno, un
    subgrupo por Fecha de cierre OT con links a cada issue en Redmine."""
    nombre_pila = nombre_real.split(",")[1].strip() if "," in nombre_real else nombre_real

    html = f"""
    <p style="font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">
        <strong>Hola {nombre_pila},</strong><br>
        Te dejamos el listado de peticiones de auditoría con cierre próximo que requieren tu atención, agrupadas por tipo de petición.
    </p>
    """

    orden_tipos = TRACKERS_AUDITORIA
    tipos_ordenados = sorted(
        datos_por_tipo.keys(),
        key=lambda t: (orden_tipos.index(t) if t in orden_tipos else len(orden_tipos), t)
    )

    for tipo in tipos_ordenados:
        html += f"""
        <div style="margin-top: 30px; padding-top: 20px; border-top: 2px solid #A75296;">
            <h2 style="color: #A75296; font-size: 18px; margin: 0 0 15px 0; font-weight: bold;">
                {tipo}
            </h2>
        """

        claves_ordenadas = sorted(datos_por_tipo[tipo].keys())

        for fecha_raw in claves_ordenadas:
            fecha_linda = formatear_fecha(fecha_raw)

            html += f"""
            <div style="margin-bottom: 20px;">
                <h3 style="color: #555555; font-size: 14px; font-weight: bold; margin: 10px 0 8px 0;">
                    Fecha de cierre OT: <strong>{fecha_linda}</strong>
                </h3>
            """

            for tarea in datos_por_tipo[tipo][fecha_raw]:
                empresa = tarea.get("empresa", "")
                asunto = tarea.get("asunto", "")
                rol = tarea.get("rol", "")
                issue_id = tarea.get("issue_id")
                link = f"{redmine_url}issues/{issue_id}" if issue_id else "#"
                numero_peticion = f"<a href='{link}' style='color: #A75296; text-decoration: none; font-weight: bold; margin-right: 6px;'>#{issue_id}</a>" if issue_id else ""

                html += f"""
                <div style="margin-left: 15px; margin-bottom: 12px; padding: 10px 12px; background-color: #fafafa; border-left: 3px solid #A75296; border-radius: 3px;">
                    <p style="margin: 0 0 5px 0; font-size: 13px;">
                        {numero_peticion}<strong style="color: #333333;">{empresa}</strong> — {asunto}
                    </p>
                    <p style="margin: 0; font-size: 12px; color: #777777;">
                        <em>Rol: {rol}</em>
                    </p>
                </div>
                """

            html += "</div>"

        html += "</div>"

    return html

def enviar_correos_auditoria(notificaciones, users_map, smtp_server, smtp_port, smtp_user, smtp_pass, from_email, redmine_url):
    print("\nIniciando motor de envío de correos de auditoría...")
    correos_enviados = 0

    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)

            for persona_id, datos in notificaciones.items():

                if users_map and persona_id in users_map:
                    nombre_real = users_map[persona_id]["nombre"]
                    correo_real = users_map[persona_id]["correo"]
                else:
                    nombre_real = f"Usuario {persona_id}"
                    correo_real = CORREO_ADMIN_AUDITORIA

                if REDIRIGIR_A_ADMIN_AUDITORIA:
                    correo_destino = CORREO_ADMIN_AUDITORIA
                else:
                    correo_destino = EXCEPCIONES_DESTINO_AUDITORIA.get(nombre_real.strip().lower(), correo_real)

                cuerpo_html = armar_html_persona(nombre_real, datos, redmine_url)

                logo_html = f'<img src="{LOGO_BASE64}" alt="Estudio Rivarossa" style="max-width: 280px; height: auto; margin-bottom: 15px;">' if LOGO_BASE64 else ""

                plantilla_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light">
    <meta name="supported-color-schemes" content="light">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; margin: 0; padding: 0; }}
        .container {{ max-width: 900px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12); }}
        .header {{ background-color: #A75296; background: linear-gradient(135deg, #A75296 0%, #8B3D7C 100%); color: #ffffff !important; padding: 35px 20px; text-align: center; }}
        .logo-container {{ display: flex; justify-content: center; }}
        .logo-container img {{ filter: brightness(1.15) drop-shadow(0 2px 4px rgba(0,0,0,0.1)); }}
        .content {{ padding: 35px 32px; }}
        .content p {{ font-size: 14px; line-height: 1.7; margin: 0 0 18px 0; }}
        .content strong {{ color: #A75296; }}
        .content em {{ color: #666; }}
        .section {{ margin-top: 28px; padding-top: 22px; border-top: 2px solid #e8e8e8; }}
        .section:first-child {{ margin-top: 0; padding-top: 0; border-top: none; }}
        .section-title {{ color: #A75296; font-size: 16px; font-weight: 600; margin: 0 0 18px 0; }}
        .task-item {{ margin-left: 12px; margin-bottom: 11px; padding: 9px 11px; background-color: #fafafa; border-left: 3px solid #A75296; border-radius: 3px; }}
        .task-item p {{ margin: 0 0 4px 0; font-size: 13px; line-height: 1.5; }}
        .task-item a {{ color: #A75296; text-decoration: none; font-weight: 600; }}
        .task-item a:hover {{ text-decoration: underline; }}
        .task-role {{ font-size: 12px; color: #888; font-style: italic; margin: 0; }}
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
            <p>Este reporte fue generado de manera automática. &copy; Estudio Rivarossa</p>
        </div>
    </div>
</body>
</html>"""

                asunto = f'Reporte de Auditoría: {nombre_real.upper()}'

                msg = EmailMessage()
                msg['Subject'] = asunto
                msg['From'] = from_email
                msg['To'] = correo_destino
                msg.set_content("Este correo requiere un cliente que soporte HTML.")
                msg.add_alternative(plantilla_html, subtype='html')

                try:
                    server.send_message(msg)
                    if REDIRIGIR_A_ADMIN_AUDITORIA:
                        etiqueta = "[REDIRIGIDO A ADMIN]"
                    elif nombre_real.strip().lower() in EXCEPCIONES_DESTINO_AUDITORIA:
                        etiqueta = "[EXCEPCIÓN -> ADMIN]"
                    else:
                        etiqueta = "[PRODUCCIÓN]"
                    print(f"[OK] {etiqueta} Correo procesado para: {nombre_real} -> Enviado a: {correo_destino}")
                    correos_enviados += 1
                except Exception as e:
                    print(f"[ERROR] Error al enviar correo a {nombre_real}: {e}")

    except Exception as e:
        print(f"Error crítico en el servidor SMTP: {e}")

if __name__ == "__main__":
    url = os.getenv("REDMINE_URL")
    redmine_key = os.getenv("REDMINE_API_KEY")

    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL")

    hoy = datetime.now().date()
    fecha_envio_mes_actual = dia_envio_efectivo(hoy.year, hoy.month)
    # Permite forzar el envío en una fecha distinta a la del corte mensual, para pruebas manuales.
    forzar_envio = os.getenv("FORZAR_ENVIO_AUDITORIA", "").strip() == "1"

    if hoy != fecha_envio_mes_actual and not forzar_envio:
        print(f"[INFO] Hoy ({hoy}) no es el día de envío del reporte mensual de auditoría "
              f"(corresponde el {fecha_envio_mes_actual}). No se envían correos.")
    else:
        with requests.Session() as session:
            mapa_usuarios = fetch_redmine_users(session, url, redmine_key)
            peticiones = fetch_redmine_issues(session, url, redmine_key)

        if peticiones:
            notificaciones_por_persona = process_redmine_auditoria(peticiones, hoy)

            if notificaciones_por_persona:
                enviar_correos_auditoria(
                    notificaciones_por_persona,
                    mapa_usuarios,
                    smtp_server,
                    smtp_port,
                    smtp_user,
                    smtp_pass,
                    from_email,
                    url
                )
            else:
                print("El sistema no detectó peticiones de auditoría pendientes con cierre dentro de la ventana mensual.")
