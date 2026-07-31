import os
import requests
import smtplib
from datetime import datetime
from email.message import EmailMessage
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

def get_periodos_validos():
    """Calcula el periodo actual y el anterior basándose en la fecha de hoy"""
    hoy = datetime.now()
    mes_actual = hoy.month
    anio_actual = hoy.year
    
    if mes_actual == 1:
        mes_anterior = 12
        anio_anterior = anio_actual - 1
    else:
        mes_anterior = mes_actual - 1
        anio_anterior = anio_actual
        
    periodo_actual = f"{mes_actual:02d}/{anio_actual}"
    periodo_anterior = f"{mes_anterior:02d}/{anio_anterior}"
    
    return [periodo_actual, periodo_anterior]

def formatear_fecha(fecha_str):
    """Convierte el formato crudo de la API (2026-08-20) a texto legible"""
    try:
        fecha_obj = datetime.strptime(fecha_str, "%Y-%m-%d")
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        return f"{fecha_obj.day} de {meses[fecha_obj.month - 1]} de {fecha_obj.year}"
    except ValueError:
        return fecha_str

def fetch_redmine_issues(redmine_url, api_key):
    print("Conectando a Redmine y descargando TODAS las peticiones abiertas...")
    url = f"{redmine_url}issues.json"
    headers = {"X-Redmine-API-Key": api_key}
    
    all_issues = []
    offset = 0
    limit = 100
    
    while True:
        params = {"status_id": "open", "limit": limit, "offset": offset}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json().get("issues", [])
            if not data: break
            all_issues.extend(data)
            offset += limit
        except Exception as e:
            print(f"Error al conectar con Redmine: {e}")
            break
            
    print(f"¡Éxito! Se descargaron {len(all_issues)} peticiones abiertas en total.")
    return all_issues

def process_redmine_data(issues):
    periodos_buscados = get_periodos_validos()
    print(f"Filtrando peticiones Pendientes de II BB para los periodos: {periodos_buscados}")
    
    vencimientos = {}
    total_tareas = 0
    
    for issue in issues:
        proyecto = issue.get("project", {}).get("name", "")
        tipo = issue.get("tracker", {}).get("name", "")
        estado = issue.get("status", {}).get("name", "")
        asunto = issue.get("subject", "")
        
        if "IMPUESTOS" in proyecto.upper() and tipo == "Tributaria - II BB" and estado == "Pendiente":
            campos_personalizados = issue.get("custom_fields", [])
            fecha_vencimiento = "Sin Vencimiento"
            periodo = ""
            
            for campo in campos_personalizados:
                nombre_campo = campo.get("name", "").upper()
                if "VENCIMIENTO" in nombre_campo:
                    val = str(campo.get("value", "")).strip()
                    if val: fecha_vencimiento = val
                if "PERÍODO" in nombre_campo or "PERIODO" in nombre_campo:
                    val = str(campo.get("value", "")).strip()
                    if val: periodo = val
            
            if periodo in periodos_buscados:
                # Agrupamos usando el periodo Y la fecha de vencimiento
                clave_grupo = (periodo, fecha_vencimiento)
                
                if clave_grupo not in vencimientos:
                    vencimientos[clave_grupo] = []
                    
                empresa = proyecto.split(" / ")[0].strip()
                
                linea = f"{empresa} - {tipo} - {asunto}"
                vencimientos[clave_grupo].append(linea)
                total_tareas += 1
                
    return {
        "total": total_tareas,
        "agrupado_por_fecha": vencimientos,
        "periodos": periodos_buscados
    }

def call_ia_api(datos_procesados, api_key):
    print("Conectando con Groq para el saludo ejecutivo...")
    client = Groq(api_key=api_key)
    
    prompt = f"""
    Redacta un saludo formal y muy breve (máximo 2 oraciones) indicando que a continuación se presenta 
    el reporte automático con {datos_procesados['total']} peticiones pendientes de liquidación 
    correspondientes a los periodos {datos_procesados['periodos']}.
    Devuelve SOLO el texto, sin formato HTML y sin introducciones.
    """
    
    modelos = ["llama-3.1-8b-instant", "llama3-8b-8192", "mixtral-8x7b-32768"]
    saludo_ia = ""
    
    for modelo in modelos:
        try:
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=modelo,
                temperature=0.3,
            )
            saludo_ia = chat_completion.choices[0].message.content.strip()
            break
        except Exception:
            continue
            
    html_lista = f"<p><em>{saludo_ia}</em></p><br>"
    html_lista += "<p>Se encuentran pendientes las siguientes peticiones de liquidación:</p>"
    
    # Ordenamos primero por el Periodo, y luego por la Fecha de vencimiento
    claves_ordenadas = sorted(datos_procesados['agrupado_por_fecha'].keys(), key=lambda x: (x[0], x[1]))
    
    for clave in claves_ordenadas:
        periodo, fecha_raw = clave
        fecha_linda = formatear_fecha(fecha_raw)
        
        # Inyectamos el formato exacto que pediste
        html_lista += f"<h3 style='color: #A75296; margin-bottom: 5px; border-bottom: 1px solid #eee; padding-bottom: 5px;'>Período: {periodo} | Vencimiento: {fecha_linda}</h3>"
        html_lista += "<ul style='margin-top: 0; margin-bottom: 20px;'>"
        for tarea in datos_procesados['agrupado_por_fecha'][clave]:
            html_lista += f"<li style='margin-bottom: 3px;'>{tarea}</li>"
        html_lista += "</ul>"

    return html_lista

def enviar_correo(html_final, smtp_server, smtp_port, smtp_user, smtp_pass, from_email, to_email):
    print("Preparando el envío del correo...")
    
    plantilla_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f7f6; color: #333333; margin: 0; padding: 20px;">
        <div style="max-width: 800px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <div style="background-color: #A75296; color: #ffffff; padding: 20px; text-align: center;">
                <h1 style="margin: 0; font-size: 24px;">Peticiones Pendientes: II BB</h1>
                <p style="margin: 5px 0 0 0; font-size: 14px; opacity: 0.9;">Estudio Rivarossa</p>
            </div>
            <div style="padding: 30px; line-height: 1.6;">
                {html_final}
            </div>
            <div style="background-color: #f8fafc; color: #64748b; text-align: center; padding: 15px; font-size: 12px; border-top: 1px solid #e2e8f0;">
                Este reporte fue generado automáticamente mediante Python y Groq. &copy; Estudio Rivarossa
            </div>
        </div>
    </body>
    </html>
    """
    
    msg = EmailMessage()
    msg['Subject'] = 'Reporte de Vencimientos: Impuestos (II BB)'
    msg['From'] = from_email
    msg['To'] = to_email
    msg.set_content("Este correo requiere un cliente que soporte HTML.")
    msg.add_alternative(plantilla_html, subtype='html')
    
    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print("¡Correo enviado exitosamente!")
    except Exception as e:
        print(f"Error al enviar el correo: {e}")

if __name__ == "__main__":
    url = os.getenv("REDMINE_URL")
    redmine_key = os.getenv("REDMINE_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL")
    to_email = os.getenv("SMTP_TO_EMAIL")
    
    peticiones = fetch_redmine_issues(url, redmine_key)
    
    if peticiones:
        datos = process_redmine_data(peticiones)
        
        if datos["total"] > 0:
            html_listado = call_ia_api(datos, groq_key)
            if html_listado:
                enviar_correo(html_listado, smtp_server, smtp_port, smtp_user, smtp_pass, from_email, to_email)
        else:
            print("No hay peticiones pendientes de II BB para los periodos válidos.")