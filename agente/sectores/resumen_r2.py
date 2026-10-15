import os
from dataclasses import dataclass, field
from agente import destinatarios, entorno, fechas, plantilla, redmine, texto
from agente.correo import Correo
from agente.corrida import Corrida
from agente.plantilla import COLOR, link_peticion
from agente.sectores import auditoria, impuestos

WORKFLOW = "resumen-r2.yml"

REDIRIGIR_A_ADMIN = False

SECTORES_ACTIVOS = {"impuestos"}

VARIABLE_FORZAR_AUDITORIA = "FORZAR_ENVIO_R2_AUDITORIA"

R2_POR_SECTOR = {
    "impuestos": "542",  # Margaria, Cintia
    "auditoria": "717",  # Depetris, Matías
}

MOSTRAR_CARGA_POR_PERSONA = {
    "impuestos": False,  # Cintia no la quiere (pedido del 06/10/2026)
    "auditoria": True,   # a confirmar con Matías
}

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

ESTILO_TH = f"text-align: left; padding: 6px 8px; background-color: {COLOR}; color: #ffffff; font-size: 12px; font-weight: 600;"
ESTILO_TD = "padding: 6px 8px; border-bottom: 1px solid #eeeeee; font-size: 12px; vertical-align: top;"

def nombre_legible(usuarios, persona_id):
    return texto.nombre_y_apellido(destinatarios.nombre_de(usuarios, persona_id))


def invertir_por_peticion(notificaciones, fuera_del_equipo):
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
    return {p: datos for p, datos in notificaciones.items() if p not in fuera_del_equipo}

def tabla(encabezados, filas):
    ths = "".join(f'<th style="{ESTILO_TH}">{h}</th>' for h in encabezados)
    trs = ""
    for i, fila in enumerate(filas):
        fondo = "#ffffff" if i % 2 == 0 else "#faf5f9"
        trs += f'<tr style="background-color: {fondo};">' + "".join(f'<td style="{ESTILO_TD}">{c}</td>' for c in fila) + "</tr>"
    return (f'<table role="presentation" cellspacing="0" cellpadding="0" '
            f'style="width: 100%; border-collapse: collapse; margin: 0 0 14px 0;">'
            f'<tr>{ths}</tr>{trs}</table>')


def titulo_tipo(tipo, margen_inferior):
    return (f'<h2 style="color: {COLOR}; font-size: 17px; margin: 30px 0 {margen_inferior}px 0; '
            f'padding-top: 16px; border-top: 2px solid {COLOR};">{tipo}</h2>')


def seccion_alerta(titulo):
    return (f'<h2 style="color: #b5651d; font-size: 16px; margin: 30px 0 8px 0; padding-top: 16px; '
            f'border-top: 2px solid #e8c9a8;">&#9888; {titulo}</h2>')


def etapa_legible(subestado):
    if not subestado or subestado.strip() == "-":
        return '<span style="color: #aaaaaa;">—</span>'
    return subestado.strip().capitalize().replace("ddjj", "DDJJ")


def tabla_carga_por_persona(notificaciones, usuarios, orden_tipos):
    tipos = sorted({t for por_tipo in notificaciones.values() for t in por_tipo}, key=orden_tipos.index)
    filas = []
    for persona_id, por_tipo in notificaciones.items():
        cantidades = {t: len({tarea["issue_id"] for g in por_tipo.get(t, {}).values() for tarea in g}) for t in tipos}
        filas.append((sum(cantidades.values()), nombre_legible(usuarios, persona_id), cantidades))
    filas.sort(key=lambda x: (-x[0], x[1]))
    html = f'<h2 style="color: {COLOR}; font-size: 17px; margin: 10px 0 10px 0;">Carga por persona</h2>'
    return html + tabla(
        ["Persona", *[TITULOS_CORTOS.get(t, t) for t in tipos], "Total"],
        [[n, *[str(c[t]) if c[t] else '<span style="color: #cccccc;">·</span>' for t in tipos], f"<strong>{total}</strong>"]
         for total, n, c in filas])

