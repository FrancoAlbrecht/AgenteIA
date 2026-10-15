from dataclasses import dataclass
from agente import fechas, redmine, reporte, texto
from agente.corrida import Corrida
from agente.destinatarios import IDS_EXCLUIDOS, USUARIOS_FALLBACK

REDIRIGIR_A_ADMIN = True

ESTADO_A_AVISAR = "Pendiente"

ROLES = {
    "liquidador 1": "Liquidador", "control 1": "Control", "soporte 1": "Soporte",
    "liquidador": "Liquidador", "control": "Control",
}

TRACKER_931 = "Laboral - Formulario 931"

REGLAS_LABORAL = {
    TRACKER_931: {"regla": "dias_habiles_antes", "dias": 2, "campo_fecha": "Vencimiento DD. JJ.",
                  "asunto": "leyes sociales", "titulo": "Leyes Sociales (F.931)",
                  "etiqueta_fecha": "Vencimiento"},
    "Laboral - 814": {"regla": "habil_despues_del_931", "dias": 1, "titulo": "Decreto 814",
                      "etiqueta_fecha": "Vencimiento del F.931"},
    "Laboral - Provision vacaciones": {"regla": "dias_habiles_antes", "dias": 3, "campo_fecha": "Fecha límite",
                                       "titulo": "Provisión de vacaciones", "etiqueta_fecha": "Fecha límite"},
}

# Para reconocer el tracker sin depender de tildes/mayúsculas ("Provisión" vs "Provision").
TRACKER_POR_NOMBRE_NORMALIZADO = {texto.normalizar(t): t for t in REGLAS_LABORAL}

def tracker_laboral(issue):
    return TRACKER_POR_NOMBRE_NORMALIZADO.get(texto.normalizar(redmine.tracker_de(issue)))


def corresponde_avisar(regla, fecha_referencia, hoy):
    if regla["regla"] == "dias_habiles_antes":
        return fechas.restar_dias_habiles(fecha_referencia, regla["dias"]) == hoy
    if regla["regla"] == "habil_despues_del_931":
        return fechas.sumar_dias_habiles(fecha_referencia, regla["dias"]) == hoy
    return False


def vencimientos_931(cliente, issues):
    trackers_814 = {t for t, regla in REGLAS_LABORAL.items() if regla["regla"] == "habil_despues_del_931"}
    proyectos = {i["project"]["id"] for i in issues
                 if tracker_laboral(i) in trackers_814 and redmine.estado_de(i) == ESTADO_A_AVISAR}
    if not proyectos:
        return {}

    regla_931 = REGLAS_LABORAL[TRACKER_931]
    tracker_931_id = next(t["id"] for t in cliente.trackers()
                          if texto.normalizar(t["name"]) == texto.normalizar(TRACKER_931))
    campo_vencimiento = texto.normalizar(regla_931["campo_fecha"])

    vencimientos = {}
    for proyecto_id in proyectos:
        for issue in cliente.peticiones(project_id=proyecto_id, tracker_id=tracker_931_id, status_id="*"):
            if regla_931["asunto"] not in texto.normalizar(issue.get("subject", "")):
                continue
            campos = redmine.campos_personalizados(issue)
            if campos.get(campo_vencimiento):
                vencimientos[(issue["project"]["id"], redmine.periodo_de(campos))] = campos[campo_vencimiento]
    return vencimientos


