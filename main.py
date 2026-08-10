import os
import requests
import smtplib
from datetime import datetime
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# CONFIGURACIÓN GENERAL DEL AGENTE
# ==========================================
# MODO_PRUEBA: Si es True, TODOS los correos se enviarán al CORREO_ADMIN,
# pero con los nombres y datos reales en el cuerpo del mensaje para que puedas testear.
MODO_PRUEBA = True
CORREO_ADMIN = "francoalbrecht@rivarossa.com"

# Límite de seguridad para no saturar tu bandeja durante las pruebas
LIMITE_ENVIOS_PRUEBA = 2

# Días de anticipación con los que se dispara el correo antes del vencimiento.
# Ej: vencimiento el 19 con DIAS_ANTICIPACION_ENVIO = 2 -> el correo sale el 17.
DIAS_ANTICIPACION_ENVIO = 2

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

def calcular_dias_restantes(fecha_vencimiento_str, hoy=None):
    """Devuelve cuántos días faltan para el vencimiento, o None si la fecha no es parseable."""
    if hoy is None:
        hoy = datetime.now().date()
    try:
        fecha_venc = datetime.strptime(fecha_vencimiento_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (fecha_venc - hoy).days

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

def process_redmine_data(issues):
    """Filtra y agrupa las peticiones cuyo vencimiento cae exactamente a
    DIAS_ANTICIPACION_ENVIO días de hoy, asignando la tarea y el rol exacto al ID del usuario"""
    notificaciones = {}
    descartadas_sin_fecha = 0
    descartadas_fuera_de_rango = 0

    for issue in issues:
        proyecto = issue.get("project", {}).get("name", "")
        tipo = issue.get("tracker", {}).get("name", "")
        estado = issue.get("status", {}).get("name", "")
        asunto = issue.get("subject", "")
        
        if "IMPUESTOS" in proyecto.upper() and tipo == "Tributaria - II BB" and estado == "Pendiente":
            campos = issue.get("custom_fields", [])
            
            fecha_vencimiento = "Sin Vencimiento"
            periodo = ""
            # Lista para guardar qué ID tiene qué Rol en esta tarea
            roles_involucrados = [] 
            
            for c in campos:
                nombre = c.get("name", "").upper()
                valor = str(c.get("value", "")).strip()
                
                if "VENCIMIENTO" in nombre and valor:
                    fecha_vencimiento = valor
                elif ("PERÍODO" in nombre or "PERIODO" in nombre) and valor:
                    periodo = valor
                elif nombre in ["AUXILIAR", "LIQUIDADOR", "RESPONSABLE"] and valor:
                    # Guardamos el ID del usuario y el rol exacto que cumple
                    roles_involucrados.append({
                        "id": valor, 
                        "rol": nombre.capitalize()
                    })
            
            # --- Filtro por fecha de vencimiento (reemplaza al filtro por período) ---
            dias_restantes = calcular_dias_restantes(fecha_vencimiento)
            if dias_restantes is None:
                descartadas_sin_fecha += 1
                continue
            if dias_restantes != DIAS_ANTICIPACION_ENVIO:
                descartadas_fuera_de_rango += 1
                continue

            empresa = proyecto.split(" / ")[0].strip()

            # Asignamos la tarea formateada a cada ID involucrado
            for involucrado in roles_involucrados:
                persona_id = involucrado["id"]
                rol_asignado = involucrado["rol"]

                # Agregamos el rol exacto a la línea que verá el usuario
                linea_tarea = f"<strong>{empresa}</strong> - {tipo} - {asunto} <em>(Rol: {rol_asignado})</em>"

                if persona_id not in notificaciones:
                    notificaciones[persona_id] = {}

                clave_grupo = (periodo, fecha_vencimiento)
                if clave_grupo not in notificaciones[persona_id]:
                    notificaciones[persona_id][clave_grupo] = []

                notificaciones[persona_id][clave_grupo].append(linea_tarea)

    print(f"Filtrado por vencimiento: {descartadas_sin_fecha} sin fecha válida, "
          f"{descartadas_fuera_de_rango} fuera de la ventana de {DIAS_ANTICIPACION_ENVIO} días.")
    return notificaciones

def armar_html_persona(nombre_real, datos_agrupados):
    """Genera el código HTML limpio utilizando el nombre real de la persona"""
    nombre_pila = nombre_real.split(",")[1].strip() if "," in nombre_real else nombre_real
    
    html = f"<p><em>Estimado/a {nombre_pila}, a continuación se detalla tu reporte automático de peticiones pendientes.</em></p><br>"
    html += "<p>Tenés participación asignada en las siguientes tareas:</p>"
    
    claves_ordenadas = sorted(datos_agrupados.keys(), key=lambda x: (x[0], x[1]))
    
    for clave in claves_ordenadas:
        periodo, fecha_raw = clave
        fecha_linda = formatear_fecha(fecha_raw)
        
        html += f"<h3 style='color: #A75296; margin-bottom: 5px; border-bottom: 1px solid #eee; padding-bottom: 5px;'>Período: {periodo} | Vencimiento: {fecha_linda}</h3>"
        html += "<ul style='margin-top: 0; margin-bottom: 25px; line-height: 1.8;'>"
        for tarea in datos_agrupados[clave]:
            html += f"<li style='margin-bottom: 4px;'>{tarea}</li>"
        html += "</ul>"
        
    return html

def enviar_correos_individuales(notificaciones, users_map, smtp_server, smtp_port, smtp_user, smtp_pass, from_email):
    print("\nIniciando motor de envío de correos...")
    correos_enviados = 0
    
    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            
            for persona_id, datos in notificaciones.items():
                
                # Freno de seguridad para pruebas
                if MODO_PRUEBA and correos_enviados >= LIMITE_ENVIOS_PRUEBA:
                    print(f"\n[!] Límite de prueba alcanzado ({LIMITE_ENVIOS_PRUEBA} correos). Envío detenido.")
                    break
                
                # Traducir el ID (ej: "579") al nombre y correo real
                if users_map and persona_id in users_map:
                    nombre_real = users_map[persona_id]["nombre"]
                    correo_real = users_map[persona_id]["correo"]
                else:
                    nombre_real = f"Usuario {persona_id}"
                    correo_real = CORREO_ADMIN # Fallback por seguridad
                
                # Redireccionamiento según entorno (Prueba vs Producción)
                correo_destino = CORREO_ADMIN if MODO_PRUEBA else correo_real
                
                cuerpo_html = armar_html_persona(nombre_real, datos)
                
                plantilla_html = f"""
                <!DOCTYPE html>
                <html>
                <head><meta charset="utf-8"></head>
                <body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f7f6; color: #333333; margin: 0; padding: 20px;">
                    <div style="max-width: 850px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                        <div style="background-color: #A75296; color: #ffffff; padding: 20px; text-align: center;">
                            <h1 style="margin: 0; font-size: 24px;">Peticiones Pendientes: II BB</h1>
                            <p style="margin: 5px 0 0 0; font-size: 14px; opacity: 0.9;">Reporte Ejecutivo: {nombre_real}</p>
                        </div>
                        <div style="padding: 30px; font-size: 14px;">
                            {cuerpo_html}
                        </div>
                        <div style="background-color: #f8fafc; color: #64748b; text-align: center; padding: 15px; font-size: 12px; border-top: 1px solid #e2e8f0;">
                            Este reporte fue generado automáticamente por el Sistema Operativo. &copy; Estudio Rivarossa
                        </div>
                    </div>
                </body>
                </html>
                """
                
                msg = EmailMessage()
                msg['Subject'] = f'Reporte de Vencimientos: {nombre_real}'
                msg['From'] = from_email
                msg['To'] = correo_destino
                msg.set_content("Este correo requiere un cliente que soporte HTML.")
                msg.add_alternative(plantilla_html, subtype='html')
                
                try:
                    server.send_message(msg)
                    etiqueta = "[MODO PRUEBA]" if MODO_PRUEBA else "[PRODUCCIÓN]"
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
                from_email
            )
        else:
            print("El sistema no detectó peticiones pendientes en los periodos filtrados.")