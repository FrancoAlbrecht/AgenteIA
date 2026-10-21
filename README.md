# AgenteIA — Avisos automáticos por correo desde Redmine

Agentes en Python que leen las peticiones de **Redmine** del Estudio Rivarossa y mandan
correos HTML (Gmail, SMTP con OAuth2) a cada persona según lo que tiene que atender.
Corren solos en **GitHub Actions**. Pese al nombre, **no usan IA**: son reglas fijas.

## Qué hace cada agente

| Sector (`python -m agente <sector>`) | Qué avisa | Cuándo corre (workflow) | Estado |
|---|---|---|---|
| `impuestos` | Peticiones `Tributaria - *` *Pendientes* (proyectos "IMPUESTOS") cuyo `Vencimiento DD. JJ.` es dentro de N días hábiles (hoy 2). Un correo por persona (Auxiliar / Liquidador / Responsable). | Diario, `reporte-vencimientos.yml` (03:24 ART + 2 respaldos) | Producción |
| `auditoria` | Peticiones `CyA Balance/Corte/Auditoria` *Pendientes* con `Fecha de cierre OT` entre hoy y fin del mes siguiente. | Diario, `reporte-auditoria.yml`; **sólo envía el día 21** (o `FECHAS_ENVIO_EXTRA`) | Producción |
| `consultorias` | Consultas nuevas o editadas por clientes de `PROYECTOS_CONSULTORIA`: al asignado, al grupo asignado o, sin asignar, a los grupos del proyecto. | Cada 30 min, lun–vie 07:17–19:47 ART, `notificacion-consultorias.yml` | Producción |
| `resumen_r2` | Un resumen por sector para el R2 (subgerente): una fila por petición con todo el equipo, más las que no le llegaron a nadie. | Diario, `resumen-r2.yml` (03:31 ART + 2 respaldos) | Producción (sólo impuestos) |
| `laboral` | F.931 (2 hábiles antes), Decreto 814 (1 hábil después del vencimiento del 931, propio o según prioridad), Provisión de vacaciones (3 hábiles antes), Asiento de sueldos (2 hábiles antes de la presentación), SICORE (4° hábil del mes) y SIRADIG (día 20). | Diario, `reporte-laboral.yml` (03:19 ART + 2 respaldos) | Producción (desde el 21/10/2026) |

## Estructura del proyecto

```
agente/
  __main__.py              Punto de entrada único: python -m agente <sector> (lo usan los workflows).
  entorno.py               Credenciales y banderas desde variables de entorno / .env.
  destinatarios.py         CORREO_ADMIN, IDS_EXCLUIDOS, REDIRIGIDOS_A_ADMIN, USUARIOS_FALLBACK y a quién va cada correo.
  fechas.py                Fecha de proceso (FECHA_SIMULADA), días hábiles, formatos, hora argentina.
  feriados.py              Calendario de feriados (cargar cada año).
  texto.py                 Normalizar textos (sin tildes), nombres y roles legibles.
  redmine.py               Cliente de sólo lectura de la API de Redmine + helpers para leer una petición.
  correo.py                Token OAuth2 de Google, conexión SMTP (XOAUTH2) y envío de correos.
  plantilla.py             Plantilla HTML común (logo, colores, pie), saludo y links a peticiones.
  reporte.py               "Reporte por persona" común a impuestos, auditoría y laboral (estructura + HTML).
  github_actions.py        API de GitHub Actions: anti-duplicados y corridas anteriores.
  corrida.py               Esqueleto de toda corrida: errores, vista previa, resumen al admin, código de salida.
  sectores/
    impuestos.py  auditoria.py  laboral.py  consultorias.py  resumen_r2.py
.github/workflows/
  ejecutar-agente.yml      Workflow reutilizable: checkout, Python 3.11, dependencias, secrets y python -m agente <sector>.
  reporte-vencimientos.yml, reporte-auditoria.yml, resumen-r2.yml, notificacion-consultorias.yml
                           Cada uno define sólo cuándo corre su agente y llama a ejecutar-agente.yml.
images/LogoBlanco.png      Logo del encabezado de los correos.
```

**Dependencias entre módulos** (de abajo hacia arriba, sin ciclos): `feriados` → `fechas`;
`texto`; `entorno` → `plantilla`; `redmine` usa `texto`; `correo`; `github_actions` usa `fechas`;
`destinatarios`; `reporte` y `corrida` combinan los anteriores; cada sector usa los comunes. El
único sector que depende de otros es `resumen_r2`, que reutiliza el filtrado de `impuestos` y
`auditoria` para que el R2 vea exactamente lo que les llegó a las personas de su equipo.

## Cómo funciona una corrida

1. `python -m agente X` (`agente/__main__.py`) llama a `agente.sectores.X.main()`, que crea una `Corrida` (lee credenciales).
2. Impuestos, auditoría y R2: si el workflow ya terminó bien hoy (disparo de respaldo o doble
   disparo), termina sin hacer nada. Auditoría, además, sólo sigue si hoy es su día de envío.
