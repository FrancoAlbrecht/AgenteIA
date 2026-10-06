import os
import requests
import smtplib
from datetime import datetime
from email.message import EmailMessage

# Se reutilizan las reglas y el filtrado de los reportes de cada sector, para que
# el R2 vea exactamente lo mismo que les llegó a las personas a su cargo. Importar
# estos módulos no manda nada: todo su envío está bajo `if __name__ == "__main__"`.
import main_impuestos as imp
import main_auditoria as aud

# ==========================================
# CONFIGURACIÓN GENERAL DEL RESUMEN R2
# ==========================================
# Cada R2 (subgerente del sector) recibe un único correo que unifica lo que les
# llegó a todas las personas del sector ese día. A diferencia del correo de cada
# empleado (una tarjeta por persona por petición), acá cada petición aparece UNA
# sola vez, en una fila de tabla, con todas las personas involucradas: así el
# resumen de impuestos baja de ~120 tarjetas a ~40 filas por día.

# Modo Prueba: mientras esté en True, todos los resúmenes van a CORREO_ADMIN_R2
# (con un cartel amarillo que indica a quién le llegaría en producción).
REDIRIGIR_A_ADMIN_R2 = True
CORREO_ADMIN_R2 = "francoalbrecht@rivarossa.com"

# ID de Redmine de cada R2 (el correo se toma de Redmine).
R2_POR_SECTOR = {
    "impuestos": "542",  # Margaria, Cintia
    "auditoria": "717",  # Depetris, Matías
}

# Personas del sector que NO están a cargo del R2 (pedido del 06/10/2026). Sus
# peticiones no entran en el resumen, salvo que también intervenga alguien que
# sí esté a cargo del R2 (en ese caso la fila muestra al equipo completo).
FUERA_DEL_EQUIPO_R2 = {
    "impuestos": {
        "579",  # Pogonza, Patricio
        "494",  # Boretto, Facundo
    },
    "auditoria": {
        "597",  # Molfino, Tobías
        "568",  # Fenoglio, Mauricio
        "254",  # Trinca, Luisina
        "242",  # Borgogno, Luis
    },
}

# Columnas cortas para la tabla "carga por persona".
TITULOS_CORTOS = {
    "Tributaria - II BB": "II BB",
    "Tributaria - DREI": "DREI",
    "Tributaria - CM": "CM",
    "Tributaria - IVA": "IVA",
    "Tributaria - Sicore": "Sicore",
    "Tributaria - Ag. Recaudación": "Ag. Rec.",
    "CyA Balance": "Balance",
    "CyA Corte": "Corte",
    "CyA Auditoria": "Auditoría",
}

ROLES_IMPUESTOS = ["Auxiliar", "Liquidador", "Responsable"]
CAMPO_SUBESTADO_IMPUESTOS = "SUBESTADO TRIBUTARIA"

COLOR = "#A75296"
ESTILO_TH = f"text-align: left; padding: 6px 8px; background-color: {COLOR}; color: #ffffff; font-size: 12px; font-weight: 600;"
ESTILO_TD = "padding: 6px 8px; border-bottom: 1px solid #eeeeee; font-size: 12px; vertical-align: top;"

# ==========================================
# UTILIDADES
# ==========================================

def fecha_de_hoy():
    """FECHA_SIMULADA=YYYY-MM-DD permite probar localmente cómo saldría el resumen otro día."""
    simulada = os.getenv("FECHA_SIMULADA")
    return datetime.strptime(simulada, "%Y-%m-%d").date() if simulada else datetime.now().date()

def nombre_legible(users_map, persona_id):
    """'Apellido, Nombre' de Redmine -> 'Nombre Apellido', más cómodo para leer en una tabla."""
    nombre = users_map.get(persona_id, {}).get("nombre", f"Usuario {persona_id}")
    if "," in nombre:
        apellido, pila = nombre.split(",", 1)
        return f"{pila.strip()} {apellido.strip()}"
    return nombre.strip()

