# Recibe los resúmenes de ejecución, los correos en modo prueba y los redirigidos.
CORREO_ADMIN = "francoalbrecht@rivarossa.com"

# Personas que no deben recibir avisos aunque Redmine las tenga asignadas en algún
# campo de rol (ej. bajas de personal). No modifica nada en Redmine.
IDS_EXCLUIDOS = frozenset({
    "792",  # Caravario, Darién - ya no es empleado (2026-09)
})

# Personas ("apellido, nombre" en minúsculas) cuyo correo, aun en producción, debe
# seguir llegando a CORREO_ADMIN en lugar de a su casilla real. Cada sector decide si
# la aplica (hoy: impuestos y laboral; auditoría y consultorías no).
REDIRIGIDOS_A_ADMIN = frozenset({
    "previotto, gisela",
    # vgorreta@rivarossa.com no existe / rebota ("no se ha encontrado la dirección");
    # mientras no se confirme la casilla real, se redirige a admin.
    "gorreta, valentina",
})

# Respaldo por si la API de Redmine deniega el acceso a /users.json: IDs cargados a mano.
USUARIOS_FALLBACK = {
    "579": {"nombre": "Sandoval, Vanina", "correo": "vsandoval@rivarossa.com"},
}

ETIQUETA_PRODUCCION = "[PRODUCCIÓN]"
ETIQUETA_MODO_PRUEBA = "[REDIRIGIDO A ADMIN]"
ETIQUETA_EXCEPCION = "[EXCEPCIÓN -> ADMIN]"


def nombre_y_correo(usuarios, persona_id):
    if persona_id in usuarios:
        return usuarios[persona_id]["nombre"], usuarios[persona_id]["correo"]
    return f"Usuario {persona_id}", CORREO_ADMIN


def nombre_de(usuarios, persona_id):
    return nombre_y_correo(usuarios, persona_id)[0]


def resolver_destino(nombre, correo_real, modo_prueba, redirigidos=REDIRIGIDOS_A_ADMIN):
    if modo_prueba:
        return CORREO_ADMIN, ETIQUETA_MODO_PRUEBA
    if nombre.strip().lower() in redirigidos:
        return CORREO_ADMIN, ETIQUETA_EXCEPCION
    return correo_real, ETIQUETA_PRODUCCION
