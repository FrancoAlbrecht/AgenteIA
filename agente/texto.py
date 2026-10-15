import unicodedata

def normalizar(texto):
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().lower()


def nombre_de_pila(nombre_redmine):
    return nombre_redmine.split(",")[1].strip() if "," in nombre_redmine else nombre_redmine


def nombre_y_apellido(nombre_redmine):
    if "," in nombre_redmine:
        apellido, pila = nombre_redmine.split(",", 1)
        return f"{pila.strip()} {apellido.strip()}"
    return nombre_redmine.strip()


def unir_roles(roles):
    unicos = list(dict.fromkeys(roles))
    if len(unicos) == 1:
        return unicos[0]
    return " y ".join([", ".join(unicos[:-1]), unicos[-1]])
