from dataclasses import dataclass
from agente import fechas, redmine, reporte
from agente.corrida import Corrida
from agente.destinatarios import IDS_EXCLUIDOS, USUARIOS_FALLBACK

WORKFLOW = "reporte-vencimientos.yml"

REDIRIGIR_A_ADMIN = False

REGLAS_NOTIFICACION = {
    "Tributaria - II BB": 2,
    "Tributaria - DREI": 2,
    "Tributaria - CM": 2,
    "Tributaria - IVA": 2,
    "Tributaria - Sicore": 2,
    "Tributaria - Ag. Recaudación": 2,
}

ESTADO_A_AVISAR = "Pendiente"

CAMPO_VENCIMIENTO = "vencimiento dd. jj."
CAMPO_SUBESTADO = "subestado tributaria"

ROLES = {"auxiliar": "Auxiliar", "liquidador": "Liquidador", "responsable": "Responsable"}


@dataclass
class PeticionDelDia:
    issue: dict
    tipo: str
    periodo: str
    vencimiento: str
    roles: list  # [(persona_id, rol)], sin IDS_EXCLUIDOS; vacía si nadie la tiene asignada


def calcular_fecha_notificacion(fecha_vencimiento, dias_habiles_anticipacion):
    vencimiento = fechas.parsear(fecha_vencimiento)
    return fechas.restar_dias_habiles(vencimiento, dias_habiles_anticipacion) if vencimiento else None


def peticiones_del_dia(issues, hoy):
    resultado = []
    sin_fecha = fuera_de_rango = 0
    for issue in issues:
        tipo = redmine.tracker_de(issue)
        dias = REGLAS_NOTIFICACION.get(tipo)
        if (dias is None or "IMPUESTOS" not in redmine.proyecto_de(issue).upper()
                or redmine.estado_de(issue) != ESTADO_A_AVISAR):
            continue
        campos = redmine.campos_personalizados(issue)
        vencimiento = campos.get(CAMPO_VENCIMIENTO, "Sin Vencimiento")
        fecha_notificacion = calcular_fecha_notificacion(vencimiento, dias)
        if fecha_notificacion is None:
            sin_fecha += 1
            continue
        if fecha_notificacion != hoy:
            fuera_de_rango += 1
            continue
        roles = [(valor, ROLES[nombre]) for nombre, valor in campos.items()
                 if nombre in ROLES and valor not in IDS_EXCLUIDOS]
        resultado.append(PeticionDelDia(issue, tipo, redmine.periodo_de(campos), vencimiento, roles))

    print(f"Filtrado por vencimiento: {sin_fecha} sin fecha válida, "
          f"{fuera_de_rango} fuera de la ventana definida en REGLAS_NOTIFICACION.")
    return resultado


def agrupar_por_persona(peticiones):
    notificaciones = {}
    for p in peticiones:
        for persona_id, rol in p.roles:
            reporte.agregar_tarea(notificaciones, persona_id, p.tipo, (p.periodo, p.vencimiento),
                                  reporte.tarea(p.issue, rol))
    return notificaciones


def armar_html(nombre_real, datos_por_tipo, redmine_url):
    secciones = []
    for tipo in sorted(datos_por_tipo, key=reporte.orden_segun(list(REGLAS_NOTIFICACION))):
        grupos = sorted(datos_por_tipo[tipo].items(), key=lambda item: item[0])
        secciones.append((tipo, [
            (f"Período: <strong>{periodo}</strong> | Vencimiento: <strong>{fechas.formatear_larga(vencimiento)}</strong>", tareas)
            for (periodo, vencimiento), tareas in grupos
        ]))
    intro = "Te dejamos el listado de vencimientos próximos que requieren tu atención, agrupados por tipo de petición."
    return reporte.armar_cuerpo(nombre_real, intro, secciones, redmine_url)


@dataclass
class Informe:
    peticiones: int = 0
    personas: int = 0
    enviados: int = 0

    def lineas(self):
        return [f"Peticiones descargadas de Redmine: {self.peticiones}",
                f"Personas con vencimientos a notificar: {self.personas}",
                f"Correos enviados con éxito: {self.enviados}"]


def procesar(corrida, informe):
    with corrida.redmine() as cliente:
        usuarios = cliente.usuarios_o_respaldo(USUARIOS_FALLBACK)
        issues = cliente.peticiones_abiertas(estricto=False)
    informe.peticiones = len(issues)
    if not issues:
        print("No se descargaron peticiones desde Redmine.")
        return

    notificaciones = agrupar_por_persona(peticiones_del_dia(issues, fechas.hoy()))
    informe.personas = len(notificaciones)
    if not notificaciones:
        print("El sistema no detectó peticiones pendientes en los periodos filtrados.")
        return

    url = corrida.credenciales.redmine_url
    correos = reporte.preparar_correos(
        notificaciones, usuarios,
        armar_html=lambda nombre, _correo, datos: armar_html(nombre, datos, url),
        prefijo_asunto="Reporte de Vencimientos", modo_prueba=REDIRIGIR_A_ADMIN)
    informe.enviados = corrida.enviar(correos)


def main():
    corrida = Corrida("Impuestos", "impuestos")
    if corrida.ya_se_envio_hoy(WORKFLOW):
        return
    informe = Informe()
    corrida.ejecutar(lambda: procesar(corrida, informe))
    corrida.finalizar(informe.lineas())
