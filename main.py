import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from dotenv import load_dotenv
from feriados import FERIADOS

load_dotenv()

# ==========================================
# CONFIGURACIÓN GENERAL DEL AGENTE
# ==========================================
# Mientras la herramienta se termina de validar, TODOS los correos se redirigen
# a CORREO_ADMIN (con los nombres y datos reales de cada destinatario en el cuerpo).
# Cuando se dé por definitivo, cambiar REDIRIGIR_A_ADMIN a False para que cada
# persona reciba su propio correo.
REDIRIGIR_A_ADMIN = True
CORREO_ADMIN = "francoalbrecht@rivarossa.com"

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
                print("⚠️  Sin permisos para leer /users.json. Se utilizará USUARIOS_FALLBACK.")
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
            
        print(f"✓ Directorio sincronizado: {len(users_map)} usuarios encontrados.")
        return users_map
    except Exception as e:
        print(f"⚠️  Error al sincronizar usuarios ({e}). Se utilizará USUARIOS_FALLBACK.")
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
            print(f"✗ Error al descargar peticiones: {e}")
            break
            
    print(f"✓ Peticiones descargadas: {len(all_issues)} en total.")
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
                valor = str(c.get("value", "")).strip()

                # Match exacto: algunos trackers (ej. IVA) tienen además un campo
                # "Vencimiento Pago" distinto, que no debe confundirse con el
                # vencimiento de la DDJJ que dispara el aviso.
                if nombre == "VENCIMIENTO DD. JJ." and valor:
                    fecha_vencimiento = valor
                elif ("PERÍODO" in nombre or "PERIODO" in nombre) and valor:
                    periodo = valor
                elif nombre in ["AUXILIAR", "LIQUIDADOR", "RESPONSABLE"] and valor:
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

def armar_html_persona(nombre_real, datos_por_tipo, redmine_url):
    """Genera el código HTML con estética mejorada, utilizando el nombre real de la persona.
    datos_por_tipo: { tipo_peticion: { (periodo, fecha_vencimiento): [tareas_dict] } }
    Se muestra un bloque por tipo de petición (II BB, DREI, CM, IVA, etc.) y, dentro
    de cada uno, un subgrupo por período/vencimiento con links a cada issue en Redmine."""
    nombre_pila = nombre_real.split(",")[1].strip() if "," in nombre_real else nombre_real

    html = f"""
    <p style="font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;">
        <strong>Estimado/a {nombre_pila},</strong><br>
        a continuación encontrás un listado de peticiones con vencimiento próximo que requieren tu atención.
        Se muestran todas aquellas que vencen en los próximos días hábiles según el tipo de trámite.
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

def enviar_correos_individuales(notificaciones, users_map, smtp_server, smtp_port, smtp_user, smtp_pass, from_email, redmine_url):
    print("\nIniciando motor de envío de correos...")
    correos_enviados = 0


    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)

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
                correo_destino = CORREO_ADMIN if REDIRIGIR_A_ADMIN else correo_real

                cuerpo_html = armar_html_persona(nombre_real, datos, redmine_url)

                plantilla_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; margin: 0; padding: 0; }}
        .container {{ max-width: 900px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12); }}
        .header {{ background: linear-gradient(135deg, #A75296 0%, #8B3D7C 100%); color: white; padding: 40px 20px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 28px; font-weight: 300; letter-spacing: 0.5px; }}
        .header p {{ margin: 10px 0 0 0; font-size: 14px; opacity: 0.95; font-weight: 300; }}
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
            <h1>Peticiones Pendientes</h1>
            <p>{nombre_real}</p>
        </div>
        <div class="content">
            {cuerpo_html}
        </div>
        <div class="footer">
            <p>Este reporte fue generado automáticamente por el Sistema Operativo.</p>
            <p>&copy; Estudio Rivarossa — Asesoramiento Fiscal y Contable</p>
        </div>
    </div>
</body>
</html>"""
                
                asunto = f'Reporte de Vencimientos: {nombre_real}'

                msg = EmailMessage()
                msg['Subject'] = asunto
                msg['From'] = from_email
                msg['To'] = correo_destino
                msg.set_content("Este correo requiere un cliente que soporte HTML.")
                msg.add_alternative(plantilla_html, subtype='html')
                
                try:
                    server.send_message(msg)
                    etiqueta = "[REDIRIGIDO A ADMIN]" if REDIRIGIR_A_ADMIN else "[PRODUCCIÓN]"
                    print(f"✓ {etiqueta} Correo procesado para: {nombre_real} -> Enviado a: {correo_destino}")
                    correos_enviados += 1
                except Exception as e:
                    print(f"✗ Error al enviar correo a {nombre_real}: {e}")
                    
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
    
    # Abrimos una sesión HTTP persistente para darle mayor velocidad y robustez
    with requests.Session() as session:
        # 1. Sincronizamos las identidades de los usuarios
        mapa_usuarios = fetch_redmine_users(session, url, redmine_key)
        
        # 2. Descargamos las tareas
        peticiones = fetch_redmine_issues(session, url, redmine_key)
        
    if peticiones:
        # 3. Procesamos y vinculamos (Tarea <-> ID <-> Rol)
        notificaciones_por_persona = process_redmine_data(peticiones)
        
        if notificaciones_por_persona:
            # 4. Disparamos la lógica de correos
            enviar_correos_individuales(
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
            print("El sistema no detectó peticiones pendientes en los periodos filtrados.")