def agrupar_por_persona(issues, hoy, vencimientos_del_931):
    notificaciones = {}
    sin_responsables = sin_fecha = 0

    for issue in issues:
        tracker = tracker_laboral(issue)
        if tracker is None or redmine.estado_de(issue) != ESTADO_A_AVISAR:
            continue
        regla = REGLAS_LABORAL[tracker]
        if regla.get("asunto") and regla["asunto"] not in texto.normalizar(issue.get("subject", "")):
            continue

        campos = redmine.campos_personalizados(issue)
        periodo = redmine.periodo_de(campos)
        roles_por_persona = {}
        for nombre, valor in campos.items():
            if nombre in ROLES and valor not in IDS_EXCLUIDOS:
                roles_por_persona.setdefault(valor, []).append(ROLES[nombre])

        if regla["regla"] == "habil_despues_del_931":
            fecha_referencia = vencimientos_del_931.get((issue["project"]["id"], periodo), "")
        else:
            fecha_referencia = campos.get(texto.normalizar(regla["campo_fecha"]), "")
        fecha = fechas.parsear(fecha_referencia)
        if fecha is None:
            sin_fecha += 1
            continue
        if not corresponde_avisar(regla, fecha, hoy):
            continue
        if not roles_por_persona:
            sin_responsables += 1
            continue

        for persona_id, roles in roles_por_persona.items():
            reporte.agregar_tarea(notificaciones, persona_id, tracker, (periodo, fecha_referencia),
                                  reporte.tarea(issue, texto.unir_roles(roles)))

    print(f"Filtrado laboral: {sin_fecha} pendientes sin fecha de referencia cargada, "
          f"{sin_responsables} a avisar hoy pero sin responsables asignados (se ignoran).")
    return notificaciones


def armar_html(nombre_real, correo_real, datos_por_tipo, redmine_url):
    aviso = ""
    if REDIRIGIR_A_ADMIN:
        aviso = ('<p style="font-size: 12px; margin: 0 0 20px 0; padding: 8px 12px; background-color: #fff4d6; '
                 'border: 1px solid #e6c65c; border-radius: 3px; color: #7a5b00;">'
                 f"MODO PRUEBA — en producción este correo le llegaría a {nombre_real} "
                 f"({correo_real or 'sin correo en Redmine'}).</p>")
    secciones = []
    for tipo in sorted(datos_por_tipo, key=reporte.orden_segun(list(REGLAS_LABORAL))):
        regla = REGLAS_LABORAL[tipo]
        grupos = []
        for periodo, fecha in sorted(datos_por_tipo[tipo], key=lambda clave: (clave[1], clave[0])):
            encabezado = (f"Período: <strong>{periodo}</strong> | {regla['etiqueta_fecha']}: "
                          f"<strong>{fechas.formatear_larga(fecha)}</strong>")
            grupos.append((encabezado, sorted(datos_por_tipo[tipo][(periodo, fecha)], key=lambda t: t["empresa"])))
        secciones.append((regla["titulo"], grupos))
    intro = ("Te dejamos el listado de vencimientos laborales próximos que requieren tu atención, "
             "agrupados por tipo de petición.")
    return reporte.armar_cuerpo(nombre_real, intro, secciones, redmine_url, aviso_html=aviso)


@dataclass
class Informe:
    hoy: object
    peticiones: int = 0
    personas: int = 0
    enviados: int = 0

    def lineas(self):
        return [f"Fecha de proceso: {self.hoy.isoformat()}",
                f"Modo prueba (todo a admin): {'SÍ' if REDIRIGIR_A_ADMIN else 'NO'}",
                f"Peticiones descargadas de Redmine: {self.peticiones}",
                f"Personas con vencimientos a notificar: {self.personas}",
                f"Correos enviados con éxito: {self.enviados}"]


def procesar(corrida, informe):
    with corrida.redmine() as cliente:
        usuarios = cliente.usuarios_o_respaldo(USUARIOS_FALLBACK)
        issues = cliente.peticiones_abiertas(estricto=True)
        vencimientos_del_931 = vencimientos_931(cliente, issues)
    informe.peticiones = len(issues)

    notificaciones = agrupar_por_persona(issues, informe.hoy, vencimientos_del_931)
    informe.personas = len(notificaciones)
    url = corrida.credenciales.redmine_url
    correos = reporte.preparar_correos(
        notificaciones, usuarios,
        armar_html=lambda nombre, correo_real, datos: armar_html(nombre, correo_real, datos, url),
        prefijo_asunto="Vencimientos Laborales", modo_prueba=REDIRIGIR_A_ADMIN)
    if not correos:
        print("No hay vencimientos laborales para avisar hoy.")
    informe.enviados = corrida.enviar(correos)


def main():
    hoy = fechas.hoy()
    print(f"Fecha de proceso: {hoy.isoformat()}")
    corrida = Corrida("Laboral", "laboral")
    informe = Informe(hoy)
    corrida.ejecutar(lambda: procesar(corrida, informe))
    corrida.finalizar(informe.lineas())
