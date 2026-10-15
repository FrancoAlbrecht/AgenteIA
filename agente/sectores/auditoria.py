from dataclasses import dataclass, field
from datetime import date
from agente import entorno, fechas, redmine, reporte, texto
from agente.corrida import Corrida
from agente.destinatarios import IDS_EXCLUIDOS

WORKFLOW = "reporte-auditoria.yml"

REDIRIGIR_A_ADMIN = False
REDIRIGIDOS_A_ADMIN = frozenset()
IDS_EXCLUIDOS_AUDITORIA = IDS_EXCLUIDOS | {
    "243",  # Berger, David - no debe recibir el reporte de auditoría (pedido 2026-09-23)
}

TRACKERS = ["CyA Balance", "CyA Corte", "CyA Auditoria"]
ESTADO_A_AVISAR = "Pendiente"
CAMPO_FECHA_CIERRE = "fecha de cierre ot"  # nombre normalizado (ver texto.normalizar)

ROLES = {
    "auditor 1": "Auditor 1",
    "auditor 2": "Auditor 2",
    "aux/junior": "Aux/Junior",
    "referente": "Referente",
    "control": "Control",
}

DIA_CORTE_MENSUAL = 21

FECHAS_ENVIO_EXTRA = {
    date(2026, 9, 24),  # primer envío en producción, fuera del corte mensual
}

# Fuerza el envío en cualquier fecha (para pruebas manuales). ¡Envía de verdad!
VARIABLE_FORZAR_ENVIO = "FORZAR_ENVIO_AUDITORIA"


def fecha_de_envio(hoy):
    return date(hoy.year, hoy.month, DIA_CORTE_MENSUAL)


def es_dia_de_envio(hoy):
    return hoy == fecha_de_envio(hoy) or hoy in FECHAS_ENVIO_EXTRA


def agrupar_por_persona(issues, hoy):
    fin_ventana = fechas.fin_del_mes_siguiente(hoy)
    notificaciones = {}
    sin_fecha = vencidas = fuera_de_rango = 0

    for issue in issues:
        tipo = redmine.tracker_de(issue)
        if tipo not in TRACKERS or redmine.estado_de(issue) != ESTADO_A_AVISAR:
            continue
        campos = redmine.campos_personalizados(issue)
        fecha_cierre_ot = campos.get(CAMPO_FECHA_CIERRE, "")
        roles_por_persona = {}
        for nombre, valor in campos.items():
            if nombre in ROLES and valor not in IDS_EXCLUIDOS_AUDITORIA:
                roles_por_persona.setdefault(valor, []).append(ROLES[nombre])

        fecha_cierre = fechas.parsear(fecha_cierre_ot)
        if fecha_cierre is None:
            sin_fecha += 1
            continue
        # Lo ya vencido se asume avisado en el reporte del mes que le correspondía.
        if fecha_cierre < hoy:
            vencidas += 1
            continue
        if fecha_cierre > fin_ventana:
            fuera_de_rango += 1
            continue

        for persona_id, roles in roles_por_persona.items():
            reporte.agregar_tarea(notificaciones, persona_id, tipo, fecha_cierre_ot,
                                  reporte.tarea(issue, texto.unir_roles(roles)))

    print(f"Filtrado por Fecha de cierre OT: {sin_fecha} sin fecha válida, "
          f"{vencidas} ya vencidas (antes de hoy, {hoy}), "
          f"{fuera_de_rango} fuera de la ventana (hasta: {fin_ventana}).")
    return notificaciones


def detectar_fuera_de_proyecto(issues):
    return [f"  #{issue.get('id')} - {redmine.proyecto_de(issue)} - {redmine.tracker_de(issue)} - {issue.get('subject', '')}"
            for issue in issues
            if redmine.tracker_de(issue) in TRACKERS and redmine.estado_de(issue) == ESTADO_A_AVISAR
            and "auditoria" not in texto.normalizar(redmine.proyecto_de(issue))]


def armar_html(nombre_real, datos_por_tipo, redmine_url):
    secciones = []
    for tipo in sorted(datos_por_tipo, key=reporte.orden_segun(TRACKERS)):
        secciones.append((tipo, [
            (f"Fecha de cierre OT: <strong>{fechas.formatear_larga(fecha)}</strong>", datos_por_tipo[tipo][fecha])
            for fecha in sorted(datos_por_tipo[tipo])
        ]))
    intro = ("Te dejamos el listado de peticiones de auditoría con cierre próximo que requieren "
             "tu atención, agrupadas por tipo de petición.")
    return reporte.armar_cuerpo(nombre_real, intro, secciones, redmine_url)


@dataclass
class Informe:
    peticiones: int = 0
    personas: int = 0
    enviados: int = 0
    fuera_de_proyecto: list = field(default_factory=list)

    def lineas(self):
        lineas = [f"Peticiones descargadas de Redmine: {self.peticiones}",
                  f"Personas con peticiones de auditoría a notificar: {self.personas}",
                  f"Correos enviados con éxito: {self.enviados}"]
        if self.fuera_de_proyecto:
            lineas += ["", "Peticiones de auditoría cargadas fuera del subproyecto de Auditoría "
                           "(igual se incluyeron en el reporte; conviene moverlas en Redmine):"]
            lineas += self.fuera_de_proyecto
        return lineas


def procesar(corrida, informe, hoy):
    with corrida.redmine() as cliente:
        usuarios = cliente.usuarios_o_respaldo(respaldo={})
        issues = cliente.peticiones_abiertas(estricto=False)
    informe.peticiones = len(issues)
    if not issues:
        print("No se descargaron peticiones desde Redmine.")
        return

    informe.fuera_de_proyecto = detectar_fuera_de_proyecto(issues)
    notificaciones = agrupar_por_persona(issues, hoy)
    informe.personas = len(notificaciones)
    if not notificaciones:
        print("El sistema no detectó peticiones de auditoría pendientes con cierre dentro de la ventana mensual.")
        return

    url = corrida.credenciales.redmine_url
    correos = reporte.preparar_correos(
        notificaciones, usuarios,
        armar_html=lambda nombre, _correo, datos: armar_html(nombre, datos, url),
        prefijo_asunto="Reporte de Auditoría", modo_prueba=REDIRIGIR_A_ADMIN,
        redirigidos=REDIRIGIDOS_A_ADMIN)
    informe.enviados = corrida.enviar(correos)


def main():
    hoy = fechas.hoy()
    forzar_envio = entorno.bandera_activa(VARIABLE_FORZAR_ENVIO)
    if not es_dia_de_envio(hoy) and not forzar_envio:
        print(f"[INFO] Hoy ({hoy}) no es el día de envío del reporte mensual de auditoría "
              f"(corresponde el {fecha_de_envio(hoy)}). No se envían correos.")
        return

    # El día de envío el workflow se dispara dos veces (cron-job.org temprano y el
    # "schedule" de GitHub, que llega horas tarde): la segunda no manda nada.
    corrida = Corrida("Auditoría", "auditoria")
    if not forzar_envio and corrida.ya_se_envio_hoy(WORKFLOW):
        return
    informe = Informe()
    corrida.ejecutar(lambda: procesar(corrida, informe, hoy))
    corrida.finalizar(informe.lineas())