3. Descarga usuarios y peticiones de Redmine (sólo GET), filtra con las reglas del sector y arma
   un `Correo` por destinatario (HTML con `plantilla.documento`).
4. `Corrida.enviar` pide el access token a Google (una vez) y manda todo por una sola conexión
   SMTP. Si había correos y no salió ninguno, falla. En vista previa los guarda como `.html`.
5. `Corrida.finalizar` manda el *resumen de ejecución* al admin con contadores y errores (en
   consultorías sólo si hubo avisos, alertas o error) y sale con código 1 si hubo error.

### Redes de seguridad que no hay que romper
- **Modo prueba**: cada sector tiene `REDIRIGIR_A_ADMIN` arriba de su módulo. En `True` todo va
  a `CORREO_ADMIN`. Probá siempre con el flag en `True` antes de pasar a producción.
- **Excluidos y excepciones** (`agente/destinatarios.py`): `IDS_EXCLUIDOS` (nadie les avisa, ej.
  bajas) y `REDIRIGIDOS_A_ADMIN` (su correo va a admin). Se cargan **una sola vez** para todos.
  Auditoría suma sus propios excluidos y no aplica `REDIRIGIDOS_A_ADMIN` (tampoco consultorías).
- **Anti-duplicados**: `github_actions.ya_hubo_envio_exitoso_hoy()` (impuestos, auditoría, R2).
- **Ventana de consultorías**: desde la última corrida exitosa hasta ésta (tope 7 días): no se
  pierden ni repiten avisos aunque GitHub saltee corridas. El workflow no permite dos a la vez.

## Puesta en marcha local

Requisitos: Python 3.11 (el mismo que GitHub Actions) y acceso a Redmine.

```bash
git clone https://github.com/FrancoAlbrecht/AgenteIA.git
cd AgenteIA
python -m venv venv
venv\Scripts\activate            # Windows  (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt  # sólo requests y python-dotenv
```

Crear `.env` en la raíz (está en `.gitignore`, **nunca** commitearlo):

```dotenv
REDMINE_URL=https://<servidor-redmine>/        # se le agrega la "/" final si falta
REDMINE_API_KEY=<Redmine > Mi cuenta > Clave de acceso a la API>
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=<cuenta de Gmail que envía, la misma del refresh token>
SMTP_FROM_EMAIL=<remitente que se muestra (normalmente = SMTP_USERNAME)>
GMAIL_OAUTH_CLIENT_ID=<Google Cloud Console > APIs y servicios > Credenciales>
GMAIL_OAUTH_CLIENT_SECRET=<idem>
GMAIL_OAUTH_REFRESH_TOKEN=<generado para SMTP_USERNAME con el scope https://mail.google.com/>
```

- Son los mismos nombres que los *Secrets* del repo (GitHub no deja leerlos: pedir los valores).
- La API key debe poder ver usuarios, grupos, membresías y peticiones de todos los proyectos.
- Si se genera un refresh token nuevo y se revoca el anterior, producción deja de enviar hasta
  actualizar el secret `GMAIL_OAUTH_REFRESH_TOKEN`.

### Probar sin mandar correos

> ⚠️ Con un `.env` válido, `python -m agente <sector>` **manda correos reales** (a personas y al admin).
> Para probar, usá **siempre** `SOLO_VISTA_PREVIA=1`. Se ejecuta desde la **raíz del repo** (si no,
> Python no encuentra el paquete `agente`); sin sector, muestra la lista de sectores válidos.

| Variable | Agentes | Efecto |
|---|---|---|
| `SOLO_VISTA_PREVIA=1` | todos | No envía nada (ni el resumen): guarda cada correo en `vista_previa/<sector>/*.html`. |
| `FECHA_SIMULADA=YYYY-MM-DD` | todos | Corre como si fuera esa fecha (días hábiles, ventana de auditoría, etc.). |
| `VENTANA_DESDE=YYYY-MM-DDTHH:MM:SSZ` / `VENTANA_HORAS=N` | consultorías | Ventana a revisar en local (default: últimas 24 h). |
| `SECTORES_R2=impuestos,auditoria` | R2 | Pisa `SECTORES_ACTIVOS`. |
| `FORZAR_ENVIO_AUDITORIA=1` | auditoría | Corre aunque no sea día 21 y saltea el anti-duplicados (**sin vista previa, envía de verdad**). |
| `FORZAR_ENVIO_R2_AUDITORIA=1` | R2 | Arma el resumen de auditoría aunque no sea su día. |

```powershell
$env:SOLO_VISTA_PREVIA="1"; $env:FECHA_SIMULADA="2026-11-06"; python -m agente laboral
```

## Tareas frecuentes (dónde tocar)