def fetch_redmine_issues_estricto(session, redmine_url, api_key):
    """Igual que en los otros scripts, pero un error a mitad de la descarga se
    relanza: un resumen armado con datos parciales le mostraría al R2 un equipo
    con menos trabajo del real (el resumen de ejecución avisa el error)."""
    print("Descargando padrón de peticiones abiertas...")
    url = f"{redmine_url}issues.json"
    headers = {"X-Redmine-API-Key": api_key}
    all_issues = []
    offset = 0
    while True:
        params = {"status_id": "open", "limit": 100, "offset": offset}
        response = session.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json().get("issues", [])
        if not data:
            break
        all_issues.extend(data)
        offset += 100
    print(f"[OK] Peticiones descargadas: {len(all_issues)} en total.")
    return all_issues

def campos_de(issue):
    """{NOMBRE DEL CAMPO EN MAYÚSCULAS: valor como texto} de una petición."""
    res = {}
    for c in issue.get("custom_fields", []):
        valor = c.get("value")
        res[c.get("name", "").strip().upper()] = str(valor).strip() if valor not in (None, "") else ""
    return res

def invertir_por_peticion(notificaciones, fuera_del_equipo):
    """Da vuelta la estructura por persona de los reportes de cada sector
    ({persona: {tipo: {clave: [tareas]}}}) a una por petición:
    {issue_id: {tipo, clave, empresa, asunto, equipo: [(rol, persona_id)]}}.
    Sólo quedan las peticiones en las que interviene al menos una persona a
    cargo del R2 (que no esté en fuera_del_equipo)."""
    por_peticion = {}
    for persona_id, por_tipo in notificaciones.items():
        for tipo, grupos in por_tipo.items():
            for clave, tareas in grupos.items():
                for t in tareas:
                    entrada = por_peticion.setdefault(t["issue_id"], {
                        "tipo": tipo, "clave": clave, "empresa": t["empresa"],
                        "asunto": t["asunto"], "equipo": [],
                    })
                    entrada["equipo"].append((t["rol"], persona_id))
    return {iid: p for iid, p in por_peticion.items()
            if any(persona_id not in fuera_del_equipo for _, persona_id in p["equipo"])}

def solo_equipo(notificaciones, fuera_del_equipo):
    """Las notificaciones por persona, sin las personas que no están a cargo del R2."""
    return {p: datos for p, datos in notificaciones.items() if p not in fuera_del_equipo}

# ==========================================
# IMPUESTOS
# ==========================================

def sin_responsables_impuestos(issues, hoy):
    """Peticiones que hoy deberían haberse avisado (mismo criterio que main_impuestos)
    pero no tienen a nadie en Auxiliar / Liquidador / Responsable: a ningún empleado
    le llegó el aviso, así que es justo lo que el R2 tiene que ver."""
    res = []
    for issue in issues:
        tipo = issue.get("tracker", {}).get("name", "")
        dias = imp.REGLAS_NOTIFICACION.get(tipo)
        proyecto = issue.get("project", {}).get("name", "")
        if dias is None or "IMPUESTOS" not in proyecto.upper() or issue.get("status", {}).get("name") != "Pendiente":
            continue
        campos = campos_de(issue)
        if imp.calcular_fecha_notificacion(campos.get("VENCIMIENTO DD. JJ.", ""), dias) != hoy:
            continue
        roles = [campos.get(r) for r in ("AUXILIAR", "LIQUIDADOR", "RESPONSABLE")]
        if not any(r and r not in imp.IDS_EXCLUIDOS for r in roles):
            res.append(issue)
    return res

