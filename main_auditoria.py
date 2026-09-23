import os
import requests
import smtplib
import base64
import unicodedata
from datetime import datetime, date
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

def obtener_access_token_oauth(client_id, client_secret, refresh_token):
    """Cambia el refresh token de Google por un access token de corta duración para
    autenticar el SMTP vía OAuth2 (XOAUTH2), en vez de usuario+contraseña de
    aplicación (mismo cambio aplicado a main_impuestos.py tras el incidente del
    15/09, donde Google bloqueaba el login con contraseña desde las IPs
    rotativas de GitHub Actions por considerarlo sospechoso)."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def autenticar_smtp_oauth(server, smtp_user, access_token):
    """Autentica una conexión SMTP ya abierta (después de starttls) usando el
    mecanismo XOAUTH2, con el access_token obtenido de obtener_access_token_oauth."""
    cadena_auth = f"user={smtp_user}\x01auth=Bearer {access_token}\x01\x01"
    server.auth("XOAUTH2", lambda challenge=None: cadena_auth)

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
# En producción desde el 24/09/2026.
REDIRIGIR_A_ADMIN_AUDITORIA = False
CORREO_ADMIN_AUDITORIA = "francoalbrecht@rivarossa.com"

# Excepciones puntuales: personas cuyo correo, aun en producción, debe seguir
# llegando a CORREO_ADMIN_AUDITORIA en lugar de a su casilla real.
EXCEPCIONES_DESTINO_AUDITORIA = {}

# Trackers (tipos de petición) del sector auditoría que este reporte cubre.
# El orden de esta lista es también el orden en que aparecen los bloques en el correo.
TRACKERS_AUDITORIA = ["CyA Balance", "CyA Corte", "CyA Auditoria"]

# Estado que deben tener las peticiones para ser incluidas.
ESTADO_FILTRO_AUDITORIA = "Pendiente"

# Nombre del campo personalizado que define la fecha de cierre de la OT.
CAMPO_FECHA_CIERRE = "FECHA DE CIERRE OT"

# Día del mes en que se envía el reporte. Se manda ese día sea hábil o no
# (fin de semana y feriados incluidos).
DIA_CORTE_MENSUAL = 21

# Envíos puntuales fuera del día de corte mensual. Tras la fecha pasan a ser
# inofensivas (nunca vuelven a coincidir con hoy).
FECHAS_ENVIO_EXTRA = {
    date(2026, 9, 24),  # primer envío en producción, fuera del corte mensual
}

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

# Personas que no deben recibir este reporte aunque Redmine los tenga asignados
# en algún campo de rol (ej. bajas de personal). No modifica nada en Redmine,
# sólo las excluye de este correo.
IDS_EXCLUIDOS_AUDITORIA = {
    "792",  # Caravario, Darién - ya no es empleado (2026-09)
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

def dia_envio_efectivo(anio, mes, dia_objetivo=DIA_CORTE_MENSUAL):
    """Devuelve la fecha de envío del reporte mensual: el día de corte del mes
    indicado, sin correrlo aunque caiga en fin de semana o feriado."""
    return date(anio, mes, dia_objetivo)

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
    Las peticiones con Fecha de cierre OT anterior a hoy quedan fuera: ya
    tuvieron su oportunidad de aparecer en un reporte anterior.
    Devuelve: { persona_id: { tipo_peticion: { fecha_cierre_ot: [tareas] } } }"""
    if hoy is None:
        hoy = datetime.now().date()

    anio_sig, mes_sig = mes_siguiente(hoy.year, hoy.month)
    proximo_corte = dia_envio_efectivo(anio_sig, mes_sig)

    notificaciones = {}
    descartadas_sin_fecha = 0
    descartadas_vencidas = 0
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
                valor_raw = c.get("value")
                valor = str(valor_raw).strip() if valor_raw not in (None, "") else ""

                if nombre == CAMPO_FECHA_CIERRE and valor:
                    fecha_cierre_ot = valor
                elif nombre in ROLES_AUDITORIA and valor and valor not in IDS_EXCLUIDOS_AUDITORIA:
                    roles_por_persona.setdefault(valor, []).append(ROLES_AUDITORIA[nombre])

            # --- Filtro por Fecha de cierre OT: sólo entra en el reporte si cae de hoy
            # en adelante y hasta el próximo corte mensual (inclusive). Lo que ya venció
            # queda afuera: se asume que ya apareció en el reporte del mes que le
            # correspondía, y no se repite en los siguientes. ---
            try:
                fecha_cierre_dt = datetime.strptime(fecha_cierre_ot, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                descartadas_sin_fecha += 1
                continue

            if fecha_cierre_dt < hoy:
                descartadas_vencidas += 1
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
          f"{descartadas_vencidas} ya vencidas (antes de hoy, {hoy}), "
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

def enviar_correos_auditoria(notificaciones, users_map, smtp_server, smtp_port, smtp_user, access_token, from_email, redmine_url):
    print("\nIniciando motor de envío de correos de auditoría...")
    correos_enviados = 0

    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            autenticar_smtp_oauth(server, smtp_user, access_token)

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

            # Si había gente para notificar y ni un solo correo salió, algo está
            # mal (ej. todos los destinatarios rebotan) aunque el login SMTP haya
            # funcionado. Lo tratamos como fallo crítico para que el job no quede
            # en verde con cero correos enviados.
            if notificaciones and correos_enviados == 0:
                raise RuntimeError(
                    "Había notificaciones pendientes pero no se pudo enviar ningún correo "
                    "(ver errores individuales arriba)."
                )

    except Exception as e:
        print(f"Error crítico en el servidor SMTP: {e}")
        raise

    return correos_enviados

def enviar_resumen_ejecucion(estado, detalle_lineas, smtp_server, smtp_port, smtp_user, access_token, from_email):
    """Envía un correo de estado a francoalbrecht@rivarossa.com informando si la
    ejecución del reporte mensual de auditoría fue exitosa o no. Se intenta
    siempre, haya terminado bien o mal el resto del script, para que un fallo
    silencioso (ej. credenciales SMTP vencidas) se note en vez de descubrirse
    por accidente, igual que en main_impuestos.py."""
    destino = "francoalbrecht@rivarossa.com"
    asunto = f"[Agente Auditoría] Ejecución {estado} - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    cuerpo = "\n".join(detalle_lineas)

    msg = EmailMessage()
    msg['Subject'] = asunto
    msg['From'] = from_email
    msg['To'] = destino
    msg.set_content(cuerpo)

    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            autenticar_smtp_oauth(server, smtp_user, access_token)
            server.send_message(msg)
        print(f"[OK] Resumen de ejecución enviado a {destino}")
    except Exception as e:
        # No relanzamos: si esto falla (ej. porque justo el SMTP está roto,
        # el mismo problema que estamos reportando) no queremos tapar el
        # error original ni romper el resto del cierre del script.
        print(f"[ERROR] No se pudo enviar el resumen de ejecución a {destino}: {e}")

if __name__ == "__main__":
    url = os.getenv("REDMINE_URL")
    redmine_key = os.getenv("REDMINE_API_KEY")

    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USERNAME")
    oauth_client_id = os.getenv("GMAIL_OAUTH_CLIENT_ID")
    oauth_client_secret = os.getenv("GMAIL_OAUTH_CLIENT_SECRET")
    oauth_refresh_token = os.getenv("GMAIL_OAUTH_REFRESH_TOKEN")
    from_email = os.getenv("SMTP_FROM_EMAIL")

    hoy = datetime.now().date()
    fecha_envio_mes_actual = dia_envio_efectivo(hoy.year, hoy.month)
    # Permite forzar el envío en una fecha distinta a la del corte mensual, para pruebas manuales.
    forzar_envio = os.getenv("FORZAR_ENVIO_AUDITORIA", "").strip() == "1"

    if hoy != fecha_envio_mes_actual and hoy not in FECHAS_ENVIO_EXTRA and not forzar_envio:
        print(f"[INFO] Hoy ({hoy}) no es el día de envío del reporte mensual de auditoría "
              f"(corresponde el {fecha_envio_mes_actual}). No se envían correos.")
    else:
        # Sólo en el día de envío (o al forzarlo) corremos con la misma red de
        # seguridad que main_impuestos.py: si algo falla, igual se manda un
        # resumen a francoalbrecht@rivarossa.com y el job queda en rojo.
        estado = "EXITOSA"
        error_texto = None
        total_peticiones = 0
        total_notificaciones = 0
        correos_enviados = 0
        access_token = None

        try:
            # Se pide un access token nuevo al toque: son de corta duración (~1h) y
            # alcanza y sobra para una corrida de unos pocos minutos.
            access_token = obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)

            with requests.Session() as session:
                mapa_usuarios = fetch_redmine_users(session, url, redmine_key)
                peticiones = fetch_redmine_issues(session, url, redmine_key)

            total_peticiones = len(peticiones)

            if peticiones:
                notificaciones_por_persona = process_redmine_auditoria(peticiones, hoy)
                total_notificaciones = len(notificaciones_por_persona)

                if notificaciones_por_persona:
                    correos_enviados = enviar_correos_auditoria(
                        notificaciones_por_persona,
                        mapa_usuarios,
                        smtp_server,
                        smtp_port,
                        smtp_user,
                        access_token,
                        from_email,
                        url
                    )
                else:
                    print("El sistema no detectó peticiones de auditoría pendientes con cierre dentro de la ventana mensual.")
            else:
                print("No se descargaron peticiones desde Redmine.")

        except Exception as e:
            estado = "CON ERROR"
            error_texto = str(e)
            print(f"[ERROR] Ejecución interrumpida: {e}")

        finally:
            detalle = [
                f"Peticiones descargadas de Redmine: {total_peticiones}",
                f"Personas con peticiones de auditoría a notificar: {total_notificaciones}",
                f"Correos enviados con éxito: {correos_enviados}",
            ]
            if error_texto:
                detalle += ["", f"Error: {error_texto}"]

            if access_token is None:
                # El error puede haber pasado antes de conseguir el token (ej. Redmine
                # caído); reintentamos una vez más acá para no perder el aviso.
                try:
                    access_token = obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)
                except Exception as e:
                    print(f"[ERROR] No se pudo obtener un access token para mandar el resumen: {e}")

            if access_token:
                enviar_resumen_ejecucion(estado, detalle, smtp_server, smtp_port, smtp_user, access_token, from_email)

        if estado == "CON ERROR":
            raise SystemExit(1)
