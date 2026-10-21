import time

import requests
from agente import texto

TAMANIO_PAGINA = 100

# 19/10/2026: Redmine respondió 502 Bad Gateway un rato a la mañana y cortó el resumen R2 y una
# corrida de consultorías. Ante errores transitorios (servidor caído o saturado, sin conexión,
# timeout) se reintenta con estas esperas (en segundos, ~4 min en total) antes de dar error.
ESPERAS_REINTENTO = (15, 30, 60, 120)
ESTADOS_TRANSITORIOS = {429, 500, 502, 503, 504}

class Redmine:
    def __init__(self, url, api_key, timeout=20):
        self.url = url
        self.timeout = timeout
        self._headers = {"X-Redmine-API-Key": api_key}
        self._sesion = requests.Session()
        self._grupos = {}
        self._grupos_de_proyecto = {}
        self._nombres_proyecto = {}
        self._campos = None
        self._estados = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._sesion.close()
        return False

    def get(self, ruta, params=None):
        for intento, espera in enumerate((*ESPERAS_REINTENTO, None), start=1):
            try:
                respuesta = self._sesion.get(f"{self.url}{ruta}", headers=self._headers,
                                             params=params or {}, timeout=self.timeout)
                if espera is None or respuesta.status_code not in ESTADOS_TRANSITORIOS:
                    respuesta.raise_for_status()
                    return respuesta.json()
                motivo = f"respondió {respuesta.status_code}"
            except (requests.ConnectionError, requests.Timeout) as e:
                if espera is None:
                    raise
                motivo = f"no respondió ({type(e).__name__})"
            print(f"[WARN] Redmine {motivo} en {ruta}; reintento {intento}/{len(ESPERAS_REINTENTO)} en {espera} s.")
            time.sleep(espera)

    def paginar(self, ruta, clave, params=None):
        """Recorre un listado paginado (ej. issues.json -> "issues") hasta la última página."""
        offset = 0
        while True:
            pagina = self.get(ruta, {**(params or {}), "limit": TAMANIO_PAGINA, "offset": offset}).get(clave, [])
            if not pagina:
                return
            yield from pagina
            offset += TAMANIO_PAGINA

    def usuarios(self):
        return {
            str(u.get("id")): {
                "nombre": f"{u.get('lastname', '')}, {u.get('firstname', '')}".strip(" ,"),
                "correo": u.get("mail", ""),
            }
            for u in self.paginar("users.json", "users")
        }

    def usuarios_o_respaldo(self, respaldo):
        print("Sincronizando directorio de usuarios desde Redmine...")
        try:
            usuarios = self.usuarios()
        except requests.HTTPError as e:
            sin_permiso = e.response is not None and e.response.status_code == 403
            print("[WARN] Sin permisos para leer /users.json. Se utiliza el respaldo de usuarios."
                  if sin_permiso else f"[WARN] Error al sincronizar usuarios ({e}). Se utiliza el respaldo de usuarios.")
            return respaldo
        except Exception as e:
            print(f"[WARN] Error al sincronizar usuarios ({e}). Se utiliza el respaldo de usuarios.")
            return respaldo
        print(f"[OK] Directorio sincronizado: {len(usuarios)} usuarios encontrados.")
        return usuarios

    def peticiones_abiertas(self, estricto=True):
        print("Descargando padrón de peticiones abiertas...")
        peticiones = []
        try:
            peticiones.extend(self.paginar("issues.json", "issues", {"status_id": "open"}))
        except Exception as e:
            if estricto:
                raise
            print(f"[ERROR] Error al descargar peticiones: {e}")
        print(f"[OK] Peticiones descargadas: {len(peticiones)} en total.")
        return peticiones

    def peticiones(self, **filtros):
        return list(self.paginar("issues.json", "issues", filtros))

    def peticion_con_historial(self, issue_id):
        return self.get(f"issues/{issue_id}.json", {"include": "journals"})["issue"]

    def trackers(self):
        return self.get("trackers.json")["trackers"]

    def miembros_de_grupo(self, grupo_id):
        if grupo_id not in self._grupos:
            data = self.get(f"groups/{grupo_id}.json", {"include": "users"})["group"]
            self._grupos[grupo_id] = (data.get("name", ""), [str(u["id"]) for u in data.get("users", [])])
        return self._grupos[grupo_id]

    def grupos_del_proyecto(self, proyecto_id):
        if proyecto_id not in self._grupos_de_proyecto:
            data = self.get(f"projects/{proyecto_id}/memberships.json", {"limit": TAMANIO_PAGINA}).get("memberships", [])
            self._grupos_de_proyecto[proyecto_id] = [str(m["group"]["id"]) for m in data if m.get("group")]
        return self._grupos_de_proyecto[proyecto_id]

    def nombre_proyecto(self, proyecto_id):
        if proyecto_id not in self._nombres_proyecto:
            try:
                self._nombres_proyecto[proyecto_id] = self.get(f"projects/{proyecto_id}.json")["project"]["name"]
            except Exception:
                self._nombres_proyecto[proyecto_id] = f"Proyecto {proyecto_id}"
        return self._nombres_proyecto[proyecto_id]

    def nombre_estado(self, estado_id):
        if self._estados is None:
            try:
                self._estados = {str(s["id"]): s["name"] for s in self.get("issue_statuses.json")["issue_statuses"]}
            except Exception:
                self._estados = {}
        return self._estados.get(str(estado_id), str(estado_id))

    def nombre_campo(self, campo_id):
        if self._campos is None:
            try:
                self._campos = {str(c["id"]): c["name"] for c in self.get("custom_fields.json")["custom_fields"]}
            except Exception:
                self._campos = {}
        return self._campos.get(str(campo_id), "Campo personalizado")

def tracker_de(issue):
    return issue.get("tracker", {}).get("name", "")


def estado_de(issue):
    return issue.get("status", {}).get("name", "")


def prioridad_de(issue):
    return issue.get("priority", {}).get("name", "")


def proyecto_de(issue):
    return issue.get("project", {}).get("name", "")


def empresa_de(issue):
    return proyecto_de(issue).split(" / ")[0].strip()


def campos_personalizados(issue):
    campos = {}
    for c in issue.get("custom_fields", []):
        valor = c.get("value")
        valor = str(valor).strip() if valor not in (None, "") else ""
        if valor:
            campos[texto.normalizar(c.get("name", ""))] = valor
    return campos


def periodo_de(campos):
    return next((v for k, v in reversed(campos.items()) if "periodo" in k), "")
