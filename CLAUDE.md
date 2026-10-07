# AgenteIA — Estudio Rivarossa

Automatizaciones que leen Redmine y mandan avisos por correo. Cinco scripts (laboral todavía en desarrollo) y sus workflows de GitHub Actions:

| Script | Workflow | Qué hace | Cuándo corre |
|---|---|---|---|
| `main_impuestos.py` | `.github/workflows/reporte-vencimientos.yml` | Avisa a cada responsable sus vencimientos tributarios próximos (II BB, DREI, CM, IVA, Sicore, Ag. Recaudación) | Todos los días, con 2 disparos de respaldo |
| `main_auditoria.py` | `.github/workflows/reporte-auditoria.yml` | Reporte mensual de auditoría (bloques en orden: CyA Balance, CyA Corte, CyA Auditoria) | Día 21 de cada mes, sea hábil o no (desde oct/2026). El workflow corre todos los días (03:13 ART desde el 06/10/2026) pero el script decide internamente si corresponde enviar |
| `main_resumen_r2.py` | `.github/workflows/resumen-r2.yml` | Resumen unificado para el R2 (subgerente) de cada sector (ver sección "Resumen R2") | Todos los días ~03:31 ART, con 2 disparos de respaldo |
| `main_consultorias.py` | `.github/workflows/notificacion-consultorias.yml` | Avisa por correo las consultas de clientes de consultoría nuevas o editadas (ver sección "Consultorías") | Cada 30 min, lunes a viernes 07:17 a 19:47 ART |
| `main_laboral.py` | **todavía no tiene** | Avisos del sector laboral. **EN DESARROLLO, en modo prueba, EN PAUSA** (ver sección "Sector Laboral") | Sólo se corre a mano por ahora |

**Ojo con los horarios:** GitHub atrasa los "schedule" de este repo 6 a 9 horas (medido el 06/10/2026: el disparo de 03:24 ART de impuestos sale ~10-12 ART). No adelantar ningún cron antes de las 03:00 UTC (00:00 ART): los scripts toman la fecha en UTC y mandarían el reporte del día siguiente.

**Disparo externo con cron-job.org (desde el 07/10/2026):** para no depender del atraso de GitHub, la cuenta de cron-job.org del usuario dispara por API (`workflow_dispatch`, que sale sin atraso) impuestos a las 06:30 ART, el resumen R2 a las 06:35 ART auditoría el día 21 a las 06:40 ART y consultorías cada 30 min (`17,47 7-19 * * 1-5`) (zona `America/Argentina/Buenos_Aires` en cada job). Usan un token fine-grained de GitHub (`cron-job-agenteia`, sólo "Actions: read/write" sobre este repo, sin vencimiento). Los crons de GitHub quedan como respaldo: `ya_hubo_envio_exitoso_hoy()` (en impuestos, R2 y, desde el 07/10/2026, auditoría, donde sólo se consulta el día de envío) cuenta cualquier corrida exitosa del día, así que no se duplica nada. Si cron-job.org falla, avisa por mail al usuario.

## Resumen R2 (`main_resumen_r2.py`) — desde el 06/10/2026

Un único correo por sector para su R2, que unifica lo que ese día les llegó a las personas a su cargo: cada petición aparece una sola vez (fila de tabla con todo el equipo y la etapa), más las peticiones que no le llegaron a nadie por no tener responsables. Reutiliza `process_redmine_data` / `process_redmine_auditoria` importando los otros scripts (sin modificarlos).
- **Impuestos → Cintia Margaria (542): EN PRODUCCIÓN.** Sin Pogonza ni Boretto (`FUERA_DEL_EQUIPO_R2`), sin la tabla "Carga por persona" (`MOSTRAR_CARGA_POR_PERSONA`), y sin peticiones viejas (sólo lo que vence en 2 días hábiles).
- **Auditoría → Matías Depetris (717): EN PAUSA** (`SECTORES_ACTIVOS_R2 = {"impuestos"}`) hasta que el usuario lo hable con él. Saldría el 21, sin Molfino, Mauricio Fenoglio, Luisina Trinca ni Luis Borgogno.
- Prueba local: `SOLO_VISTA_PREVIA=1 FECHA_SIMULADA=2026-10-21 SECTORES_R2=impuestos,auditoria python main_resumen_r2.py` (HTML en `vista_previa_r2/`).