| Quiero... | Archivo / constante |
|---|---|
| Excluir a alguien de todos los avisos (baja) | `agente/destinatarios.py` → `IDS_EXCLUIDOS` |
| Cambiar el correo admin | `agente/destinatarios.py` → `CORREO_ADMIN` |
| Cambiar días de anticipación de impuestos o sumar un tracker | `agente/sectores/impuestos.py` → `REGLAS_NOTIFICACION` (nombre idéntico al de Redmine) |
| Reglas laborales | `agente/sectores/laboral.py` → `REGLAS_LABORAL` |
| Día de envío de auditoría / envío puntual | `agente/sectores/auditoria.py` → `DIA_CORTE_MENSUAL`, `FECHAS_ENVIO_EXTRA` |
| Nuevo cliente de consultoría | `agente/sectores/consultorias.py` → `PROYECTOS_CONSULTORIA` (identificador de la URL) |
| R2, su equipo o activar auditoría | `agente/sectores/resumen_r2.py` → `R2_POR_SECTOR`, `FUERA_DEL_EQUIPO_R2`, `SECTORES_ACTIVOS` |
| Feriados del año | `agente/feriados.py` (2027 sin los días turísticos: el decreto no salió aún) |
| Estética de todos los correos | `agente/plantilla.py` |
| Un agente nuevo | Módulo en `agente/sectores/` con `main()` usando `Corrida`; agregarlo a `SECTORES` en `agente/__main__.py`; workflow que llame a `ejecutar-agente.yml` |

Los nombres de campos de Redmine (`vencimiento dd. jj.`, `fecha de cierre ot`, roles) están en
cada sector, normalizados con `texto.normalizar` (minúsculas, sin tildes). Si alguien los renombra
en Redmine, el agente deja de encontrar peticiones **sin dar error**: mirar los contadores del resumen.

## GitHub Actions

- Los `schedule` llegan **3 a 7 horas tarde** en este repo: por eso se disparan de madrugada,
  en minutos "no redondos", y con respaldos. Auditoría además recibe un disparo externo temprano
  (cron-job.org, vía `workflow_dispatch` sobre `reporte-auditoria.yml`: no renombrar ese archivo).
- **Fechas en UTC** en el runner: no mover crons antes de las 03:00 UTC (00:00 ART).
- Todos los workflows aceptan disparo manual (*Actions → Run workflow*) y necesitan
  `permissions: actions: read` (anti-duplicados y ventana de consultorías).
- GitHub desactiva los `schedule` de un repo sin commits durante 60 días.

## Diagnóstico ante problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| No llegó ningún correo ni resumen | El workflow no corrió o está desactivado | *Actions*: ver si hubo corrida; habilitar o disparar a mano. |
| `invalid_grant` al pedir el token | Refresh token vencido o revocado | Generar uno nuevo; actualizar secret y `.env`. |
| Error de login SMTP (`535`, `WebLoginRequired`) | `SMTP_USERNAME` no es la cuenta del token, o bloqueo de Google | Verificar; revisar alertas de seguridad de esa cuenta. |
| `401`/`403` de Redmine | API key inválida o sin permisos | Regenerar la key o revisar permisos. |
| `502`/`503`/`504` de Redmine o "no respondió" | Redmine caído o saturado | Se reintenta solo ~4 min (`ESPERAS_REINTENTO` en `redmine.py`); si sigue, la corrida falla y la retoma el próximo disparo de respaldo. |
| "0 personas a notificar" un día que debería haber | Campo/tracker renombrado, fecha sin cargar o feriado mal cargado | Contadores "sin fecha válida / fuera de la ventana" del log; `feriados.py`. |
| Una persona no recibe su correo | Excluida/redirigida, sin mail en Redmine o rebota | `destinatarios.py` y el log `[ERROR] Error al enviar correo a ...`. |
| Llegó el correo dos veces | Falló el anti-duplicados (`[WARN] No se pudo chequear corridas previas`) | Revisar `permissions: actions: read`. |

Para reproducir un problema en local: `SOLO_VISTA_PREVIA=1` (y `FECHA_SIMULADA` si hace falta).

## Convenciones

- Código, comentarios, commits y salida en **español**; los comentarios explican el *por qué*
  (con fecha y quién lo pidió cuando es una decisión del negocio).
- Lo común va en `agente/` y se cambia en un solo lugar; lo propio de un sector, arriba de su
  módulo. Importar cualquier módulo no manda nada.
- Logs con `[OK]`, `[WARN]`, `[ERROR]`, `[INFO]`. **El repo es público**: en logs sólo nombres
  y números de petición, nunca contenido de clientes.
- Correos con estilos *inline* (los clientes de correo ignoran CSS externo); color `#A75296`.
- Ramas por funcionalidad y merge a `main`, que es lo que corre en producción.
- No hay tests automatizados: validar con vista previa y revisar el resumen de ejecución.

## Problemas conocidos / pendientes

- Impuestos y auditoría, si la descarga de Redmine falla a mitad, siguen con datos parciales
  (`peticiones_abiertas(estricto=False)`); laboral, R2 y consultorías cortan con error.
- Resumen R2 de auditoría en pausa (`SECTORES_ACTIVOS`).
- Correos, IDs y nombres de personas están en el código de un repo público.
- `REDIRIGIDOS_A_ADMIN` no aplica en auditoría: Previotto, Gisela recibe ese reporte en su casilla.
- Hay peticiones sin fecha de referencia cargada (al 15/10/2026: 96 de impuestos y 23 laborales):
  nunca generan aviso hasta que se complete el campo en Redmine.
