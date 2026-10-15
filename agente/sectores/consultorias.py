import html
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from agente import destinatarios, fechas, github_actions, plantilla, texto
from agente.correo import Correo
from agente.corrida import Corrida
from agente.destinatarios import IDS_EXCLUIDOS
from agente.plantilla import COLOR

WORKFLOW = "notificacion-consultorias.yml"

REDIRIGIR_A_ADMIN = False

REDIRIGIDOS_A_ADMIN = frozenset()

PROYECTOS_CONSULTORIA = [
    "federicocaglieris",  # CAGLIERIS FEDERICO
    "evelynsaires",       # SAIRES EVELYN
]

DOMINIO_INTERNO = "@rivarossa.com"

VENTANA_INICIAL = timedelta(hours=24)
VENTANA_MAXIMA = timedelta(days=7)

MAX_CARACTERES_TEXTO = 1500           # de la descripción / notas copiadas al correo
MAX_CARACTERES_ASUNTO_CONSULTA = 80   # del asunto de la consulta en el título del correo

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

def calcular_ventana():
    corridas = github_actions.inicio_de_esta_corrida_y_anteriores(WORKFLOW)
    if corridas is None:
        hasta = datetime.now(timezone.utc).replace(microsecond=0)
        if os.getenv("VENTANA_DESDE"):
            return fechas.parsear_iso_utc(os.getenv("VENTANA_DESDE")), hasta
        return hasta - timedelta(hours=float(os.getenv("VENTANA_HORAS", "24"))), hasta

    hasta, anteriores = corridas
    desde = max(anteriores) if anteriores else hasta - VENTANA_INICIAL
    if hasta - desde > VENTANA_MAXIMA:
        print(f"[WARN] La última corrida exitosa fue hace más de {VENTANA_MAXIMA.days} días; "
              f"se limita la ventana para no mandar avisos viejos.")
        desde = hasta - VENTANA_MAXIMA
    return desde, hasta

def peticiones_con_movimiento(cliente, desde):
    vistas, peticiones = set(), []
    filtro = ">=" + desde.strftime("%Y-%m-%dT%H:%M:%SZ")
    for proyecto in PROYECTOS_CONSULTORIA:
        cliente_consultoria = cliente.nombre_proyecto(proyecto)
        for resumen in cliente.peticiones(project_id=proyecto, status_id="*", updated_on=filtro):
            if resumen["id"] not in vistas:
                vistas.add(resumen["id"])
                issue = cliente.peticion_con_historial(resumen["id"])
                issue["_cliente"] = cliente_consultoria
                peticiones.append(issue)
    return peticiones


def novedades_de_peticion(issue, desde, hasta):
    eventos = []
    creada = fechas.parsear_iso_utc(issue["created_on"])
    if desde <= creada < hasta:
        eventos.append({
            "tipo": "nueva", "fecha": creada,
            "actor_id": str(issue.get("author", {}).get("id", "")),
            "actor": issue.get("author", {}).get("name", ""),
            "texto": issue.get("description") or "", "detalles": [],
        })
    for j in issue.get("journals", []):
        fecha = fechas.parsear_iso_utc(j["created_on"])
        if desde <= fecha < hasta:
            eventos.append({
                "tipo": "edicion", "fecha": fecha,
                "actor_id": str(j.get("user", {}).get("id", "")),
                "actor": j.get("user", {}).get("name", ""),
                "texto": j.get("notes") or "", "detalles": j.get("details", []),
            })
    return sorted(eventos, key=lambda e: e["fecha"])


def destinatarios_de_peticion(issue, cliente, usuarios):
    asignado = issue.get("assigned_to")
    if asignado:
        asignado_id = str(asignado["id"])
        if asignado_id in usuarios:
            return [asignado_id], f"asignada a {asignado.get('name', '')}"
        nombre_grupo, miembros = cliente.miembros_de_grupo(asignado_id)
        return miembros, f"asignada al grupo {nombre_grupo}"

    ids, nombres = [], []
    for grupo_id in cliente.grupos_del_proyecto(issue["project"]["id"]):
        nombre_grupo, miembros = cliente.miembros_de_grupo(grupo_id)
        nombres.append(nombre_grupo)
        ids += [m for m in miembros if m not in ids]
    if ids:
        return ids, f"sin asignar: se avisa al grupo {', '.join(nombres)}"
    return [], "sin asignar y el proyecto no tiene grupo de consultoría"