## Consultorías (`main_consultorias.py`) — EN PRODUCCIÓN desde el 06/10/2026

Los clientes de consultoría (proyectos `federicocaglieris` y `evelynsaires` en `PROYECTOS_CONSULTORIA`, con sus subproyectos) cargan consultas en Redmine y Redmine no avisa. Cada corrida revisa lo creado/editado desde el inicio de la última corrida exitosa del workflow (vía API de GitHub), así que no se pierde nada aunque GitHub saltee disparos.
- Destinatarios: el asignado (ej. Patricio Pogonza); si está asignada al grupo `CONSULTORIA X` o sin asignar, los miembros del grupo (Borgogno, Boretto, Mihlager).
- Nunca avisa a alguien de su propia acción ni a casillas que no sean `@rivarossa.com` (los clientes nunca reciben nada).
- El repo es público: el log sólo imprime números de petición, nunca el contenido de las consultas.
- Disparo: cron-job.org cada 30 min en horario laboral (ver "Disparo externo"), con el schedule de GitHub de respaldo. `concurrency` en el workflow evita que dos corridas se superpongan y dupliquen avisos.
- Primera corrida en GitHub: 07/10/2026 (manual), avisó #192724 y #192723 a Borgogno, Boretto y Mihlager.
- Prueba local: `SOLO_VISTA_PREVIA=1 VENTANA_DESDE=2026-10-01T00:00:00Z python main_consultorias.py`.

`feriados.py` tiene el calendario de feriados/no laborables usado para calcular días hábiles en `main_impuestos.py` (auditoría ya no lo usa: manda siempre el 21).

Ambos scripts comparten el mismo patrón: descargan de Redmine → arman el HTML por persona → mandan por SMTP (Gmail) → siempre mandan un correo de resumen de ejecución a `francoalbrecht@rivarossa.com` (éxito o error), para que un fallo nunca sea silencioso.

## Autenticación de Gmail: OAuth2 (desde el 15/09/2026)

**Ya no se usa contraseña de aplicación.** El 15/09 Google empezó a bloquear el login SMTP con contraseña desde las IPs rotativas de GitHub Actions (error `534 5.7.9 WebLoginRequired`), cortando el envío diario sin aviso hasta que alguien lo notaba a mano.

Se migró a **OAuth2 (XOAUTH2)**:
- Proyecto de Google Cloud dedicado: `agente-impuestos-rivarossa` (dueño: `agenterivarossa@gmail.com`), **publicado en modo producción** (no "Prueba") para que el refresh token no venza cada 7 días.
- Scope: `https://mail.google.com/`.
- Homepage / Política de Privacidad de la app OAuth: página mínima publicada aparte (no es un servicio productivo, solo cumple el requisito de Google para publicar la app a producción).
- Credenciales en GitHub Secrets: `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN` (usadas por ambos workflows). El secret viejo `SMTP_PASSWORD` **ya no existe**, se borró el 15/09 tras confirmar que ningún script lo referenciaba.
- Funciones clave en ambos scripts: `obtener_access_token_oauth()` (cambia el refresh token por un access token de ~1h) y `autenticar_smtp_oauth()` (hace el `AUTH XOAUTH2` sobre la conexión SMTP ya abierta).
- La app queda con el cartel de "Google no verificó esta app" — es esperable, es de un solo desarrollador/usuario, no se pidió verificación formal.

**Riesgo residual:** aunque OAuth2 es mucho más robusto que la contraseña, Google podría en teoría volver a pedir alguna verificación. Si el correo de resumen avisa un error de auth, revisar `https://myaccount.google.com/notifications` en `agenterivarossa@gmail.com`.

