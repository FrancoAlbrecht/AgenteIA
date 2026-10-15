import os
from datetime import datetime, timezone
from agente import correo, entorno, fechas, github_actions
from agente.destinatarios import CORREO_ADMIN
from agente.redmine import Redmine

class Corrida:
    def __init__(self, agente, carpeta):
        self.agente = agente
        self.credenciales = entorno.Credenciales.desde_entorno()
        self.vista_previa = entorno.bandera_activa("SOLO_VISTA_PREVIA")
        self.carpeta_vista_previa = os.path.join(entorno.RAIZ_DEL_PROYECTO, "vista_previa", carpeta)
        self.error = None
        self._access_token = None

    @property
    def estado(self):
        return "CON ERROR" if self.error else "EXITOSA"

    def redmine(self):
        return Redmine(self.credenciales.redmine_url, self.credenciales.redmine_api_key)

    def ya_se_envio_hoy(self, workflow):
        """Anti-duplicados de los disparos de respaldo (ver github_actions)."""
        if not self.vista_previa and github_actions.ya_hubo_envio_exitoso_hoy(workflow):
            print("Ya hubo una ejecución exitosa hoy — se omite este disparo (de respaldo) "
                  "para no duplicar los avisos.")
            return True
        return False

    def ejecutar(self, trabajo):
        try:
            trabajo()
        except Exception as e:
            self.error = str(e)
            print(f"[ERROR] Ejecución interrumpida: {e}")

    def enviar(self, correos):
        if self.vista_previa:
            self._guardar_vista_previa(correos)
            return 0
        if not correos:
            return 0
        return correo.enviar_correos(self.credenciales, self._token(), correos)

    def finalizar(self, detalle, enviar_resumen=True):
        lineas = list(detalle) + (["", f"Error: {self.error}"] if self.error else [])
        if self.vista_previa:
            print("\n--- Resumen (no enviado, vista previa) ---\n" + "\n".join(lineas))
        elif enviar_resumen or self.error:
            self._enviar_resumen(lineas)
        if self.error:
            raise SystemExit(1)

    def _token(self):
        if self._access_token is None:
            self._access_token = correo.obtener_access_token(self.credenciales)
        return self._access_token

    def _enviar_resumen(self, lineas):
        try:
            token = self._token()
        except Exception as e:
            print(f"[ERROR] No se pudo obtener un access token para mandar el resumen: {e}")
            return
        asunto = (f"[Agente {self.agente}] Ejecución {self.estado} - "
                  f"{fechas.fecha_hora_art(datetime.now(timezone.utc))}")
        try:
            correo.enviar_texto(self.credenciales, token, CORREO_ADMIN, asunto, "\n".join(lineas))
            print(f"[OK] Resumen de ejecución enviado a {CORREO_ADMIN}")
        except Exception as e:
            print(f"[ERROR] No se pudo enviar el resumen de ejecución a {CORREO_ADMIN}: {e}")

    def _guardar_vista_previa(self, correos):
        os.makedirs(self.carpeta_vista_previa, exist_ok=True)
        for c in correos:
            archivo = os.path.join(self.carpeta_vista_previa, f"{c.archivo}.html")
            with open(archivo, "w", encoding="utf-8") as f:
                f.write(c.html)
            print(f"[VISTA PREVIA] {c.etiqueta} {c.nombre} -> {c.destino} | {c.asunto} | {archivo}")