def armar_resumen_impuestos(issues, users_map, hoy, redmine_url):
    """Devuelve (cuerpo_html, cantidad_de_peticiones) o (None, 0) si hoy no hay nada que resumir.
    Sólo entra lo que hoy les llegó a los empleados (vencimiento en 2 días hábiles):
    peticiones viejas que quedaron abiertas (ej. de julio, estando en octubre) nunca
    aparecen, porque su fecha de aviso ya pasó."""
    fuera = FUERA_DEL_EQUIPO_R2["impuestos"]
    notificaciones = imp.process_redmine_data(issues, hoy)
    por_peticion = invertir_por_peticion(notificaciones, fuera)
    sin_resp = sin_responsables_impuestos(issues, hoy)
    if not por_peticion and not sin_resp:
        return None, 0

    issues_por_id = {i.get("id"): i for i in issues}
    orden_tipos = list(imp.REGLAS_NOTIFICACION.keys())

    html = tabla_carga_por_persona(solo_equipo(notificaciones, fuera), users_map, orden_tipos)

    tipos = sorted({p["tipo"] for p in por_peticion.values()}, key=orden_tipos.index)
    for tipo in tipos:
        html += f'<h2 style="color: {COLOR}; font-size: 17px; margin: 30px 0 4px 0; padding-top: 16px; border-top: 2px solid {COLOR};">{tipo}</h2>'
        claves = sorted({p["clave"] for p in por_peticion.values() if p["tipo"] == tipo}, key=lambda c: (c[1], c[0]))
        for periodo, venc in claves:
            html += (f'<p style="margin: 12px 0 6px 0; font-size: 13px; color: #555555;">Período: <strong>{periodo}</strong> | '
                     f'Vencimiento: <strong>{imp.formatear_fecha(venc)}</strong></p>')
            filas = []
            peticiones = sorted(
                ((iid, p) for iid, p in por_peticion.items() if p["tipo"] == tipo and p["clave"] == (periodo, venc)),
                key=lambda x: x[1]["empresa"])
            for issue_id, p in peticiones:
                subestado = campos_de(issues_por_id.get(issue_id, {})).get(CAMPO_SUBESTADO_IMPUESTOS, "")
                por_rol = {rol: [] for rol in ROLES_IMPUESTOS}
                for rol, persona_id in p["equipo"]:
                    por_rol.setdefault(rol, []).append(nombre_legible(users_map, persona_id))
                filas.append([
                    link_peticion(redmine_url, issue_id),
                    f'<strong>{p["empresa"]}</strong>',
                    etapa_legible(subestado),
                    *[", ".join(por_rol[rol]) or "—" for rol in ROLES_IMPUESTOS],
                ])
            html += tabla(["#", "Empresa", "Etapa", *ROLES_IMPUESTOS], filas)

    if sin_resp:
        html += seccion_alerta("Peticiones que hoy no le llegaron a nadie (sin Auxiliar, Liquidador ni Responsable asignado)")
        filas = []
        for issue in sorted(sin_resp, key=lambda i: i.get("project", {}).get("name", "")):
            campos = campos_de(issue)
            filas.append([
                link_peticion(redmine_url, issue.get("id")),
                f'<strong>{issue.get("project", {}).get("name", "").split(" / ")[0].strip()}</strong>',
                issue.get("tracker", {}).get("name", ""),
                imp.formatear_fecha(campos.get("VENCIMIENTO DD. JJ.", "")),
            ])
        html += tabla(["#", "Empresa", "Tipo", "Vencimiento"], filas)

    return html, len(por_peticion) + len(sin_resp)

def etapa_legible(subestado):
    """'LIQUIDACIÓN DEL IMPUESTO' -> 'Liquidación del impuesto'; vacío o '-' -> '—'."""
    if not subestado or subestado.strip() == "-":
        return '<span style="color: #aaaaaa;">—</span>'
    return subestado.strip().capitalize().replace("ddjj", "DDJJ")

# ==========================================
# AUDITORÍA
# ==========================================