## Resiliencia del envío diario (impuestos)

- `reporte-vencimientos.yml` tiene el cron original (~03:24 ART) más **dos disparos de respaldo** (~07:11 y ~10:37 ART), porque GitHub retrasa "schedule" 3-7hs en este repo bajo carga.
- `main_impuestos.py` chequea al arrancar (`ya_hubo_envio_exitoso_hoy()`, vía API de GitHub Actions) si ya hubo una corrida exitosa hoy; si la hay, no manda nada — así los disparos de respaldo no duplican avisos.

## Sector Laboral (`main_laboral.py`) — EN DESARROLLO

Tercer script, **independiente** de impuestos y auditoría (no importa nada de ellos; comparte sólo `feriados.py`). Se arma de a poco. Si el usuario dice "seguimos con lo de laboral", retomar desde acá.

**Regla de oro mientras esté en desarrollo:** `REDIRIGIR_A_ADMIN_LABORAL = True` → todos los correos a `francoalbrecht@rivarossa.com`. **Nunca mandar nada al sector laboral** hasta que el usuario lo pida explícitamente. Cada correo en modo prueba lleva un cartel amarillo con el destinatario real.

**Pruebas locales:**
- `SOLO_VISTA_PREVIA=1 FECHA_SIMULADA=2026-10-08 python main_laboral.py` → no manda nada, guarda los HTML en `vista_previa_laboral/` (en `.gitignore`) e imprime el resumen.
- Sin `SOLO_VISTA_PREVIA` manda de verdad (todo a admin por el modo prueba). `FECHA_SIMULADA` sirve también ahí.

**Destinatarios:** el 931 usa `Liquidador 1`, `Control 1`, `Soporte 1`; los trackers nuevos (814, Provisión vacaciones, Asiento, SICORE) usan `Liquidador` y `Control` (sin "1"). Se avisa a todos los que figuren, un correo por persona con todos sus vencimientos. Las peticiones sin ningún responsable **se ignoran en silencio** (pedido del sector: el agente no las debe tomar).

**Reglas** (Excel del usuario 28/09/2026 + respuestas de Diego, responsable de laboral, 07/10/2026):

| Petición | Tracker en Redmine | Cuándo llega el aviso | Estado |
|---|---|---|---|
| Leyes sociales | `Laboral - Formulario 931` con asunto "Leyes Sociales" | 2 días hábiles antes del `Vencimiento DD. JJ.` (se carga ~23/24 del mes anterior) | ✅ Hecho |
| 814 | `Laboral - 814` | Día hábil siguiente al `Vencimiento DD. JJ.` del 931 (Leyes Sociales) del **mismo proyecto y período**, buscado también entre 931 cerrados (`fetch_vencimientos_931`) | ✅ Hecho (al 07/10 los 2 abiertos no tienen responsables) |
| Provisión vacaciones | `Laboral - Provision vacaciones` | 3 días hábiles antes del campo `Fecha límite` | ✅ Hecho (al 07/10 las 29 están cerradas y sin fecha: Diego las edita) |
| Domésticas | `Laboral - Doméstica` | **Sacado** (Diego: "SACARLO"; el usuario lo interpretó como sacarlas del todo) | ❌ No se avisa |
| SICORE | `Laboral - SICORE` | 4° día hábil del mes | ⏸ En pausa: el sector les carga responsables, las abre y avisa |
| SIRADIG | `Laboral - SIRADIG` | El 20 de cada mes o el día hábil siguiente | ⏸ En pausa (ídem) |
| Asiento de sueldos | `Laboral - Asiento sueldo` | Wiltel: 2° día hábil del mes. Cortassa: 2 días hábiles antes de `Fecha Presentación:` (Diego ya la cargó) | ⏸ En pausa (ídem) |