def armar_resumen_impuestos(issues, usuarios, hoy, redmine_url):
    fuera = FUERA_DEL_EQUIPO_R2["impuestos"]
    peticiones = impuestos.peticiones_del_dia(issues, hoy)
    notificaciones = impuestos.agrupar_por_persona(peticiones)
    por_peticion = invertir_por_peticion(notificaciones, fuera)
    sin_responsables = [p for p in peticiones if not p.roles]
    if not por_peticion and not sin_responsables:
        return None, 0

    issues_por_id = {i.get("id"): i for i in issues}
    orden_tipos = list(impuestos.REGLAS_NOTIFICACION)
    roles = list(impuestos.ROLES.values())

    html = ""
    if MOSTRAR_CARGA_POR_PERSONA["impuestos"]:
        html += tabla_carga_por_persona(solo_equipo(notificaciones, fuera), usuarios, orden_tipos)

    for tipo in sorted({p["tipo"] for p in por_peticion.values()}, key=orden_tipos.index):
        html += titulo_tipo(tipo, margen_inferior=4)
        claves = sorted({p["clave"] for p in por_peticion.values() if p["tipo"] == tipo}, key=lambda c: (c[1], c[0]))
        for periodo, vencimiento in claves:
            html += (f'<p style="margin: 12px 0 6px 0; font-size: 13px; color: #555555;">Período: <strong>{periodo}</strong> | '
                     f'Vencimiento: <strong>{fechas.formatear_larga(vencimiento)}</strong></p>')
            filas = []
            del_grupo = sorted(((iid, p) for iid, p in por_peticion.items()
                                if p["tipo"] == tipo and p["clave"] == (periodo, vencimiento)),
                               key=lambda x: x[1]["empresa"])
            for issue_id, p in del_grupo:
                subestado = redmine.campos_personalizados(issues_por_id.get(issue_id, {})).get(impuestos.CAMPO_SUBESTADO, "")
                por_rol = {rol: [] for rol in roles}
                for rol, persona_id in p["equipo"]:
                    por_rol.setdefault(rol, []).append(nombre_legible(usuarios, persona_id))
                filas.append([link_peticion(redmine_url, issue_id), f'<strong>{p["empresa"]}</strong>',
                              etapa_legible(subestado), *[", ".join(por_rol[rol]) or "—" for rol in roles]])
            html += tabla(["#", "Empresa", "Etapa", *roles], filas)

    if sin_responsables:
        html += seccion_alerta("Peticiones que hoy no le llegaron a nadie (sin Auxiliar, Liquidador ni Responsable asignado)")
        filas = [[link_peticion(redmine_url, p.issue.get("id")), f'<strong>{redmine.empresa_de(p.issue)}</strong>',
                  p.tipo, fechas.formatear_larga(p.vencimiento)]
                 for p in sorted(sin_responsables, key=lambda p: redmine.proyecto_de(p.issue))]
        html += tabla(["#", "Empresa", "Tipo", "Vencimiento"], filas)

    return html, len(por_peticion) + len(sin_responsables)


def armar_resumen_auditoria(issues, usuarios, hoy, redmine_url):
    fuera = FUERA_DEL_EQUIPO_R2["auditoria"]
    notificaciones = auditoria.agrupar_por_persona(issues, hoy)
    por_peticion = invertir_por_peticion(notificaciones, fuera)
    if not por_peticion:
        return None, 0

    orden_roles = list(auditoria.ROLES.values())
    html = ""
    if MOSTRAR_CARGA_POR_PERSONA["auditoria"]:
        html += tabla_carga_por_persona(solo_equipo(notificaciones, fuera), usuarios, auditoria.TRACKERS)

    for tipo in sorted({p["tipo"] for p in por_peticion.values()}, key=auditoria.TRACKERS.index):
        html += titulo_tipo(tipo, margen_inferior=10)
        filas = []
        del_tipo = sorted(((iid, p) for iid, p in por_peticion.items() if p["tipo"] == tipo),
                          key=lambda x: (x[1]["clave"], x[1]["empresa"]))
        for issue_id, p in del_tipo:
            # En auditoría hay 5 roles posibles: en vez de 5 columnas casi vacías, una
            # sola columna "Equipo" con cada persona y su(s) rol(es).
            equipo = sorted(p["equipo"], key=lambda x: min(
                (orden_roles.index(r) for r in orden_roles if r in x[0]), default=len(orden_roles)))
            texto_equipo = "<br>".join(f'{nombre_legible(usuarios, persona_id)} <span style="color: #888888;">({rol})</span>'
                                       for rol, persona_id in equipo)
            filas.append([link_peticion(redmine_url, issue_id),
                          f'<strong>{p["empresa"]}</strong><br><span style="color: #777777;">{p["asunto"]}</span>',
                          fechas.formatear_corta(p["clave"]), texto_equipo])
        html += tabla(["#", "Empresa / asunto", "Cierre OT", "Equipo"], filas)

    return html, len(por_peticion)


def es_dia_de_resumen_auditoria(hoy):
    return entorno.bandera_activa(VARIABLE_FORZAR_AUDITORIA) or auditoria.es_dia_de_envio(hoy)

