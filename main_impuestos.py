import os
import requests
import smtplib
import base64
from datetime import datetime, timedelta
from email.message import EmailMessage
from dotenv import load_dotenv
from feriados import FERIADOS

load_dotenv()

def obtener_access_token_oauth(client_id, client_secret, refresh_token):
    """Cambia el refresh token de Google por un access token de corta duración para
    autenticar el SMTP vía OAuth2 (XOAUTH2), en vez de usuario+contraseña de
    aplicación. Se hizo el cambio porque Google trataba el login con contraseña
    desde las IPs rotativas de GitHub Actions como sospechoso y lo bloqueaba
    (WebLoginRequired, incidente del 15/09) — OAuth2 es el método que Google
    recomienda para automatizaciones justamente porque no dispara ese chequeo."""
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
# CONFIGURACIÓN GENERAL DEL AGENTE
# ==========================================
# Modo Prueba: si está en True, TODOS los correos se redirigen a CORREO_ADMIN
# (con los nombres y datos reales de cada destinatario en el cuerpo). En
# producción debe estar en False para que cada persona reciba su propio correo.
REDIRIGIR_A_ADMIN = False
CORREO_ADMIN = "francoalbrecht@rivarossa.com"

# Excepciones puntuales: personas cuyo correo, aun en producción, debe seguir
# llegando a CORREO_ADMIN en lugar de a su casilla real. Se matchea contra el
# nombre real ("Apellido, Nombre") de forma flexible (sin importar mayúsculas).
EXCEPCIONES_DESTINO = {
    "previotto, gisela": CORREO_ADMIN,
    # vgorreta@rivarossa.com no existe / rebota ("no se ha encontrado la
    # dirección"); mientras no se confirme la casilla real, se redirige a admin.
    "gorreta, valentina": CORREO_ADMIN,
}

# Motor de reglas de notificación: cada tracker (tipo de petición) define con
# cuántos días HÁBILES de anticipación se dispara su correo de aviso. Se consideran
# hábiles los días de lunes a viernes que no figuren en FERIADOS (ver feriados.py).
# Ej: "Tributaria - II BB": 2 -> vencimiento el lunes 19, el correo sale el jueves 15
# (2 días hábiles antes, saltando el fin de semana y cualquier feriado intermedio).
REGLAS_NOTIFICACION = {
    "Tributaria - II BB": 2,
    "Tributaria - DREI": 2,
    "Tributaria - CM": 2,
    "Tributaria - IVA": 2,
    "Tributaria - Sicore": 2,
    "Tributaria - Ag. Recaudación": 2,
    # "Laboral": 5,
}

# Diccionario de respaldo (Fallback) por si la API de Redmine deniega el acceso a /users.json.
# Si la auto-sincronización falla, podés cargar los IDs manualmente acá.
USUARIOS_FALLBACK = {
    "579": {"nombre": "Sandoval, Vanina", "correo": "vsandoval@rivarossa.com"},
    # "ID": {"nombre": "Apellido, Nombre", "correo": "email@rivarossa.com"}
}

# Personas que no deben recibir este reporte aunque Redmine los tenga asignados
# en algún campo de rol (ej. bajas de personal). No modifica nada en Redmine,
# sólo las excluye de este correo.
IDS_EXCLUIDOS = {
    "792",  # Caravario, Darién - ya no es empleado (2026-09)
}

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

def restar_dias_habiles(fecha, dias_habiles):
    """Resta N días hábiles (lunes a viernes, excluyendo feriados) a una fecha."""
    actual = fecha
    restantes = dias_habiles
    while restantes > 0:
        actual -= timedelta(days=1)
        if es_dia_habil(actual):
            restantes -= 1
    return actual

