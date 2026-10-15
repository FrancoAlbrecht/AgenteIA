from agente import destinatarios, fechas, plantilla, redmine, texto
from agente.correo import Correo
from agente.plantilla import COLOR

def tarea(issue, rol):
    return {"empresa": redmine.empresa_de(issue), "asunto": issue.get("subject", ""),
            "rol": rol, "issue_id": issue.get("id")}

def agregar_tarea(notificaciones, persona_id, tipo, clave, datos_tarea):
    notificaciones.setdefault(persona_id, {}).setdefault(tipo, {}).setdefault(clave, []).append(datos_tarea)


def orden_segun(referencia):
    return lambda t: (referencia.index(t) if t in referencia else len(referencia), t)


def armar_cuerpo(nombre_real, intro, secciones, redmine_url, aviso_html=""):
    html = aviso_html + plantilla.saludo(texto.nombre_de_pila(nombre_real), intro)
    for titulo, grupos in secciones:
        html += (f'<div style="margin-top: 30px; padding-top: 20px; border-top: 2px solid {COLOR};">'
                 f'<h2 style="color: {COLOR}; font-size: 18px; margin: 0 0 15px 0; font-weight: bold;">{titulo}</h2>')
        for encabezado, tareas in grupos:
            html += ('<div style="margin-bottom: 20px;">'
                     f'<h3 style="color: #555555; font-size: 14px; font-weight: bold; margin: 10px 0 8px 0;">{encabezado}</h3>')
            for t in tareas:
                html += _tarjeta(t, redmine_url)
            html += "</div>"
        html += "</div>"
    return html


def _tarjeta(t, redmine_url):
    link = plantilla.link_peticion(redmine_url, t["issue_id"], estilo_extra=" margin-right: 6px;")
    return (f'<div style="margin-left: 15px; margin-bottom: 12px; padding: 10px 12px; background-color: #fafafa; '
            f'border-left: 3px solid {COLOR}; border-radius: 3px;">'
            f'<p style="margin: 0 0 5px 0; font-size: 13px;">{link}<strong style="color: #333333;">{t["empresa"]}</strong> — {t["asunto"]}</p>'
            f'<p style="margin: 0; font-size: 12px; color: #777777;"><em>Rol: {t["rol"]}</em></p>'
            '</div>')


def preparar_correos(notificaciones, usuarios, armar_html, prefijo_asunto, modo_prueba,
                     redirigidos=destinatarios.REDIRIGIDOS_A_ADMIN):
    """Un Correo por persona. armar_html(nombre_real, correo_real, datos_por_tipo) -> cuerpo HTML."""
    correos = []
    for persona_id, datos in notificaciones.items():
        nombre, correo_real = destinatarios.nombre_y_correo(usuarios, persona_id)
        destino, etiqueta = destinatarios.resolver_destino(nombre, correo_real, modo_prueba, redirigidos)
        correos.append(Correo(
            nombre=nombre, destino=destino, etiqueta=etiqueta,
            asunto=f"{prefijo_asunto}: {nombre.upper()}",
            html=plantilla.documento(armar_html(nombre, correo_real, datos)),
            archivo=f"{fechas.hoy().isoformat()}_{persona_id}",
        ))
    return correos