def es_dia_de_envio_auditoria(hoy):
    """Mismo calendario que main_auditoria: el día de corte de cada mes (o las fechas
    extra), salvo que se fuerce con FORZAR_ENVIO_R2_AUDITORIA=1."""
    if os.getenv("FORZAR_ENVIO_R2_AUDITORIA", "").strip() == "1":
        return True
    return hoy == aud.dia_envio_efectivo(hoy.year, hoy.month) or hoy in aud.FECHAS_ENVIO_EXTRA

def armar_resumen_auditoria(issues, users_map, hoy, redmine_url):
    """Devuelve (cuerpo_html, cantidad_de_peticiones) o (None, 0) si no hay nada que resumir."""
    fuera = FUERA_DEL_EQUIPO_R2["auditoria"]
    notificaciones = aud.process_redmine_auditoria(issues, hoy)
    por_peticion = invertir_por_peticion(notificaciones, fuera)
    if not por_peticion:
        return None, 0

    orden_tipos = aud.TRACKERS_AUDITORIA
    orden_roles = list(aud.ROLES_AUDITORIA.values())
    html = tabla_carga_por_persona(solo_equipo(notificaciones, fuera), users_map, orden_tipos)

    tipos = sorted({p["tipo"] for p in por_peticion.values()}, key=orden_tipos.index)
    for tipo in tipos:
        html += f'<h2 style="color: {COLOR}; font-size: 17px; margin: 30px 0 10px 0; padding-top: 16px; border-top: 2px solid {COLOR};">{tipo}</h2>'
        filas = []
        peticiones = sorted(
            ((iid, p) for iid, p in por_peticion.items() if p["tipo"] == tipo),
            key=lambda x: (x[1]["clave"], x[1]["empresa"]))
        for issue_id, p in peticiones:
            # En auditoría hay 5 roles posibles: en vez de 5 columnas casi vacías,
            # una sola columna "Equipo" con cada persona y su(s) rol(es).
            equipo = sorted(p["equipo"], key=lambda x: min(
                (orden_roles.index(r) for r in orden_roles if r in x[0]), default=len(orden_roles)))
            texto_equipo = "<br>".join(
                f'{nombre_legible(users_map, persona_id)} <span style="color: #888888;">({rol})</span>'
                for rol, persona_id in equipo)
            filas.append([
                link_peticion(redmine_url, issue_id),
                f'<strong>{p["empresa"]}</strong><br><span style="color: #777777;">{p["asunto"]}</span>',
                fecha_corta(p["clave"]),
                texto_equipo,
            ])
        html += tabla(["#", "Empresa / asunto", "Cierre OT", "Equipo"], filas)

    return html, len(por_peticion)

# ==========================================
# HTML
# ==========================================

