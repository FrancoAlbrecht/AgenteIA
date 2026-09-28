import os
import unicodedata
import requests
import smtplib
import base64
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from dotenv import load_dotenv
from feriados import FERIADOS

load_dotenv()

def obtener_access_token_oauth(client_id, client_secret, refresh_token):
    """Cambia el refresh token de Google por un access token de corta duración para
    autenticar el SMTP vía OAuth2 (XOAUTH2). Mismo mecanismo que impuestos y
    auditoría (ver CLAUDE.md)."""
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
    """Autentica una conexión SMTP ya abierta (después de starttls) usando XOAUTH2."""
    cadena_auth = f"user={smtp_user}\x01auth=Bearer {access_token}\x01\x01"
    server.auth("XOAUTH2", lambda challenge=None: cadena_auth)

def cargar_logo_base64():
    """Carga la imagen del logo y la convierte a base64 para incrustarla en el HTML"""
    ruta_logo = os.path.join(os.path.dirname(__file__), "images", "LogoBlanco.png")
    try:
        with open(ruta_logo, "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"
    except Exception as e:
        print(f"[WARN] No se pudo cargar el logo: {e}")
        return None

LOGO_BASE64 = cargar_logo_base64()

# ==========================================
# CONFIGURACIÓN GENERAL DEL AGENTE LABORAL
# ==========================================
# Modo Prueba: si está en True, TODOS los correos se redirigen a CORREO_ADMIN_LABORAL
# (con los nombres y datos reales de cada destinatario en el cuerpo). Recién pasar a
# False cuando se valide el reporte con el sector laboral.
REDIRIGIR_A_ADMIN_LABORAL = True
CORREO_ADMIN_LABORAL = "francoalbrecht@rivarossa.com"

# Personas cuyo correo, aun en producción, debe seguir llegando a admin.
EXCEPCIONES_DESTINO = {
    "previotto, gisela": CORREO_ADMIN_LABORAL,
    "gorreta, valentina": CORREO_ADMIN_LABORAL,
}

# Personas que no deben recibir este reporte aunque Redmine los tenga asignados.
IDS_EXCLUIDOS = {
    "792",  # Caravario, Darién - ya no es empleado (2026-09)
}

# Campos de rol de las peticiones laborales (distintos a los de impuestos).
CAMPOS_ROL = {"LIQUIDADOR 1": "Liquidador", "CONTROL 1": "Control", "SOPORTE 1": "Soporte"}

# Motor de reglas: cada tracker define cuándo se dispara su aviso. El orden del
# diccionario es el orden de los bloques en el correo.
#  - "dias_habiles_antes": N días hábiles antes del "Vencimiento DD. JJ." de la petición.
#  - "ultimo_habil_del_mes": el último día hábil de cada mes se avisa lo que vence
#    durante el mes siguiente.
# "asunto" (opcional) exige además que el asunto de la petición lo contenga.
# Pendientes de agregar: Laboral - 814, SICORE, SIRADIG, Asiento sueldo, Provisión vacaciones.
REGLAS_LABORAL = {
    "Laboral - Formulario 931": {"regla": "dias_habiles_antes", "dias": 2,
                                 "asunto": "leyes sociales", "titulo": "Leyes Sociales (F.931)"},
    "Laboral - Doméstica": {"regla": "ultimo_habil_del_mes", "titulo": "Domésticas"},
}

USUARIOS_FALLBACK = {
    "579": {"nombre": "Sandoval, Vanina", "correo": "vsandoval@rivarossa.com"},
}

def _normalizar_texto(texto):
    """Minúsculas y sin tildes, para comparar nombres de tracker/asunto sin depender
    de cómo se cargaron en Redmine (ej. "Domestica" vs "Doméstica")."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().lower()

REGLAS_NORMALIZADAS = {_normalizar_texto(k): k for k in REGLAS_LABORAL}

def formatear_fecha(fecha_str):
    """Traduce el formato YYYY-MM-DD a texto legible en español"""
    try:
        fecha_obj = datetime.strptime(fecha_str, "%Y-%m-%d")
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        return f"{fecha_obj.day} de {meses[fecha_obj.month - 1]} de {fecha_obj.year}"
    except (ValueError, TypeError):
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

def mes_siguiente(anio, mes):
    return (anio + 1, 1) if mes == 12 else (anio, mes + 1)

def ultimo_dia_habil_del_mes(anio, mes):
    anio_sig, mes_sig = mes_siguiente(anio, mes)
    actual = date(anio_sig, mes_sig, 1) - timedelta(days=1)
    while not es_dia_habil(actual):
        actual -= timedelta(days=1)
    return actual

def _parsear_fecha(fecha_str):
    try:
        return datetime.strptime(fecha_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

def corresponde_avisar(config, fecha_venc, hoy):
    """Decide si una petición con vencimiento fecha_venc se avisa hoy según la regla de su tracker."""
    if config["regla"] == "dias_habiles_antes":
        return restar_dias_habiles(fecha_venc, config["dias"]) == hoy
    if config["regla"] == "ultimo_habil_del_mes":
        if hoy != ultimo_dia_habil_del_mes(hoy.year, hoy.month):
            return False
        return (fecha_venc.year, fecha_venc.month) == mes_siguiente(hoy.year, hoy.month)
    return False

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
                nombre_completo = f"{u.get('lastname', '')}, {u.get('firstname', '')}".strip(" ,")
                users_map[str(u.get("id"))] = {"nombre": nombre_completo, "correo": u.get("mail", "")}
            offset += limit
        print(f"[OK] Directorio sincronizado: {len(users_map)} usuarios encontrados.")
        return users_map
    except Exception as e:
        print(f"[WARN] Error al sincronizar usuarios ({e}). Se utilizara USUARIOS_FALLBACK.")
        return USUARIOS_FALLBACK

def fetch_redmine_issues(session, redmine_url, api_key):
    """Descarga todas las peticiones abiertas. A diferencia de impuestos, un error a
    mitad de la descarga se relanza: mandar un reporte con datos parciales sería peor
    que no mandarlo (el resumen de ejecución avisa el error)."""
    print("Descargando padrón de peticiones abiertas...")
    url = f"{redmine_url}issues.json"
    headers = {"X-Redmine-API-Key": api_key}
    all_issues = []
    offset = 0
    limit = 100
    while True:
        params = {"status_id": "open", "limit": limit, "offset": offset}
        response = session.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json().get("issues", [])
        if not data:
            break
        all_issues.extend(data)
        offset += limit
    print(f"[OK] Peticiones descargadas: {len(all_issues)} en total.")
    return all_issues

def process_redmine_laboral(issues, hoy):
    """Filtra las peticiones laborales en estado Pendiente que corresponde avisar hoy
    según REGLAS_LABORAL y las agrupa por persona.
    Devuelve: ({ persona_id: { tracker: { (periodo, vencimiento): [tareas] } } },
               [peticiones que tocaban hoy pero no tienen a nadie asignado])"""
    notificaciones = {}
    sin_responsables = []
    descartadas_sin_fecha = 0

    for issue in issues:
        tracker = REGLAS_NORMALIZADAS.get(_normalizar_texto(issue.get("tracker", {}).get("name", "")))
        if tracker is None or issue.get("status", {}).get("name", "") != "Pendiente":
            continue
        config = REGLAS_LABORAL[tracker]
        asunto = issue.get("subject", "")
        if config.get("asunto") and config["asunto"] not in _normalizar_texto(asunto):
            continue

        fecha_vencimiento = ""
        periodo = ""
        roles_involucrados = {}
        for c in issue.get("custom_fields", []):
            nombre = c.get("name", "").strip().upper()
            valor_raw = c.get("value")
            valor = str(valor_raw).strip() if valor_raw not in (None, "") else ""
            if not valor:
                continue
            if nombre == "VENCIMIENTO DD. JJ.":
                fecha_vencimiento = valor
            elif "PERÍODO" in nombre or "PERIODO" in nombre:
                periodo = valor
            elif nombre in CAMPOS_ROL and valor not in IDS_EXCLUIDOS:
                # Una misma persona puede figurar en más de un rol de la misma petición.
                roles_involucrados.setdefault(valor, []).append(CAMPOS_ROL[nombre])

        fecha_venc = _parsear_fecha(fecha_vencimiento)
        if fecha_venc is None:
            descartadas_sin_fecha += 1
            continue
        if not corresponde_avisar(config, fecha_venc, hoy):
            continue

        empresa = issue.get("project", {}).get("name", "").split(" / ")[0].strip()
        tarea_base = {"empresa": empresa, "asunto": asunto, "issue_id": issue.get("id")}

        if not roles_involucrados:
            sin_responsables.append({**tarea_base, "tracker": tracker, "vencimiento": fecha_vencimiento})
            continue

        for persona_id, roles in roles_involucrados.items():
            grupo = (notificaciones.setdefault(persona_id, {})
                     .setdefault(tracker, {})
                     .setdefault((periodo, fecha_vencimiento), []))
            grupo.append({**tarea_base, "rol": " / ".join(roles)})

    print(f"Filtrado laboral: {descartadas_sin_fecha} pendientes sin vencimiento cargado, "
          f"{len(sin_responsables)} a avisar hoy pero sin responsables asignados.")
    return notificaciones, sin_responsables

def armar_html_persona(nombre_real, datos_por_tipo, redmine_url, correo_real=None):
    """Cuerpo del correo: un bloque por tipo de petición y, dentro, un subgrupo por
    período/vencimiento con links a cada petición en Redmine."""
    nombre_pila = nombre_real.split(",")[1].strip() if "," in nombre_real else nombre_real

    html = ""
    if REDIRIGIR_A_ADMIN_LABORAL:
        html += f"""
    <p style="font-size: 12px; margin: 0 0 20px 0; padding: 8px 12px; background-color: #fff4d6; border: 1px solid #e6c65c; border-radius: 3px; color: #7a5b00;">
        MODO PRUEBA — en producción este correo le llegaría a {nombre_real} ({correo_real or 'sin correo en Redmine'}).
    </p>
    """
    html += f"""
    <p style="font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">
        <strong>Hola {nombre_pila},</strong><br>
        Te dejamos el listado de vencimientos laborales próximos que requieren tu atención, agrupados por tipo de petición.
    </p>
    """

    orden_tipos = list(REGLAS_LABORAL.keys())
    for tipo in sorted(datos_por_tipo, key=orden_tipos.index):
        html += f"""
        <div style="margin-top: 30px; padding-top: 20px; border-top: 2px solid #A75296;">
            <h2 style="color: #A75296; font-size: 18px; margin: 0 0 15px 0; font-weight: bold;">
                {REGLAS_LABORAL[tipo]["titulo"]}
            </h2>
        """
        for periodo, fecha_raw in sorted(datos_por_tipo[tipo], key=lambda x: (x[1], x[0])):
            html += f"""
            <div style="margin-bottom: 20px;">
                <h3 style="color: #555555; font-size: 14px; font-weight: bold; margin: 10px 0 8px 0;">
                    Período: <strong>{periodo}</strong> | Vencimiento: <strong>{formatear_fecha(fecha_raw)}</strong>
                </h3>
            """
            tareas = sorted(datos_por_tipo[tipo][(periodo, fecha_raw)], key=lambda t: t["empresa"])
            for tarea in tareas:
                issue_id = tarea["issue_id"]
                link = f"{redmine_url}issues/{issue_id}"
                html += f"""
                <div style="margin-left: 15px; margin-bottom: 12px; padding: 10px 12px; background-color: #fafafa; border-left: 3px solid #A75296; border-radius: 3px;">
                    <p style="margin: 0 0 5px 0; font-size: 13px;">
                        <a href='{link}' style='color: #A75296; text-decoration: none; font-weight: bold; margin-right: 6px;'>#{issue_id}</a><strong style="color: #333333;">{tarea["empresa"]}</strong> — {tarea["asunto"]}
                    </p>
                    <p style="margin: 0; font-size: 12px; color: #777777;">
                        <em>Rol: {tarea["rol"]}</em>
                    </p>
                </div>
                """
            html += "</div>"
        html += "</div>"
    return html

def armar_plantilla(cuerpo_html):
    logo_html = f'<img src="{LOGO_BASE64}" alt="Estudio Rivarossa" style="max-width: 280px; height: auto; margin-bottom: 15px;">' if LOGO_BASE64 else ""
    return f"""<!DOCTYPE html>
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
        .content strong {{ color: #A75296; }}
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

def preparar_correos(notificaciones, users_map, redmine_url):
    """Arma la lista de correos a mandar: [(nombre_real, correo_destino, etiqueta, asunto, html)]"""
    correos = []
    for persona_id, datos in notificaciones.items():
        if users_map and persona_id in users_map:
            nombre_real = users_map[persona_id]["nombre"]
            correo_real = users_map[persona_id]["correo"]
        else:
            nombre_real = f"Usuario {persona_id}"
            correo_real = CORREO_ADMIN_LABORAL

        if REDIRIGIR_A_ADMIN_LABORAL:
            correo_destino, etiqueta = CORREO_ADMIN_LABORAL, "[REDIRIGIDO A ADMIN]"
        elif nombre_real.strip().lower() in EXCEPCIONES_DESTINO:
            correo_destino, etiqueta = EXCEPCIONES_DESTINO[nombre_real.strip().lower()], "[EXCEPCIÓN -> ADMIN]"
        else:
            correo_destino, etiqueta = correo_real, "[PRODUCCIÓN]"

        html = armar_plantilla(armar_html_persona(nombre_real, datos, redmine_url, correo_real))
        asunto = f"Vencimientos Laborales: {nombre_real.upper()}"
        correos.append((nombre_real, correo_destino, etiqueta, asunto, html))
    return correos

def enviar_correos(correos, smtp_server, smtp_port, smtp_user, access_token, from_email):
    print("\nIniciando motor de envío de correos...")
    correos_enviados = 0
    with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
        server.starttls()
        autenticar_smtp_oauth(server, smtp_user, access_token)
        for nombre_real, correo_destino, etiqueta, asunto, html in correos:
            msg = EmailMessage()
            msg['Subject'] = asunto
            msg['From'] = from_email
            msg['To'] = correo_destino
            msg.set_content("Este correo requiere un cliente que soporte HTML.")
            msg.add_alternative(html, subtype='html')
            try:
                server.send_message(msg)
                print(f"[OK] {etiqueta} Correo procesado para: {nombre_real} -> Enviado a: {correo_destino}")
                correos_enviados += 1
            except Exception as e:
                print(f"[ERROR] Error al enviar correo a {nombre_real}: {e}")
    if correos and correos_enviados == 0:
        raise RuntimeError("Había notificaciones pendientes pero no se pudo enviar ningún correo "
                           "(ver errores individuales arriba).")
    return correos_enviados

def enviar_resumen_ejecucion(estado, detalle_lineas, smtp_server, smtp_port, smtp_user, access_token, from_email):
    """Correo de estado a admin, se manda siempre (éxito o error) para que un fallo no sea silencioso."""
    destino = CORREO_ADMIN_LABORAL
    msg = EmailMessage()
    msg['Subject'] = f"[Agente Laboral] Ejecución {estado} - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    msg['From'] = from_email
    msg['To'] = destino
    msg.set_content("\n".join(detalle_lineas))
    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            autenticar_smtp_oauth(server, smtp_user, access_token)
            server.send_message(msg)
        print(f"[OK] Resumen de ejecución enviado a {destino}")
    except Exception as e:
        print(f"[ERROR] No se pudo enviar el resumen de ejecución a {destino}: {e}")

def fecha_de_hoy():
    """FECHA_SIMULADA=YYYY-MM-DD permite probar localmente cómo saldría el reporte otro día."""
    simulada = os.getenv("FECHA_SIMULADA")
    return datetime.strptime(simulada, "%Y-%m-%d").date() if simulada else datetime.now().date()

if __name__ == "__main__":
    # SOLO_VISTA_PREVIA=1: no manda nada, guarda los HTML en ./vista_previa_laboral/ para revisarlos.
    solo_vista_previa = os.getenv("SOLO_VISTA_PREVIA") == "1"
    hoy = fecha_de_hoy()
    print(f"Fecha de proceso: {hoy.isoformat()}" + (" (simulada)" if os.getenv("FECHA_SIMULADA") else ""))

    url = os.getenv("REDMINE_URL")
    if url and not url.endswith("/"):
        url += "/"
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
    correos = []
    sin_responsables = []
    correos_enviados = 0
    access_token = None

    try:
        with requests.Session() as session:
            mapa_usuarios = fetch_redmine_users(session, url, redmine_key)
            peticiones = fetch_redmine_issues(session, url, redmine_key)
        total_peticiones = len(peticiones)

        notificaciones, sin_responsables = process_redmine_laboral(peticiones, hoy)
        correos = preparar_correos(notificaciones, mapa_usuarios, url)

        if solo_vista_previa:
            carpeta = os.path.join(os.path.dirname(__file__), "vista_previa_laboral")
            os.makedirs(carpeta, exist_ok=True)
            for nombre_real, correo_destino, etiqueta, asunto, html in correos:
                archivo = os.path.join(carpeta, f"{hoy.isoformat()}_{_normalizar_texto(nombre_real).replace(', ', '_').replace(' ', '_')}.html")
                with open(archivo, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"[VISTA PREVIA] {etiqueta} {nombre_real} -> {correo_destino} | {archivo}")
        elif correos:
            access_token = obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)
            correos_enviados = enviar_correos(correos, smtp_server, smtp_port, smtp_user, access_token, from_email)
        else:
            print("No hay vencimientos laborales para avisar hoy.")

    except Exception as e:
        estado = "CON ERROR"
        error_texto = str(e)
        print(f"[ERROR] Ejecución interrumpida: {e}")

    finally:
        detalle = [
            f"Fecha de proceso: {hoy.isoformat()}",
            f"Modo prueba (todo a admin): {'SÍ' if REDIRIGIR_A_ADMIN_LABORAL else 'NO'}",
            f"Peticiones descargadas de Redmine: {total_peticiones}",
            f"Personas con vencimientos a notificar: {len(correos)}",
            f"Correos enviados con éxito: {correos_enviados}",
        ]
        if sin_responsables:
            detalle += ["", "Peticiones que tocaba avisar hoy pero no tienen Liquidador/Control/Soporte asignado:"]
            detalle += [f"  #{t['issue_id']} {t['empresa']} — {t['asunto']} (vence {t['vencimiento']})" for t in sin_responsables]
        if error_texto:
            detalle += ["", f"Error: {error_texto}"]

        if solo_vista_previa:
            print("\n--- Resumen (no enviado, vista previa) ---\n" + "\n".join(detalle))
        else:
            if access_token is None:
                try:
                    access_token = obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)
                except Exception as e:
                    print(f"[ERROR] No se pudo obtener un access token para mandar el resumen: {e}")
            if access_token:
                enviar_resumen_ejecucion(estado, detalle, smtp_server, smtp_port, smtp_user, access_token, from_email)

    if estado == "CON ERROR":
        raise SystemExit(1)