def calcular_fecha_notificacion(fecha_vencimiento_str, dias_habiles_anticipacion):
    """Devuelve la fecha en la que corresponde notificar (vencimiento menos N días
    hábiles), o None si la fecha de vencimiento no es parseable."""
    try:
        fecha_venc = datetime.strptime(fecha_vencimiento_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return restar_dias_habiles(fecha_venc, dias_habiles_anticipacion)

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
                print("[WARN] Sin permisos para leer /users.json. Se utilizara USUARIOS_FALLBACK.")
                return USUARIOS_FALLBACK
            
            response.raise_for_status()
            data = response.json().get("users", [])
            if not data:
                break
                
            for u in data:
                # Redmine envía firstname y lastname por separado
                nombre_completo = f"{u.get('lastname', '')}, {u.get('firstname', '')}".strip(" ,")
                users_map[str(u.get("id"))] = {
                    "nombre": nombre_completo,
                    "correo": u.get("mail", "")
                }
            offset += limit
            
        print(f"[OK] Directorio sincronizado: {len(users_map)} usuarios encontrados.")
        return users_map
    except Exception as e:
        print(f"[WARN] Error al sincronizar usuarios ({e}). Se utilizara USUARIOS_FALLBACK.")
        return USUARIOS_FALLBACK

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

def process_redmine_data(issues, hoy=None):
    """Filtra y agrupa las peticiones cuya fecha de notificación (vencimiento menos los
    días HÁBILES de anticipación definidos en REGLAS_NOTIFICACION para su tracker) es
    hoy, asignando la tarea y el rol exacto al ID del usuario.
    Devuelve: { persona_id: { tipo_peticion: { (periodo, vencimiento): [tareas] } } }
    de forma que cada persona reciba un único correo con todos sus vencimientos
    próximos, agrupados por tipo de petición (II BB, DREI, CM, IVA, etc)."""
    if hoy is None:
        hoy = datetime.now().date()

    notificaciones = {}
    descartadas_sin_fecha = 0
    descartadas_fuera_de_rango = 0

    for issue in issues:
        proyecto = issue.get("project", {}).get("name", "")
        tipo = issue.get("tracker", {}).get("name", "")
        estado = issue.get("status", {}).get("name", "")
        asunto = issue.get("subject", "")

        dias_anticipacion = REGLAS_NOTIFICACION.get(tipo)
        if "IMPUESTOS" in proyecto.upper() and dias_anticipacion is not None and estado == "Pendiente":
            campos = issue.get("custom_fields", [])
            
            fecha_vencimiento = "Sin Vencimiento"
            periodo = ""
            # Lista para guardar qué ID tiene qué Rol en esta tarea
            roles_involucrados = [] 
            
            for c in campos:
                nombre = c.get("name", "").strip().upper()
                valor_raw = c.get("value")
                valor = str(valor_raw).strip() if valor_raw not in (None, "") else ""

                # Match exacto: algunos trackers (ej. IVA) tienen además un campo
                # "Vencimiento Pago" distinto, que no debe confundirse con el
                # vencimiento de la DDJJ que dispara el aviso.
                if nombre == "VENCIMIENTO DD. JJ." and valor:
                    fecha_vencimiento = valor
                elif ("PERÍODO" in nombre or "PERIODO" in nombre) and valor:
                    periodo = valor
                elif nombre in ["AUXILIAR", "LIQUIDADOR", "RESPONSABLE"] and valor and valor not in IDS_EXCLUIDOS:
                    # Guardamos el ID del usuario y el rol exacto que cumple
                    roles_involucrados.append({
                        "id": valor,
                        "rol": nombre.capitalize()
                    })
            
            # --- Filtro por fecha de vencimiento, en días HÁBILES (reemplaza al filtro por período) ---
            fecha_notificacion = calcular_fecha_notificacion(fecha_vencimiento, dias_anticipacion)
            if fecha_notificacion is None:
                descartadas_sin_fecha += 1
                continue
            if fecha_notificacion != hoy:
                descartadas_fuera_de_rango += 1
                continue

            empresa = proyecto.split(" / ")[0].strip()
            issue_id = issue.get("id")

            # Asignamos la tarea a cada ID involucrado, con todos los datos necesarios para
            # armar el HTML (empresa, asunto, rol, ID de issue para el link)
            for involucrado in roles_involucrados:
                persona_id = involucrado["id"]
                rol_asignado = involucrado["rol"]

                tarea_datos = {
                    "empresa": empresa,
                    "asunto": asunto,
                    "rol": rol_asignado,
                    "issue_id": issue_id,
                }

                # Estructura: persona -> tipo de petición -> (periodo, vencimiento) -> [tareas]
                # Así, en el mismo correo, cada persona ve todos sus vencimientos próximos
                # agrupados y separados por tipo de petición (II BB, DREI, CM, IVA, etc).
                if persona_id not in notificaciones:
                    notificaciones[persona_id] = {}

                if tipo not in notificaciones[persona_id]:
                    notificaciones[persona_id][tipo] = {}

                clave_grupo = (periodo, fecha_vencimiento)
                if clave_grupo not in notificaciones[persona_id][tipo]:
                    notificaciones[persona_id][tipo][clave_grupo] = []

                notificaciones[persona_id][tipo][clave_grupo].append(tarea_datos)

    print(f"Filtrado por vencimiento: {descartadas_sin_fecha} sin fecha válida, "
          f"{descartadas_fuera_de_rango} fuera de la ventana definida en REGLAS_NOTIFICACION.")
    return notificaciones

def armar_html_persona(nombre_real, datos_por_tipo, redmine_url, logo_base64=None):
    """Genera el código HTML con estética mejorada, utilizando el nombre real de la persona.
    datos_por_tipo: { tipo_peticion: { (periodo, fecha_vencimiento): [tareas_dict] } }
    Se muestra un bloque por tipo de petición (II BB, DREI, CM, IVA, etc.) y, dentro
    de cada uno, un subgrupo por período/vencimiento con links a cada issue en Redmine."""
    nombre_pila = nombre_real.split(",")[1].strip() if "," in nombre_real else nombre_real

    html = f"""
    <p style="font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">
        <strong>Hola {nombre_pila},</strong><br>
        Te dejamos el listado de vencimientos próximos que requieren tu atención, agrupados por tipo de petición.
    </p>
    """

    orden_tipos = list(REGLAS_NOTIFICACION.keys())
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

        claves_ordenadas = sorted(datos_por_tipo[tipo].keys(), key=lambda x: (x[0], x[1]))

        for clave in claves_ordenadas:
            periodo, fecha_raw = clave
            fecha_linda = formatear_fecha(fecha_raw)

            html += f"""
            <div style="margin-bottom: 20px;">
                <h3 style="color: #555555; font-size: 14px; font-weight: bold; margin: 10px 0 8px 0;">
                    Período: <strong>{periodo}</strong> | Vencimiento: <strong>{fecha_linda}</strong>
                </h3>
            """

            for tarea in datos_por_tipo[tipo][clave]:
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

def enviar_correos_individuales(notificaciones, users_map, smtp_server, smtp_port, smtp_user, access_token, from_email, redmine_url):
    print("\nIniciando motor de envío de correos...")
    correos_enviados = 0


    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            autenticar_smtp_oauth(server, smtp_user, access_token)

            for persona_id, datos in notificaciones.items():

                # Traducir el ID (ej: "579") al nombre y correo real
                if users_map and persona_id in users_map:
                    nombre_real = users_map[persona_id]["nombre"]
                    correo_real = users_map[persona_id]["correo"]
                else:
                    nombre_real = f"Usuario {persona_id}"
                    correo_real = CORREO_ADMIN # Fallback por seguridad

                # Mientras REDIRIGIR_A_ADMIN esté activo, todo correo se manda a CORREO_ADMIN
                # en vez de al destinatario real (ver nota en la configuración general).
                # EXCEPCIONES_DESTINO tiene prioridad y aplica incluso en producción.
                if REDIRIGIR_A_ADMIN:
                    correo_destino = CORREO_ADMIN
                else:
                    correo_destino = EXCEPCIONES_DESTINO.get(nombre_real.strip().lower(), correo_real)

                cuerpo_html = armar_html_persona(nombre_real, datos, redmine_url, LOGO_BASE64)

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
                
                asunto = f'Reporte de Vencimientos: {nombre_real.upper()}'

                msg = EmailMessage()
                msg['Subject'] = asunto
                msg['From'] = from_email
                msg['To'] = correo_destino
                msg.set_content("Este correo requiere un cliente que soporte HTML.")
                msg.add_alternative(plantilla_html, subtype='html')
                
                try:
                    server.send_message(msg)
                    if REDIRIGIR_A_ADMIN:
                        etiqueta = "[REDIRIGIDO A ADMIN]"
                    elif nombre_real.strip().lower() in EXCEPCIONES_DESTINO:
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
    ejecución del aviso de vencimientos fue exitosa o no. Se intenta siempre,
    haya terminado bien o mal el resto del script, para que un fallo silencioso
    (ej. credenciales SMTP vencidas) se note en vez de descubrirse por accidente."""
    destino = "francoalbrecht@rivarossa.com"
    asunto = f"[Agente Impuestos] Ejecución {estado} - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
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

def ya_hubo_envio_exitoso_hoy():
    """Consulta la API de GitHub Actions para ver si ya hubo otra corrida exitosa
    de este mismo workflow hoy. Se usa para poder tener disparos de respaldo más
    tarde en la mañana (ver reporte-vencimientos.yml) sin duplicar los avisos si
    el disparo original ya salió bien. Fuera de GitHub Actions (ej. corrida local)
    no hay forma de chequear esto, así que se asume que no y se sigue de largo."""
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    run_id_actual = os.getenv("GITHUB_RUN_ID")
    if not repo or not token:
        return False
    try:
        url_api = f"https://api.github.com/repos/{repo}/actions/workflows/reporte-vencimientos.yml/runs"
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        hoy = datetime.now().date().isoformat()
        resp = requests.get(url_api, headers=headers, timeout=10,
                             params={"status": "success", "created": f">={hoy}", "per_page": 10})
        resp.raise_for_status()
        runs = resp.json().get("workflow_runs", [])
        return any(str(r.get("id")) != str(run_id_actual) for r in runs)
    except Exception as e:
        print(f"[WARN] No se pudo chequear corridas previas de hoy ({e}); se continúa igual.")
        return False

if __name__ == "__main__":
    if ya_hubo_envio_exitoso_hoy():
        print("Ya hubo una ejecución exitosa hoy — se omite este disparo (de respaldo) "
              "para no duplicar los avisos.")
        raise SystemExit(0)

    url = os.getenv("REDMINE_URL")
    redmine_key = os.getenv("REDMINE_API_KEY")
    
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USERNAME")
    oauth_client_id = os.getenv("GMAIL_OAUTH_CLIENT_ID")
    oauth_client_secret = os.getenv("GMAIL_OAUTH_CLIENT_SECRET")
    oauth_refresh_token = os.getenv("GMAIL_OAUTH_REFRESH_TOKEN")
    from_email = os.getenv("SMTP_FROM_EMAIL")

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

        # Abrimos una sesión HTTP persistente para darle mayor velocidad y robustez
        with requests.Session() as session:
            # 1. Sincronizamos las identidades de los usuarios
            mapa_usuarios = fetch_redmine_users(session, url, redmine_key)

            # 2. Descargamos las tareas
            peticiones = fetch_redmine_issues(session, url, redmine_key)

        total_peticiones = len(peticiones)

        if peticiones:
            # 3. Procesamos y vinculamos (Tarea <-> ID <-> Rol)
            notificaciones_por_persona = process_redmine_data(peticiones)
            total_notificaciones = len(notificaciones_por_persona)

            if notificaciones_por_persona:
                # 4. Disparamos la lógica de correos
                correos_enviados = enviar_correos_individuales(
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
                print("El sistema no detectó peticiones pendientes en los periodos filtrados.")
        else:
            print("No se descargaron peticiones desde Redmine.")

    except Exception as e:
        estado = "CON ERROR"
        error_texto = str(e)
        print(f"[ERROR] Ejecución interrumpida: {e}")

    finally:
        detalle = [
            f"Peticiones descargadas de Redmine: {total_peticiones}",
            f"Personas con vencimientos a notificar: {total_notificaciones}",
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