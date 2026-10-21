from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from agente import fechas, redmine, reporte, texto
from agente.corrida import Corrida
from agente.destinatarios import IDS_EXCLUIDOS, USUARIOS_FALLBACK

WORKFLOW = "reporte-laboral.yml"

REDIRIGIR_A_ADMIN = True

ESTADO_A_AVISAR = "Pendiente"

ROLES = {
    "liquidador 1": "Liquidador", "control 1": "Control", "soporte 1": "Soporte",
    "liquidador": "Liquidador", "control": "Control",
}

TRACKER_931 = "Laboral - Formulario 931"

# Cada regla dice de dónde sale la fecha de referencia y cuántos días hábiles antes (negativo) o
# después (positivo) de ella sale el aviso ("dias_habiles"). La fecha sale de un campo de la
# petición ("campo_fecha"), del vencimiento del F.931 ("segun_931") o del mes siguiente al
# período, que es cuando se presenta ("dia_habil_del_mes": el N-ésimo hábil; "dia_del_mes": ese
# día o el hábil siguiente).
REGLAS_LABORAL = {
    TRACKER_931: {"campo_fecha": "Vencimiento DD. JJ.", "dias_habiles": -2, "asunto": "leyes sociales",
                  "titulo": "Leyes Sociales (F.931)", "etiqueta_fecha": "Vencimiento"},
    "Laboral - 814": {"segun_931": True, "dias_habiles": 1, "titulo": "Decreto 814",
                      "etiqueta_fecha": "Vencimiento del F.931"},
    "Laboral - Provision vacaciones": {"campo_fecha": "Fecha límite", "dias_habiles": -3,
                                       "titulo": "Provisión de vacaciones", "etiqueta_fecha": "Fecha límite"},
    # 21/10/2026 (Diego): Wiltel tiene que sacar el asiento sí o sí el 3er día hábil de cada mes;
    # el resto usa la "Fecha Presentación:" que carga el sector en cada petición.
    "Laboral - Asiento sueldo": {"campo_fecha": "Fecha Presentación:", "dias_habiles": -2,
                                 "dia_habil_del_mes_por_empresa": {"WILTEL COMUNICACIONES": 3},
                                 "titulo": "Asiento de sueldos", "etiqueta_fecha": "Presentación"},
    "Laboral - SICORE": {"dia_habil_del_mes": 4, "dias_habiles": 0, "titulo": "SICORE",
                         "etiqueta_fecha": "Fecha"},
    "Laboral - SIRADIG": {"dia_del_mes": 20, "dias_habiles": 0, "titulo": "SIRADIG",
                          "etiqueta_fecha": "Fecha"},
}

# Los 931 sin cambios hace más de esto ya no le sirven a ningún 814 a avisar.
DIAS_HACIA_ATRAS_931 = 120

# Prioridad de Redmine sin cargar.
SIN_PRIORIDAD = "-"

# Para reconocer el tracker sin depender de tildes/mayúsculas ("Provisión" vs "Provision").
TRACKER_POR_NOMBRE_NORMALIZADO = {texto.normalizar(t): t for t in REGLAS_LABORAL}

def tracker_laboral(issue):
    return TRACKER_POR_NOMBRE_NORMALIZADO.get(texto.normalizar(redmine.tracker_de(issue)))


def prioridad_de(issue):
    return issue.get("priority", {}).get("name", SIN_PRIORIDAD)


def mes_siguiente_al_periodo(periodo):
    """"09/2026" -> (2026, 10): lo de un período se presenta el mes siguiente."""
    try:
        mes, anio = (int(parte) for parte in periodo.split("/"))
    except ValueError:
        return None
    if not 1 <= mes <= 12:
        return None
    return (anio + 1, 1) if mes == 12 else (anio, mes + 1)