def armar_avisos(peticiones, cliente, usuarios, desde, hasta):
    avisos, alertas = {}, []
    for issue in peticiones:
        eventos = novedades_de_peticion(issue, desde, hasta)
        if not eventos:
            continue
        ids, motivo = destinatarios_de_peticion(issue, cliente, usuarios)
        if not ids:
            alertas.append(f"#{issue['id']}: tuvo novedades pero no hay a quién avisar ({motivo}).")
            continue
        for persona_id in ids:
            if persona_id in IDS_EXCLUIDOS:
                continue
            correo = usuarios.get(persona_id, {}).get("correo", "")
            if not correo.lower().endswith(DOMINIO_INTERNO):
                alertas.append(f"#{issue['id']}: no se avisa a {usuarios.get(persona_id, {}).get('nombre', persona_id)} "
                               f"porque su correo no es del estudio.")
                continue
            # Nadie recibe aviso de lo que hizo él mismo.
            ajenos = [e for e in eventos if e["actor_id"] != persona_id]
            if ajenos:
                avisos.setdefault(persona_id, []).append((issue, motivo, ajenos))
    return avisos, alertas

def _texto_html(texto_redmine):
    contenido = (texto_redmine or "").strip()
    if len(contenido) > MAX_CARACTERES_TEXTO:
        contenido = contenido[:MAX_CARACTERES_TEXTO].rstrip() + " […]"
    return html.escape(contenido).replace("\n", "<br>")


def nombre_usuario_o_grupo(id_texto, cliente, usuarios):
    if id_texto in usuarios:
        return usuarios[id_texto]["nombre"]
    try:
        return cliente.miembros_de_grupo(id_texto)[0]
    except Exception:
        return f"#{id_texto}"


def describir_cambio(detalle, cliente, usuarios):
    prop, nombre = detalle.get("property"), detalle.get("name", "")
    viejo, nuevo = detalle.get("old_value"), detalle.get("new_value")
    if prop == "attachment":
        return f"Adjuntó el archivo {nuevo}" if nuevo else f"Quitó el archivo {viejo}"
    if prop == "relation":
        return "Cambió las peticiones relacionadas"
    if prop == "cf":
        etiqueta = cliente.nombre_campo(nombre)
    elif prop == "attr":
        etiqueta = NOMBRES_ATRIBUTOS.get(nombre, nombre)
        if nombre == "description":
            return "Modificó la descripción"
        if nombre == "status_id":
            viejo, nuevo = [cliente.nombre_estado(v) if v else v for v in (viejo, nuevo)]
        elif nombre == "assigned_to_id":
            viejo, nuevo = [nombre_usuario_o_grupo(v, cliente, usuarios) if v else v for v in (viejo, nuevo)]
        elif nombre == "project_id":
            viejo, nuevo = [cliente.nombre_proyecto(v) if v else v for v in (viejo, nuevo)]
    else:
        etiqueta = nombre
    return f"{etiqueta}: {viejo or '(vacío)'} → {nuevo or '(vacío)'}"


