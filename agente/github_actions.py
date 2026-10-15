import os
from datetime import datetime
import requests
from agente.fechas import parsear_iso_utc

URL_API = "https://api.github.com"

def _contexto():
    repo, token, run_id = (os.getenv(v) for v in ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "GITHUB_RUN_ID"))
    return (repo, token, run_id) if repo and token and run_id else None


def _get(repo, token, ruta, params=None):
    respuesta = requests.get(f"{URL_API}/repos/{repo}/actions/{ruta}", params=params or {}, timeout=15,
                             headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
    respuesta.raise_for_status()
    return respuesta.json()


def ya_hubo_envio_exitoso_hoy(workflow):
    contexto = _contexto()
    if not contexto:
        return False
    repo, token, run_id = contexto
    try:
        hoy = datetime.now().date().isoformat()
        corridas = _get(repo, token, f"workflows/{workflow}/runs",
                        {"status": "success", "created": f">={hoy}", "per_page": 10}).get("workflow_runs", [])
        return any(str(r.get("id")) != str(run_id) for r in corridas)
    except Exception as e:
        print(f"[WARN] No se pudo chequear corridas previas de hoy ({e}); se continúa igual.")
        return False


def inicio_de_esta_corrida_y_anteriores(workflow):
    contexto = _contexto()
    if not contexto:
        return None
    repo, token, run_id = contexto
    inicio = parsear_iso_utc(_get(repo, token, f"runs/{run_id}")["run_started_at"])
    corridas = _get(repo, token, f"workflows/{workflow}/runs",
                    {"status": "success", "per_page": 30}).get("workflow_runs", [])
    anteriores = [parsear_iso_utc(r["run_started_at"]) for r in corridas
                  if str(r.get("id")) != str(run_id) and r.get("run_started_at")]
    return inicio, [t for t in anteriores if t < inicio]