**Pendientes:**
1. **Workflow de GitHub Actions para laboral:** no existe. Crearlo cuando el reporte esté validado. Replicar disparos de respaldo + `ya_hubo_envio_exitoso_hoy()` de impuestos (y sumar un cronjob en cron-job.org).
2. Cuando el sector avise que abrió SICORE / SIRADIG / Asiento, programarlos (campos `Período`, `Liquidador`, `Control`, `Fecha Presentación:`).

**Vista previa (07/10/2026):** 08/10 y 09/10 → 8 correos cada día (el sector asignó más 931 desde el 28/09; la versión anterior del script da lo mismo).

## Pendientes / cosas a tener en cuenta

- **Gorreta, Valentina**: su email en Redmine (`valentinagorreta@rivarossa.com`) rebota (dirección inexistente). Mientras no se confirme/corrija la casilla real, su reporte se redirige a `francoalbrecht@rivarossa.com` (mismo mecanismo que ya existía para Previotto, Gisela — ver diccionario `EXCEPCIONES_DESTINO` en `main_impuestos.py`). Login de Redmine de Valentina es `vgorreta`, así que `vgorreta@rivarossa.com` es la dirección más probable si alguien la confirma.
- **Migración a Mailgun**: se evaluó y se llegó a crear una cuenta/dominio (`mg.rivarossa.com`) en Mailgun, pero **no se completó** por falta de acceso al DNS de `rivarossa.com` (lo administra un tercero, hosting BAEHOST). La cuenta de Mailgun queda creada y sin usar, por si en algún momento se consigue ese acceso.
- **Auditoría en producción desde el 24/09/2026** (`REDIRIGIR_A_ADMIN_AUDITORIA = False`): ese día hubo un envío puntual fuera de corte (`FECHAS_ENVIO_EXTRA`); a partir de ahí sale todos los 21, aunque caiga fin de semana o feriado. El envío del 21/09 fue todavía en modo prueba (todo a admin).
- **Ventana del reporte de auditoría (desde el 25/09/2026):** trae lo que vence desde el día del envío hasta el **último día del mes siguiente** (pedido del sector auditoría: antes llegaba sólo hasta el 21 del mes siguiente y los cierres de fin de mes quedaban afuera). Lo que vence entre el 21 y fin de mes sale en dos reportes seguidos, a propósito.
- **Auditoría no filtra por proyecto:** entran todas las peticiones CyA Balance/Corte/Auditoria en Pendiente, estén o no en el subproyecto de Auditoría. Las que están mal ubicadas se listan en el correo de resumen (`detectar_fuera_de_proyecto_auditoria`) para moverlas en Redmine.
- **Envío manual de auditoría fuera del 21:** correr localmente `FORZAR_ENVIO_AUDITORIA=1 python main_auditoria.py` (usa el `.env`). Manda a todos, y la corrida automática del día no duplica porque no fuerza. Se usó el 25/09/2026 para mandar el reporte completo con la ventana nueva. (Ahora que el `gh` local tiene scope `workflow`, se podría agregar como opción del workflow.)

## Historial reciente (sesión del 15/09/2026)

1. El envío diario de impuestos falló por el bloqueo de Google (contraseña de aplicación + IP de GitHub Actions).
2. Se destrabó manualmente (login por navegador) y se reenvió el reporte del día.
3. Se corrigió el email de Gorreta, Valentina (excepción a admin) y se agregó la red de resiliencia (disparos de respaldo + chequeo de idempotencia).
4. Se migró la autenticación SMTP de ambos scripts (impuestos y auditoría) de contraseña de aplicación a OAuth2, para no depender más de ese tipo de bloqueo.
5. Se evaluó y descartó (por ahora) la migración a Mailgun.
6. Se borró el secret `SMTP_PASSWORD`, ya sin uso en el repo.

Último trabajo (06/10/2026): auditoría pasa a dispararse 03:13 ART; se agregan el resumen R2 (impuestos en producción, auditoría en pausa) y la notificación de consultorías (en producción). Laboral queda en pausa.