def fecha_de_referencia(issue, regla, campos, periodo, vencimientos_del_931):
    """La fecha contra la que se calcula el aviso, o None si falta el dato."""
    if regla.get("segun_931"):
        por_proyecto, por_prioridad = vencimientos_del_931
        return fechas.parsear(por_proyecto.get((issue["project"]["id"], periodo))
                              or por_prioridad.get((periodo, prioridad_de(issue))))

    por_empresa = {texto.normalizar(e): n for e, n in regla.get("dia_habil_del_mes_por_empresa", {}).items()}
    dia_habil = por_empresa.get(texto.normalizar(redmine.empresa_de(issue)), regla.get("dia_habil_del_mes"))
    if dia_habil or regla.get("dia_del_mes"):
        mes = mes_siguiente_al_periodo(periodo)
        if mes is None:
            return None
        if dia_habil:
            return fechas.dia_habil_del_mes(*mes, dia_habil)
        return fechas.habil_desde(date(*mes, regla["dia_del_mes"]))

    return fechas.parsear(campos.get(texto.normalizar(regla["campo_fecha"]), ""))


def dia_del_aviso(fecha_referencia, dias_habiles):
    if dias_habiles < 0:
        return fechas.restar_dias_habiles(fecha_referencia, -dias_habiles)
    return fechas.sumar_dias_habiles(fecha_referencia, dias_habiles)


def vencimientos_931(cliente, issues, hoy):
    """Vencimientos de los F.931 (Leyes Sociales) recientes, abiertos o cerrados, para los 814: por
    proyecto y período y, para las empresas sin 931 en Redmine, por período y prioridad.

    21/10/2026 (Diego): la prioridad de la petición es la terminación del CUIT ("0 - 1 - 2 - 3",
    "4 - 5 - 6", "7 - 8 - 9") y define en cuál de los 3 días de vencimiento del 931 cae.
    """
    hay_814 = any(REGLAS_LABORAL.get(tracker_laboral(i), {}).get("segun_931")
                  and redmine.estado_de(i) == ESTADO_A_AVISAR for i in issues)
    if not hay_814:
        return {}, {}

    regla_931 = REGLAS_LABORAL[TRACKER_931]
    tracker_931_id = next(t["id"] for t in cliente.trackers()
                          if texto.normalizar(t["name"]) == texto.normalizar(TRACKER_931))
    campo_vencimiento = texto.normalizar(regla_931["campo_fecha"])
    desde = hoy - timedelta(days=DIAS_HACIA_ATRAS_931)

    por_proyecto, cantidad_por_prioridad = {}, defaultdict(Counter)
    for issue in cliente.peticiones(tracker_id=tracker_931_id, status_id="*", updated_on=f">={desde}"):
        if regla_931["asunto"] not in texto.normalizar(issue.get("subject", "")):
            continue
        campos = redmine.campos_personalizados(issue)
        vencimiento = campos.get(campo_vencimiento)
        if not vencimiento:
            continue
        periodo = redmine.periodo_de(campos)
        por_proyecto[(issue["project"]["id"], periodo)] = vencimiento
        if prioridad_de(issue) != SIN_PRIORIDAD:
            cantidad_por_prioridad[(periodo, prioridad_de(issue))][vencimiento] += 1
    # Los del mismo período y prioridad vencen el mismo día; si alguno difiere, gana la mayoría.
    por_prioridad = {clave: cantidades.most_common(1)[0][0] for clave, cantidades in cantidad_por_prioridad.items()}
    return por_proyecto, por_prioridad


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

        fecha = fecha_de_referencia(issue, regla, campos, periodo, vencimientos_del_931)
        if fecha is None:
            sin_fecha += 1
            continue
        if dia_del_aviso(fecha, regla["dias_habiles"]) != hoy:
            continue
        if not roles_por_persona:
            sin_responsables += 1
            continue

        for persona_id, roles in roles_por_persona.items():
            reporte.agregar_tarea(notificaciones, persona_id, tracker, (periodo, fecha.isoformat()),
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
        vencimientos_del_931 = vencimientos_931(cliente, issues, informe.hoy)
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
    if corrida.ya_se_envio_hoy(WORKFLOW):
        return
    informe = Informe(hoy)
    corrida.ejecutar(lambda: procesar(corrida, informe))
    corrida.finalizar(informe.lineas())