def fecha_corta(fecha_str):
    """'2026-11-20' -> '20/11/2026' (en tabla, la fecha larga se parte en dos renglones)."""
    try:
        return datetime.strptime(fecha_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return fecha_str

def link_peticion(redmine_url, issue_id):
    return f"<a href='{redmine_url}issues/{issue_id}' style='color: {COLOR}; text-decoration: none; font-weight: bold;'>#{issue_id}</a>"

def tabla(encabezados, filas):
    ths = "".join(f'<th style="{ESTILO_TH}">{h}</th>' for h in encabezados)
    trs = ""
    for i, fila in enumerate(filas):
        fondo = "#ffffff" if i % 2 == 0 else "#faf5f9"
        trs += f'<tr style="background-color: {fondo};">' + "".join(f'<td style="{ESTILO_TD}">{c}</td>' for c in fila) + "</tr>"
    return (f'<table role="presentation" cellspacing="0" cellpadding="0" '
            f'style="width: 100%; border-collapse: collapse; margin: 0 0 14px 0;">'
            f'<tr>{ths}</tr>{trs}</table>')

def seccion_alerta(titulo):
    return (f'<h2 style="color: #b5651d; font-size: 16px; margin: 30px 0 8px 0; padding-top: 16px; '
            f'border-top: 2px solid #e8c9a8;">&#9888; {titulo}</h2>')

def tabla_carga_por_persona(notificaciones, users_map, orden_tipos):
    """Primera tabla del correo: una fila por persona con cuántas peticiones tiene
    hoy en el reporte, abierto por tipo. Es la vista rápida de "quién está con qué"."""
    tipos = sorted({t for por_tipo in notificaciones.values() for t in por_tipo}, key=orden_tipos.index)
    filas = []
    for persona_id, por_tipo in notificaciones.items():
        por_tipo_cant = {t: len({tarea["issue_id"] for g in por_tipo.get(t, {}).values() for tarea in g}) for t in tipos}
        total = sum(por_tipo_cant.values())
        filas.append((total, nombre_legible(users_map, persona_id), por_tipo_cant))
    filas.sort(key=lambda x: (-x[0], x[1]))
    html = f'<h2 style="color: {COLOR}; font-size: 17px; margin: 10px 0 10px 0;">Carga por persona</h2>'
    html += tabla(
        ["Persona", *[TITULOS_CORTOS.get(t, t) for t in tipos], "Total"],
        [[n, *[str(c[t]) if c[t] else '<span style="color: #cccccc;">·</span>' for t in tipos], f"<strong>{total}</strong>"]
         for total, n, c in filas])
    return html

def armar_cuerpo(nombre_r2, cuerpo_sector, cantidad, texto_intro):
    # Sin cartel de modo prueba (pedido del 06/10/2026): el correo de prueba se ve
    # igual al de producción. A quién le llegaría figura en el resumen de ejecución.
    nombre_pila = nombre_r2.split(" ")[0] if nombre_r2 else ""
    html = (f'<p style="font-size: 15px; line-height: 1.6; margin: 0 0 20px 0;"><strong>Hola {nombre_pila},</strong><br>'
             f'{texto_intro} Son <strong>{cantidad}</strong> peticiones; cada una aparece una sola vez, '
             f'con todas las personas que intervienen.</p>')
    return html + cuerpo_sector

def armar_plantilla(cuerpo_html):
    logo_html = f'<img src="{imp.LOGO_BASE64}" alt="Estudio Rivarossa" style="max-width: 280px; height: auto; margin-bottom: 15px;">' if imp.LOGO_BASE64 else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light">
    <meta name="supported-color-schemes" content="light">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.5; color: #333; background-color: #f5f5f5; margin: 0; padding: 0; }}
        .container {{ max-width: 960px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12); }}
        .header {{ background-color: #A75296; background: linear-gradient(135deg, #A75296 0%, #8B3D7C 100%); color: #ffffff !important; padding: 35px 20px; text-align: center; }}
        .logo-container {{ display: flex; justify-content: center; }}
        .logo-container img {{ filter: brightness(1.15) drop-shadow(0 2px 4px rgba(0,0,0,0.1)); }}
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
            <p>Este reporte fue generado de manera automática. &copy; Estudio Rivarossa</p>
        </div>
    </div>
</body>
</html>"""

# ==========================================
# ARMADO Y ENVÍO
# ==========================================

def preparar_resumenes(issues, users_map, hoy, redmine_url, sectores):
    """Devuelve [(sector, nombre_r2, correo_destino, etiqueta, asunto, html, cantidad)]."""
    fecha_txt = imp.formatear_fecha(hoy.isoformat())
    definiciones = []
    if "impuestos" in sectores:
        cuerpo, cant = armar_resumen_impuestos(issues, users_map, hoy, redmine_url)
        definiciones.append(("impuestos", "Impuestos", cuerpo, cant,
                             f"Este es el resumen de los vencimientos que hoy ({fecha_txt}) le llegaron al equipo de Impuestos."))
    if "auditoria" in sectores:
        if es_dia_de_envio_auditoria(hoy):
            cuerpo, cant = armar_resumen_auditoria(issues, users_map, hoy, redmine_url)
            definiciones.append(("auditoria", "Auditoría", cuerpo, cant,
                                 f"Este es el resumen del reporte mensual de auditoría que hoy ({fecha_txt}) le llegó al equipo."))
        else:
            print(f"[INFO] Hoy ({hoy}) no es día de envío del reporte de auditoría: no se arma su resumen R2.")

    resumenes = []
    for sector, titulo, cuerpo, cant, intro in definiciones:
        if cuerpo is None:
            print(f"[INFO] {titulo}: no hay peticiones para resumir hoy.")
            continue
        r2_id = R2_POR_SECTOR[sector]
        nombre_r2 = nombre_legible(users_map, r2_id)
        correo_r2 = users_map.get(r2_id, {}).get("correo", "")
        if REDIRIGIR_A_ADMIN_R2:
            destino, etiqueta = CORREO_ADMIN_R2, "[REDIRIGIDO A ADMIN]"
        elif correo_r2:
            destino, etiqueta = correo_r2, "[PRODUCCIÓN]"
        else:
            destino, etiqueta = CORREO_ADMIN_R2, "[SIN CORREO -> ADMIN]"
        html = armar_plantilla(armar_cuerpo(nombre_r2, cuerpo, cant, intro))
        asunto = f"Resumen del equipo de {titulo} - {hoy.strftime('%d/%m/%Y')}"
        resumenes.append((sector, nombre_r2, destino, etiqueta, asunto, html, cant))
    return resumenes

def enviar_resumenes(resumenes, smtp_server, smtp_port, smtp_user, access_token, from_email):
    enviados = 0
    with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
        server.starttls()
        imp.autenticar_smtp_oauth(server, smtp_user, access_token)
        for sector, nombre_r2, destino, etiqueta, asunto, html, cant in resumenes:
            msg = EmailMessage()
            msg['Subject'] = asunto
            msg['From'] = from_email
            msg['To'] = destino
            msg.set_content("Este correo requiere un cliente que soporte HTML.")
            msg.add_alternative(html, subtype='html')
            try:
                server.send_message(msg)
                print(f"[OK] {etiqueta} Resumen {sector} para: {nombre_r2} -> Enviado a: {destino}")
                enviados += 1
            except Exception as e:
                print(f"[ERROR] Error al enviar el resumen {sector} a {nombre_r2}: {e}")
    if resumenes and enviados == 0:
        raise RuntimeError("Había resúmenes para mandar pero no se pudo enviar ninguno (ver errores arriba).")
    return enviados

def enviar_resumen_ejecucion(estado, detalle_lineas, smtp_server, smtp_port, smtp_user, access_token, from_email):
    """Correo de estado a admin, se manda siempre (éxito o error) para que un fallo no sea silencioso."""
    msg = EmailMessage()
    msg['Subject'] = f"[Agente Resumen R2] Ejecución {estado} - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    msg['From'] = from_email
    msg['To'] = CORREO_ADMIN_R2
    msg.set_content("\n".join(detalle_lineas))
    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            imp.autenticar_smtp_oauth(server, smtp_user, access_token)
            server.send_message(msg)
        print(f"[OK] Resumen de ejecución enviado a {CORREO_ADMIN_R2}")
    except Exception as e:
        print(f"[ERROR] No se pudo enviar el resumen de ejecución a {CORREO_ADMIN_R2}: {e}")

def ya_hubo_envio_exitoso_hoy():
    """Mismo chequeo que main_impuestos.ya_hubo_envio_exitoso_hoy, pero sobre el
    workflow de este resumen: evita que los disparos de respaldo dupliquen el envío."""
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    run_id_actual = os.getenv("GITHUB_RUN_ID")
    if not repo or not token:
        return False
    try:
        url_api = f"https://api.github.com/repos/{repo}/actions/workflows/resumen-r2.yml/runs"
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        hoy = datetime.now().date().isoformat()
        resp = requests.get(url_api, headers=headers, timeout=10,
                            params={"status": "success", "created": f">={hoy}", "per_page": 10})
        resp.raise_for_status()
        return any(str(r.get("id")) != str(run_id_actual) for r in resp.json().get("workflow_runs", []))
    except Exception as e:
        print(f"[WARN] No se pudo chequear corridas previas de hoy ({e}); se continúa igual.")
        return False

if __name__ == "__main__":
    # SOLO_VISTA_PREVIA=1: no manda nada, guarda los HTML en ./vista_previa_r2/ para revisarlos.
    # SECTORES_R2=impuestos,auditoria: permite correr sólo uno de los dos.
    solo_vista_previa = os.getenv("SOLO_VISTA_PREVIA") == "1"
    sectores = {s.strip() for s in os.getenv("SECTORES_R2", "impuestos,auditoria").split(",") if s.strip()}
    hoy = fecha_de_hoy()
    print(f"Fecha de proceso: {hoy.isoformat()}" + (" (simulada)" if os.getenv("FECHA_SIMULADA") else ""))

    if not solo_vista_previa and ya_hubo_envio_exitoso_hoy():
        print("Ya hubo una ejecución exitosa hoy — se omite este disparo (de respaldo) para no duplicar.")
        raise SystemExit(0)

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
    resumenes = []
    enviados = 0
    access_token = None

    try:
        with requests.Session() as session:
            mapa_usuarios = imp.fetch_redmine_users(session, url, redmine_key)
            peticiones = fetch_redmine_issues_estricto(session, url, redmine_key)
        total_peticiones = len(peticiones)

        resumenes = preparar_resumenes(peticiones, mapa_usuarios, hoy, url, sectores)

        if solo_vista_previa:
            carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vista_previa_r2")
            os.makedirs(carpeta, exist_ok=True)
            for sector, nombre_r2, destino, etiqueta, asunto, html, cant in resumenes:
                archivo = os.path.join(carpeta, f"{hoy.isoformat()}_{sector}.html")
                with open(archivo, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"[VISTA PREVIA] {etiqueta} {sector}: {nombre_r2} -> {destino} | {cant} peticiones | {archivo}")
        elif resumenes:
            access_token = imp.obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)
            enviados = enviar_resumenes(resumenes, smtp_server, smtp_port, smtp_user, access_token, from_email)
        else:
            print("No hay resúmenes R2 para mandar hoy.")

    except Exception as e:
        estado = "CON ERROR"
        error_texto = str(e)
        print(f"[ERROR] Ejecución interrumpida: {e}")

    finally:
        detalle = [
            f"Fecha de proceso: {hoy.isoformat()}",
            f"Modo prueba (todo a admin): {'SÍ' if REDIRIGIR_A_ADMIN_R2 else 'NO'}",
            f"Peticiones descargadas de Redmine: {total_peticiones}",
            f"Resúmenes armados: {len(resumenes)}"
            + "".join(f"\n  - {s}: {n} -> {d} ({c} peticiones)" for s, n, d, _, _, _, c in resumenes),
            f"Resúmenes enviados con éxito: {enviados}",
        ]
        if error_texto:
            detalle += ["", f"Error: {error_texto}"]

        if solo_vista_previa:
            print("\n--- Resumen (no enviado, vista previa) ---\n" + "\n".join(detalle))
        else:
            if access_token is None:
                try:
                    access_token = imp.obtener_access_token_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token)
                except Exception as e:
                    print(f"[ERROR] No se pudo obtener un access token para mandar el resumen: {e}")
            if access_token:
                enviar_resumen_ejecucion(estado, detalle, smtp_server, smtp_port, smtp_user, access_token, from_email)

    if estado == "CON ERROR":
        raise SystemExit(1)