def armar_html(nombre_real, items, cliente, usuarios, redmine_url):
    hay_nuevas = any(e["tipo"] == "nueva" for _, _, eventos in items for e in eventos)
    cuerpo = plantilla.saludo(
        html.escape(texto.nombre_de_pila(nombre_real)),
        f'{"Se cargaron o actualizaron" if hay_nuevas else "Se actualizaron"} consultas de clientes de consultoría '
        f'que te tocan atender:')

    for issue, motivo, eventos in items:
        link = plantilla.url_peticion(redmine_url, issue["id"])
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
                    <strong style="color: {COLOR};">{titulo}</strong> de {html.escape(e['actor'])} — {fechas.fecha_hora_art(e['fecha'])} hs
                </p>
            """
            if e["texto"].strip():
                cuerpo += f'<p style="margin: 0 0 6px 0; font-size: 13px; line-height: 1.5; color: #333333;">{_texto_html(e["texto"])}</p>'
            cambios = [describir_cambio(d, cliente, usuarios) for d in e["detalles"]]
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
    return plantilla.documento(cuerpo, ancho=760, pie="aviso")


def _nombre_corto_cliente(issue):
    cliente = (issue.get("_cliente") or issue.get("project", {}).get("name", "")).strip()
    return cliente.split()[0].title() if cliente else "Consultoría"


def asunto_del_aviso(items):
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


def preparar_correos(avisos, cliente, usuarios, redmine_url, hasta):
    correos = []
    for persona_id, items in avisos.items():
        nombre, correo_real = destinatarios.nombre_y_correo(usuarios, persona_id)
        destino, etiqueta = destinatarios.resolver_destino(nombre, correo_real, REDIRIGIR_A_ADMIN, REDIRIGIDOS_A_ADMIN)
        # El repo es público: en el log van sólo números de petición, nunca el contenido.
        ids = ", ".join(f"#{issue['id']}" for issue, _, _ in items)
        correos.append(Correo(
            nombre=nombre, destino=destino, etiqueta=etiqueta, asunto=asunto_del_aviso(items),
            html=armar_html(nombre, items, cliente, usuarios, redmine_url),
            archivo=f"{hasta.strftime('%Y%m%d_%H%M')}_{persona_id}", detalle=f" ({ids})",
        ))
    return correos

@dataclass
class Informe:
    desde: datetime = None
    hasta: datetime = None
    revisadas: int = 0
    correos: list = field(default_factory=list)
    enviados: int = 0
    alertas: list = field(default_factory=list)

    def lineas(self):
        ventana = " a ".join(fechas.fecha_hora_art(m) if m else "?" for m in (self.desde, self.hasta))
        lineas = [f"Ventana revisada: {ventana} (hora argentina)",
                  f"Modo prueba (todo a admin): {'SÍ' if REDIRIGIR_A_ADMIN else 'NO'}",
                  f"Peticiones de consultoría con movimiento: {self.revisadas}",
                  f"Avisos armados: {len(self.correos)}" + "".join(f"\n  - {c.nombre}{c.detalle}" for c in self.correos),
                  f"Avisos enviados con éxito: {self.enviados}"]
        if self.alertas:
            lineas += ["", "Alertas:"] + [f"  {a}" for a in self.alertas]
        return lineas


def procesar(corrida, informe):
    informe.desde, informe.hasta = calcular_ventana()
    print(f"Ventana: {fechas.fecha_hora_art(informe.desde)} a {fechas.fecha_hora_art(informe.hasta)} (hora argentina)")

    url = corrida.credenciales.redmine_url
    with corrida.redmine() as cliente:
        usuarios = cliente.usuarios()
        peticiones = peticiones_con_movimiento(cliente, informe.desde)
        informe.revisadas = len(peticiones)
        print(f"Peticiones de consultoría actualizadas en la ventana: {informe.revisadas}")
        avisos, informe.alertas = armar_avisos(peticiones, cliente, usuarios, informe.desde, informe.hasta)
        informe.correos = preparar_correos(avisos, cliente, usuarios, url, informe.hasta)

    if not informe.correos:
        print("No hay novedades de consultoría para avisar.")
    informe.enviados = corrida.enviar(informe.correos)


def main():
    corrida = Corrida("Consultorías", "consultorias")
    informe = Informe()
    corrida.ejecutar(lambda: procesar(corrida, informe))
    if informe.alertas:
        print(f"[WARN] {len(informe.alertas)} alerta(s), ver resumen de ejecución.")
    # Corre muchas veces por día: el resumen sólo se manda si hubo avisos, alertas o error.
    corrida.finalizar(informe.lineas(), enviar_resumen=bool(informe.correos or informe.alertas))