def preparar_resumenes(issues, usuarios, hoy, redmine_url, sectores):
    fecha_txt = fechas.formatear_larga(hoy.isoformat())
    definiciones = []
    if "impuestos" in sectores:
        cuerpo, cantidad = armar_resumen_impuestos(issues, usuarios, hoy, redmine_url)
        definiciones.append(("impuestos", "Impuestos", cuerpo, cantidad,
                             f"Este es el resumen de los vencimientos que hoy ({fecha_txt}) le llegaron al equipo de Impuestos."))
    if "auditoria" in sectores:
        if es_dia_de_resumen_auditoria(hoy):
            cuerpo, cantidad = armar_resumen_auditoria(issues, usuarios, hoy, redmine_url)
            definiciones.append(("auditoria", "Auditoría", cuerpo, cantidad,
                                 f"Este es el resumen del reporte mensual de auditoría que hoy ({fecha_txt}) le llegó al equipo."))
        else:
            print(f"[INFO] Hoy ({hoy}) no es día de envío del reporte de auditoría: no se arma su resumen R2.")

    resumenes = []
    for sector, titulo, cuerpo, cantidad, intro in definiciones:
        if cuerpo is None:
            print(f"[INFO] {titulo}: no hay peticiones para resumir hoy.")
            continue
        r2_id = R2_POR_SECTOR[sector]
        nombre_r2 = nombre_legible(usuarios, r2_id)
        correo_r2 = usuarios.get(r2_id, {}).get("correo", "")
        destino, etiqueta = destinatarios.resolver_destino(nombre_r2, correo_r2, REDIRIGIR_A_ADMIN, frozenset())
        if not destino:
            destino, etiqueta = destinatarios.CORREO_ADMIN, "[SIN CORREO -> ADMIN]"
        # Sin cartel de modo prueba (pedido del 06/10/2026): a quién le llegaría figura
        # en el resumen de ejecución.
        saludo = plantilla.saludo(nombre_r2.split(" ")[0],
                                  f"{intro} Son <strong>{cantidad}</strong> peticiones; cada una aparece "
                                  f"una sola vez, con todas las personas que intervienen.")
        resumenes.append((sector, Correo(
            nombre=nombre_r2, destino=destino, etiqueta=etiqueta,
            asunto=f"Resumen del equipo de {titulo} - {hoy.strftime('%d/%m/%Y')}",
            html=plantilla.documento(saludo + cuerpo, ancho=960), archivo=f"{hoy.isoformat()}_{sector}",
            detalle=f" ({cantidad} peticiones)",
        )))
    return resumenes


@dataclass
class Informe:
    hoy: object
    peticiones: int = 0
    resumenes: list = field(default_factory=list)  # [(sector, Correo)]
    enviados: int = 0

    def lineas(self):
        return [f"Fecha de proceso: {self.hoy.isoformat()}",
                f"Modo prueba (todo a admin): {'SÍ' if REDIRIGIR_A_ADMIN else 'NO'}",
                f"Peticiones descargadas de Redmine: {self.peticiones}",
                f"Resúmenes armados: {len(self.resumenes)}"
                + "".join(f"\n  - {sector}: {c.nombre} -> {c.destino}{c.detalle}" for sector, c in self.resumenes),
                f"Resúmenes enviados con éxito: {self.enviados}"]


def procesar(corrida, informe, sectores):
    with corrida.redmine() as cliente:
        usuarios = cliente.usuarios_o_respaldo(destinatarios.USUARIOS_FALLBACK)
        # Estricto: un resumen con datos parciales le mostraría al R2 menos trabajo del real.
        issues = cliente.peticiones_abiertas(estricto=True)
    informe.peticiones = len(issues)
    informe.resumenes = preparar_resumenes(issues, usuarios, informe.hoy, corrida.credenciales.redmine_url, sectores)
    if not informe.resumenes:
        print("No hay resúmenes R2 para mandar hoy.")
    informe.enviados = corrida.enviar([c for _, c in informe.resumenes])


def main():
    sectores = ({s.strip() for s in os.getenv("SECTORES_R2").split(",") if s.strip()}
                if os.getenv("SECTORES_R2") else SECTORES_ACTIVOS)
    hoy = fechas.hoy()
    print(f"Fecha de proceso: {hoy.isoformat()}")
    corrida = Corrida("Resumen R2", "r2")
    if corrida.ya_se_envio_hoy(WORKFLOW):
        return
    informe = Informe(hoy)
    corrida.ejecutar(lambda: procesar(corrida, informe, sectores))
    corrida.finalizar(informe.lineas())
