import os
import html
import base64
import requests
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

def obtener_access_token_oauth(client_id, client_secret, refresh_token):
    """Cambia el refresh token de Google por un access token de corta duración para
    autenticar el SMTP vía OAuth2 (XOAUTH2). Mismo mecanismo que el resto de los
    scripts (ver CLAUDE.md)."""
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
# CONFIGURACIÓN - NOTIFICACIÓN DE CONSULTORÍAS
# ==========================================
# Los clientes de consultoría cargan sus consultas directamente en Redmine y
# Redmine no le avisa a nadie. Este script corre seguido (ver
# notificacion-consultorias.yml), busca las peticiones creadas o editadas desde
# la corrida anterior y le manda un correo a quien corresponda atenderlas.

# Modo Prueba: si está en True, todos los avisos van a CORREO_ADMIN_CONSULTORIAS
# en vez de a cada persona. En producción desde el 06/10/2026.
# Hoy los avisos llegan a Borgogno, Boretto o Mihlager (grupos CONSULTORIA X)
# o a quien esté asignado, por ejemplo Patricio Pogonza.
REDIRIGIR_A_ADMIN_CONSULTORIAS = False
CORREO_ADMIN_CONSULTORIAS = "francoalbrecht@rivarossa.com"

# Identificadores de Redmine de los proyectos de consultoría (incluye sus
# subproyectos). Para sumar un cliente nuevo, agregar acá el identificador del
# proyecto raíz (el que figura en la URL: .../projects/<identificador>).
PROYECTOS_CONSULTORIA = [
    "federicocaglieris",  # CAGLIERIS FEDERICO
    "evelynsaires",       # SAIRES EVELYN
]

# Sólo se avisa a casillas del estudio: aunque alguien asignara una consulta al
# propio cliente, el aviso nunca le llega a alguien de afuera.
DOMINIO_INTERNO = "@rivarossa.com"

# Personas que no deben recibir avisos aunque estén asignadas o en el grupo.
IDS_EXCLUIDOS = {
    "792",  # Caravario, Darién - ya no es empleado (2026-09)
}

# Ventana de búsqueda cuando no hay una corrida exitosa anterior (la primera
# vez, o al correrlo a mano), y tope máximo para no inundar de avisos viejos
# si el workflow estuvo mucho tiempo sin correr.
VENTANA_INICIAL = timedelta(hours=24)
VENTANA_MAXIMA = timedelta(days=7)

# Hora argentina (UTC-3, sin horario de verano) para mostrar en los correos.
ZONA_ART = timezone(timedelta(hours=-3))

# Largo máximo de la descripción / notas que se copian al correo.
MAX_CARACTERES_TEXTO = 1500

NOMBRES_ATRIBUTOS = {
    "status_id": "Estado",
    "assigned_to_id": "Asignado a",
    "project_id": "Proyecto",
    "tracker_id": "Tipo",
    "priority_id": "Prioridad",
    "subject": "Asunto",
    "description": "Descripción",
    "due_date": "Fecha de vencimiento",
    "start_date": "Fecha de inicio",
    "done_ratio": "% realizado",
    "parent_id": "Petición padre",
    "category_id": "Categoría",
    "fixed_version_id": "Versión",
    "estimated_hours": "Horas estimadas",
    "is_private": "Privada",
}

COLOR = "#A75296"

# ==========================================
# VENTANA DE TIEMPO
# ==========================================

def _parsear_iso(texto):
    return datetime.strptime(texto, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

def calcular_ventana():
    """Devuelve (desde, hasta) en UTC: el período cuyas novedades se avisan.
    En GitHub Actions la ventana va desde el inicio de la última corrida exitosa
    hasta el inicio de esta. Así las ventanas quedan pegadas una a la otra: nada
    se avisa dos veces, y si una corrida falla o GitHub la saltea (pasa seguido
    con los "schedule"), la siguiente exitosa cubre su período.
    Localmente: VENTANA_DESDE=YYYY-MM-DDTHH:MM:SSZ o VENTANA_HORAS=N (default 24)."""
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    run_id = os.getenv("GITHUB_RUN_ID")

    if not (repo and token and run_id):
        hasta = datetime.now(timezone.utc).replace(microsecond=0)
        if os.getenv("VENTANA_DESDE"):
            return _parsear_iso(os.getenv("VENTANA_DESDE")), hasta
        return hasta - timedelta(hours=float(os.getenv("VENTANA_HORAS", "24"))), hasta

    # Si la API de GitHub falla, se deja que la excepción corte la corrida: la
    # siguiente exitosa va a cubrir este período (no se pierde nada).
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    base = f"https://api.github.com/repos/{repo}/actions"
    resp = requests.get(f"{base}/runs/{run_id}", headers=headers, timeout=15)
    resp.raise_for_status()
    hasta = _parsear_iso(resp.json()["run_started_at"])

    resp = requests.get(f"{base}/workflows/notificacion-consultorias.yml/runs", headers=headers, timeout=15,
                        params={"status": "success", "per_page": 30})
    resp.raise_for_status()
    anteriores = [_parsear_iso(r["run_started_at"]) for r in resp.json().get("workflow_runs", [])
                  if str(r.get("id")) != str(run_id) and r.get("run_started_at")]
    anteriores = [t for t in anteriores if t < hasta]
    desde = max(anteriores) if anteriores else hasta - VENTANA_INICIAL
    if hasta - desde > VENTANA_MAXIMA:
        print(f"[WARN] La última corrida exitosa fue hace más de {VENTANA_MAXIMA.days} días; "
              f"se limita la ventana para no mandar avisos viejos.")
        desde = hasta - VENTANA_MAXIMA
    return desde, hasta

# ==========================================
# REDMINE
# ==========================================

class Redmine:
    """Acceso de sólo lectura a la API de Redmine, con caché de lo que se consulta varias veces."""

    def __init__(self, session, url, api_key):
        self.s = session
        self.url = url
        self.h = {"X-Redmine-API-Key": api_key}
        self._grupos = {}
        self._grupos_de_proyecto = {}
        self._nombres_proyecto = {}
        self._campos = None
        self._estados = None

    def get(self, path, params=None):
        r = self.s.get(f"{self.url}{path}", headers=self.h, params=params or {}, timeout=20)
        r.raise_for_status()
        return r.json()

    def usuarios(self):
        """{id: {"nombre", "correo"}} de todos los usuarios (los grupos no figuran acá)."""
        res, offset = {}, 0
        while True:
            data = self.get("users.json", {"limit": 100, "offset": offset}).get("users", [])
            if not data:
                break
            for u in data:
                res[str(u["id"])] = {
                    "nombre": f"{u.get('lastname', '')}, {u.get('firstname', '')}".strip(" ,"),
                    "correo": u.get("mail", ""),
                }
            offset += 100
        return res

    def peticiones_actualizadas(self, proyecto, desde):
        """Peticiones del proyecto (y subproyectos), en cualquier estado, actualizadas desde `desde`."""
        res, offset = [], 0
        filtro = ">=" + desde.strftime("%Y-%m-%dT%H:%M:%SZ")
        while True:
            data = self.get("issues.json", {"project_id": proyecto, "status_id": "*", "updated_on": filtro,
                                            "limit": 100, "offset": offset}).get("issues", [])
            if not data:
                break
            res.extend(data)
            offset += 100
        return res

    def peticion_con_historial(self, issue_id):
        return self.get(f"issues/{issue_id}.json", {"include": "journals"})["issue"]

    def miembros_de_grupo(self, grupo_id):
        if grupo_id not in self._grupos:
            data = self.get(f"groups/{grupo_id}.json", {"include": "users"})["group"]
            self._grupos[grupo_id] = (data.get("name", ""), [str(u["id"]) for u in data.get("users", [])])
        return self._grupos[grupo_id]

    def grupos_del_proyecto(self, proyecto_id):
        """IDs de los grupos que son miembros del proyecto (ej. CONSULTORIA CAGLIERIS)."""
        if proyecto_id not in self._grupos_de_proyecto:
            data = self.get(f"projects/{proyecto_id}/memberships.json", {"limit": 100}).get("memberships", [])
            self._grupos_de_proyecto[proyecto_id] = [str(m["group"]["id"]) for m in data if m.get("group")]
        return self._grupos_de_proyecto[proyecto_id]

    def nombre_proyecto(self, proyecto_id):
        if proyecto_id not in self._nombres_proyecto:
            try:
                self._nombres_proyecto[proyecto_id] = self.get(f"projects/{proyecto_id}.json")["project"]["name"]
            except Exception:
                self._nombres_proyecto[proyecto_id] = f"Proyecto {proyecto_id}"
        return self._nombres_proyecto[proyecto_id]

    def nombre_estado(self, estado_id):
        if self._estados is None:
            try:
                self._estados = {str(s["id"]): s["name"] for s in self.get("issue_statuses.json")["issue_statuses"]}
            except Exception:
                self._estados = {}
        return self._estados.get(str(estado_id), str(estado_id))

    def nombre_campo(self, campo_id):
        if self._campos is None:
            try:
                self._campos = {str(c["id"]): c["name"] for c in self.get("custom_fields.json")["custom_fields"]}
            except Exception:
                self._campos = {}
        return self._campos.get(str(campo_id), "Campo personalizado")

# ==========================================
# DETECCIÓN DE NOVEDADES Y DESTINATARIOS
# ==========================================

def novedades_de_peticion(issue, desde, hasta):
    """Eventos de la petición dentro de [desde, hasta): su creación y cada edición
    (journal). Cada evento: {"tipo": "nueva"|"edicion", "fecha", "actor_id", "actor",
    "texto", "detalles"}."""
    eventos = []
    creada = _parsear_iso(issue["created_on"])
    if desde <= creada < hasta:
        eventos.append({
            "tipo": "nueva", "fecha": creada,
            "actor_id": str(issue.get("author", {}).get("id", "")),
            "actor": issue.get("author", {}).get("name", ""),
            "texto": issue.get("description") or "", "detalles": [],
        })
    for j in issue.get("journals", []):
        fecha = _parsear_iso(j["created_on"])
        if desde <= fecha < hasta:
            eventos.append({
                "tipo": "edicion", "fecha": fecha,
                "actor_id": str(j.get("user", {}).get("id", "")),
                "actor": j.get("user", {}).get("name", ""),
                "texto": j.get("notes") or "", "detalles": j.get("details", []),
            })
    return sorted(eventos, key=lambda e: e["fecha"])

def destinatarios_de_peticion(issue, redmine, users_map):
    """IDs de usuario a avisar y de dónde salen:
      - asignada a una persona -> esa persona;
      - asignada a un grupo (ej. CONSULTORIA CAGLIERIS) -> los miembros del grupo;
      - sin asignar -> los miembros de los grupos que son miembros del proyecto.
    Devuelve (ids, motivo)."""
    asignado = issue.get("assigned_to")
    if asignado:
        asignado_id = str(asignado["id"])
        if asignado_id in users_map:
            return [asignado_id], f"asignada a {asignado.get('name', '')}"
        nombre_grupo, miembros = redmine.miembros_de_grupo(asignado_id)
        return miembros, f"asignada al grupo {nombre_grupo}"

    ids, nombres = [], []
    for grupo_id in redmine.grupos_del_proyecto(issue["project"]["id"]):
        nombre_grupo, miembros = redmine.miembros_de_grupo(grupo_id)
        nombres.append(nombre_grupo)
        ids += [m for m in miembros if m not in ids]
    if ids:
        return ids, f"sin asignar: se avisa al grupo {', '.join(nombres)}"
    return [], "sin asignar y el proyecto no tiene grupo de consultoría"

def armar_avisos(peticiones, redmine, users_map, desde, hasta):
    """Devuelve ({persona_id: [(issue, motivo, [eventos])]}, [alertas para el resumen])."""
    avisos = {}
    alertas = []
    for issue in peticiones:
        eventos = novedades_de_peticion(issue, desde, hasta)
        if not eventos:
            continue
        destinatarios, motivo = destinatarios_de_peticion(issue, redmine, users_map)
        if not destinatarios:
            alertas.append(f"#{issue['id']}: tuvo novedades pero no hay a quién avisar ({motivo}).")
            continue
        for persona_id in destinatarios:
            if persona_id in IDS_EXCLUIDOS:
                continue
            correo = users_map.get(persona_id, {}).get("correo", "")
            if not correo.lower().endswith(DOMINIO_INTERNO):
                alertas.append(f"#{issue['id']}: no se avisa a {users_map.get(persona_id, {}).get('nombre', persona_id)} "
                               f"porque su correo no es del estudio.")
                continue
            # Nadie recibe aviso de lo que hizo él mismo.
            propios = [e for e in eventos if e["actor_id"] != persona_id]
            if propios:
                avisos.setdefault(persona_id, []).append((issue, motivo, propios))
    return avisos, alertas

# ==========================================
# HTML
# ==========================================

def _texto_html(texto):
    """Texto cargado en Redmine -> HTML seguro (escapado), recortado y con saltos de línea."""
    texto = (texto or "").strip()
    if len(texto) > MAX_CARACTERES_TEXTO:
        texto = texto[:MAX_CARACTERES_TEXTO].rstrip() + " […]"
    return html.escape(texto).replace("\n", "<br>")

def _fecha_art(fecha_utc):
    return fecha_utc.astimezone(ZONA_ART).strftime("%d/%m/%Y %H:%M")

def nombre_usuario_o_grupo(id_texto, redmine, users_map):
    if id_texto in users_map:
        return users_map[id_texto]["nombre"]
    try:
        return redmine.miembros_de_grupo(id_texto)[0]
    except Exception:
        return f"#{id_texto}"

def describir_cambio(detalle, redmine, users_map):
    """Un 'detail' de un journal de Redmine -> texto legible ('Estado: Pendiente → Terminado')."""
    prop, nombre = detalle.get("property"), detalle.get("name", "")
    viejo, nuevo = detalle.get("old_value"), detalle.get("new_value")
    if prop == "attachment":
        return f"Adjuntó el archivo {nuevo}" if nuevo else f"Quitó el archivo {viejo}"
    if prop == "relation":
        return "Cambió las peticiones relacionadas"
    if prop == "cf":
        etiqueta = redmine.nombre_campo(nombre)
    elif prop == "attr":
        etiqueta = NOMBRES_ATRIBUTOS.get(nombre, nombre)
        if nombre == "description":
            return "Modificó la descripción"
        if nombre == "status_id":
            viejo, nuevo = [redmine.nombre_estado(v) if v else v for v in (viejo, nuevo)]
        elif nombre == "assigned_to_id":
            viejo, nuevo = [nombre_usuario_o_grupo(v, redmine, users_map) if v else v for v in (viejo, nuevo)]
        elif nombre == "project_id":
            viejo, nuevo = [redmine.nombre_proyecto(v) if v else v for v in (viejo, nuevo)]
    else:
        etiqueta = nombre
    return f"{etiqueta}: {viejo or '(vacío)'} → {nuevo or '(vacío)'}"

def armar_html_persona(nombre_real, items, redmine, users_map, redmine_url):
    # Sin cartel de modo prueba (pedido del 06/10/2026): el correo de prueba se ve
    # igual al de producción. A quién le llegaría figura en el resumen de ejecución.
    nombre_pila = nombre_real.split(",")[1].strip() if "," in nombre_real else nombre_real
    hay_nuevas = any(e["tipo"] == "nueva" for _, _, evs in items for e in evs)
    cuerpo = (f'<p style="font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;"><strong>Hola {html.escape(nombre_pila)},</strong><br>'
               f'{"Se cargaron o actualizaron" if hay_nuevas else "Se actualizaron"} consultas de clientes de consultoría '
               f'que te tocan atender:</p>')

    for issue, motivo, eventos in items:
        link = f"{redmine_url}issues/{issue['id']}"
        proyecto = issue.get("project", {}).get("name", "")
        raiz = issue.get("_cliente", "")
        if raiz == proyecto:
            raiz = ""
        cuerpo += f"""
        <div style="margin: 0 0 22px 0; border: 1px solid #e6d3e2; border-left: 4px solid {COLOR}; border-radius: 4px; padding: 14px 16px;">
            <p style="margin: 0 0 4px 0; font-size: 16px;">
                <a href="{link}" style="color: {COLOR}; text-decoration: none; font-weight: bold;">#{issue['id']}</a>
                <strong style="color: #333333;">{html.escape(issue.get('subject', ''))}</strong>
            </p>
            <p style="margin: 0 0 12px 0; font-size: 12px; color: #777777;">
                {html.escape(raiz + ' › ' if raiz else '')}{html.escape(proyecto)} · Estado: {html.escape(issue.get('status', {}).get('name', ''))}
                · {html.escape(motivo[0].upper() + motivo[1:])}
            </p>
        """
        for e in eventos:
            titulo = "Nueva consulta" if e["tipo"] == "nueva" else "Actualización"
            cuerpo += f"""
            <div style="margin: 10px 0 0 0; padding: 10px 12px; background-color: #fafafa; border-radius: 3px;">
                <p style="margin: 0 0 6px 0; font-size: 12px; color: #555555;">
                    <strong style="color: {COLOR};">{titulo}</strong> de {html.escape(e['actor'])} — {_fecha_art(e['fecha'])} hs
                </p>
            """
            if e["texto"].strip():
                cuerpo += f'<p style="margin: 0 0 6px 0; font-size: 13px; line-height: 1.5; color: #333333;">{_texto_html(e["texto"])}</p>'
            cambios = [describir_cambio(d, redmine, users_map) for d in e["detalles"]]
            if cambios:
                cuerpo += ('<ul style="margin: 4px 0 0 18px; padding: 0; font-size: 12px; color: #666666;">'
                           + "".join(f"<li>{html.escape(c)}</li>" for c in cambios) + "</ul>")
            cuerpo += "</div>"
        cuerpo += f"""
            <p style="margin: 12px 0 0 0;">
                <a href="{link}" style="display: inline-block; padding: 7px 14px; background-color: {COLOR}; color: #ffffff; text-decoration: none; border-radius: 3px; font-size: 13px;">Abrir en Redmine</a>
            </p>
        </div>
        """
    return armar_plantilla(cuerpo)

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
        .container {{ max-width: 760px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12); }}
        .header {{ background-color: #A75296; background: linear-gradient(135deg, #A75296 0%, #8B3D7C 100%); color: #ffffff !important; padding: 25px 20px; text-align: center; }}
        .logo-container {{ display: flex; justify-content: center; }}
        .content {{ padding: 30px 28px; }}
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
            <p>Este aviso fue generado de manera automática. &copy; Estudio Rivarossa</p>
        </div>
    </div>
</body>
</html>"""

MAX_CARACTERES_ASUNTO_CONSULTA = 80

def _nombre_corto_cliente(issue):
    """'CAGLIERIS FEDERICO' -> 'Caglieris': el proyecto raíz se llama APELLIDO NOMBRE."""
    cliente = (issue.get("_cliente") or issue.get("project", {}).get("name", "")).strip()
    return cliente.split()[0].title() if cliente else "Consultoría"

def asunto_del_aviso(items):
    """Título del correo: cliente(s), si son nuevas o actualizadas, y el asunto de
    la primera consulta (las nuevas primero), p. ej.
      [Consultoría] Caglieris – Nueva consulta: Liquidación de ganancias 2025
      [Consultoría] Caglieris, Saires – 1 nueva y 1 actualizada: Liquidación de ganancias 2025 y 1 más"""
    es_nueva = lambda eventos: any(e["tipo"] == "nueva" for e in eventos)
    ordenados = sorted(items, key=lambda it: not es_nueva(it[2]))
    clientes = list(dict.fromkeys(_nombre_corto_cliente(issue) for issue, _, _ in ordenados))

    nuevas = sum(1 for _, _, eventos in ordenados if es_nueva(eventos))
    actualizadas = len(ordenados) - nuevas
    if len(ordenados) == 1:
        que = "Nueva consulta" if nuevas else "Consulta actualizada"
    elif not actualizadas:
        que = f"{nuevas} consultas nuevas"
    elif not nuevas:
        que = f"{actualizadas} consultas actualizadas"
    else:
        que = (f"{nuevas} nueva{'s' if nuevas > 1 else ''} y "
               f"{actualizadas} actualizada{'s' if actualizadas > 1 else ''}")

    primer_asunto = (ordenados[0][0].get("subject") or "").strip() or f"#{ordenados[0][0]['id']}"
    if len(primer_asunto) > MAX_CARACTERES_ASUNTO_CONSULTA:
        primer_asunto = primer_asunto[:MAX_CARACTERES_ASUNTO_CONSULTA - 1].rstrip() + "…"
    resto = f" y {len(ordenados) - 1} más" if len(ordenados) > 1 else ""
    return f"[Consultoría] {', '.join(clientes)} – {que}: {primer_asunto}{resto}"

def preparar_correos(avisos, redmine, users_map, redmine_url):
    """[(persona_id, nombre_real, correo_destino, etiqueta, asunto, html, ids_peticiones)]"""
    correos = []
    for persona_id, items in avisos.items():
        nombre_real = users_map.get(persona_id, {}).get("nombre", f"Usuario {persona_id}")
        correo_real = users_map.get(persona_id, {}).get("correo", "")
        if REDIRIGIR_A_ADMIN_CONSULTORIAS:
            destino, etiqueta = CORREO_ADMIN_CONSULTORIAS, "[REDIRIGIDO A ADMIN]"
        else:
            destino, etiqueta = correo_real, "[PRODUCCIÓN]"
        asunto = asunto_del_aviso(items)
        html_correo = armar_html_persona(nombre_real, items, redmine, users_map, redmine_url)
        correos.append((persona_id, nombre_real, destino, etiqueta, asunto, html_correo, [i["id"] for i, _, _ in items]))
    return correos

def enviar_correos(correos, smtp_server, smtp_port, smtp_user, access_token, from_email):
    enviados = 0
    with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
        server.starttls()
        autenticar_smtp_oauth(server, smtp_user, access_token)
        for _, nombre_real, destino, etiqueta, asunto, html_correo, ids in correos:
            msg = EmailMessage()
            msg['Subject'] = asunto
            msg['From'] = from_email
            msg['To'] = destino
            msg.set_content("Este correo requiere un cliente que soporte HTML.")
            msg.add_alternative(html_correo, subtype='html')
            try:
                server.send_message(msg)
                # El repo es público: en el log van sólo números de petición, nunca el contenido.
                print(f"[OK] {etiqueta} Aviso para: {nombre_real} -> {destino} (peticiones {', '.join(f'#{i}' for i in ids)})")
                enviados += 1
            except Exception as e:
                print(f"[ERROR] Error al enviar el aviso a {nombre_real}: {e}")
    if correos and enviados == 0:
        raise RuntimeError("Había avisos para mandar pero no se pudo enviar ninguno (ver errores arriba).")
    return enviados

def enviar_resumen_ejecucion(estado, detalle_lineas, smtp_server, smtp_port, smtp_user, access_token, from_email):
    """Correo de estado a admin. Como este script corre muchas veces por día, sólo se
    manda cuando hubo avisos, alertas o un error (no en cada corrida sin novedades)."""
    msg = EmailMessage()
    msg['Subject'] = f"[Agente Consultorías] Ejecución {estado} - {datetime.now(ZONA_ART).strftime('%d/%m/%Y %H:%M')}"
    msg['From'] = from_email
    msg['To'] = CORREO_ADMIN_CONSULTORIAS
    msg.set_content("\n".join(detalle_lineas))
    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            autenticar_smtp_oauth(server, smtp_user, access_token)
            server.send_message(msg)
        print(f"[OK] Resumen de ejecución enviado a {CORREO_ADMIN_CONSULTORIAS}")
    except Exception as e:
        print(f"[ERROR] No se pudo enviar el resumen de ejecución a {CORREO_ADMIN_CONSULTORIAS}: {e}")

if __name__ == "__main__":
    # SOLO_VISTA_PREVIA=1: no manda nada, guarda los HTML en ./vista_previa_consultorias/.
    solo_vista_previa = os.getenv("SOLO_VISTA_PREVIA") == "1"

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
    desde = hasta = None
    revisadas = 0
    correos = []
    alertas = []
    enviados = 0
    access_token = None

    try:
        desde, hasta = calcular_ventana()
        print(f"Ventana: {_fecha_art(desde)} a {_fecha_art(hasta)} (hora argentina)")

        with requests.Session() as session:
            redmine = Redmine(session, url, redmine_key)
            mapa_usuarios = redmine.usuarios()

            ids_vistos = set()
            peticiones = []
            for proyecto in PROYECTOS_CONSULTORIA:
                cliente = redmine.nombre_proyecto(proyecto)
                for resumen in redmine.peticiones_actualizadas(proyecto, desde):
                    if resumen["id"] not in ids_vistos:
                        ids_vistos.add(resumen["id"])
                        issue = redmine.peticion_con_historial(resumen["id"])
                        issue["_cliente"] = cliente  # las consultas suelen estar en subproyectos
                        peticiones.append(issue)
            revisadas = len(peticiones)
            print(f"Peticiones de consultoría actualizadas en la ventana: {revisadas}")

            avisos, alertas = armar_avisos(peticiones, redmine, mapa_usuarios, desde, hasta)
            correos = preparar_correos(avisos, redmine, mapa_usuarios, url)

        if solo_vista_previa:
            carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vista_previa_consultorias")
            os.makedirs(carpeta, exist_ok=True)
            for persona_id, nombre_real, destino, etiqueta, asunto, html_correo, ids in correos:
                archivo = os.path.join(carpeta, f"{hasta.strftime('%Y%m%d_%H%M')}_{persona_id}.html")
                with open(archivo, "w", encoding="utf-8") as f:
                    f.write(html_correo)
                print(f"[VISTA PREVIA] {etiqueta} {nombre_real} -> {destino} | {asunto} | {archivo}")
        elif correos:
            access_token = obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)
            enviados = enviar_correos(correos, smtp_server, smtp_port, smtp_user, access_token, from_email)
        else:
            print("No hay novedades de consultoría para avisar.")

    except Exception as e:
        estado = "CON ERROR"
        error_texto = str(e)
        print(f"[ERROR] Ejecución interrumpida: {e}")

    finally:
        detalle = [
            f"Ventana revisada: {_fecha_art(desde) if desde else '?'} a {_fecha_art(hasta) if hasta else '?'} (hora argentina)",
            f"Modo prueba (todo a admin): {'SÍ' if REDIRIGIR_A_ADMIN_CONSULTORIAS else 'NO'}",
            f"Peticiones de consultoría con movimiento: {revisadas}",
            f"Avisos armados: {len(correos)}"
            + "".join(f"\n  - {n} ({', '.join(f'#{i}' for i in ids)})" for _, n, _, _, _, _, ids in correos),
            f"Avisos enviados con éxito: {enviados}",
        ]
        if alertas:
            detalle += ["", "Alertas:"] + [f"  {a}" for a in alertas]
        if error_texto:
            detalle += ["", f"Error: {error_texto}"]

        if alertas:
            print(f"[WARN] {len(alertas)} alerta(s), ver resumen de ejecución.")
        if solo_vista_previa:
            print("\n--- Resumen (no enviado, vista previa) ---\n" + "\n".join(detalle))
        elif correos or alertas or error_texto:
            if access_token is None:
                try:
                    access_token = obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)
                except Exception as e:
                    print(f"[ERROR] No se pudo obtener un access token para mandar el resumen: {e}")
            if access_token:
                enviar_resumen_ejecucion(estado, detalle, smtp_server, smtp_port, smtp_user, access_token, from_email)

    if estado == "CON ERROR":
        raise SystemExit(1)
